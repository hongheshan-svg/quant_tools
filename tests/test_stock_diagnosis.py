from __future__ import annotations

from datetime import datetime

import pandas as pd
import pytest

from src.analyzers import market_regime as regime_mod
from src.analyzers.market_regime import MarketRegime
from src.collectors import daily_history as daily_history_mod
from src.collectors import fundamentals as fundamentals_mod
from src.collectors import stock_news as stock_news_mod
from src.database import db as db_module
from src.database.db import get_db_session, init_db
from src.database.models import DragonTigerBoard, FinanceNews, LimitUpStock, StockDaily, StockDiagnosis, StockFundFlow
from src.services.stock_diagnosis import StockDiagnosisService, render_markdown

DAYS = [d.strftime("%Y-%m-%d") for d in pd.bdate_range(end="2026-09-25", periods=25)]
CHIP = {"date": "2026-09-25", "profit_ratio": 85.0, "avg_cost": 21.3, "cost_90_low": 18.6, "cost_90_high": 23.9,
        "concentration_90": 12.5, "source": "东方财富"}
EARNINGS = {"type": "业绩预告", "period": "20260930", "change_type": "预增", "summary": "预计1-9月净利润增长50%",
            "change_pct": 50.0, "notice_date": "2026-09-20"}
STOCK_NEWS = {
    "news": [{"kind": "新闻", "title": "比亚迪9月销量创新高", "date": "2026-09-24 18:00", "source": "证券时报网", "url": "", "risk": "", "severe": False}],
    "notices": [{"kind": "公告", "title": "比亚迪:关于股票交易异常波动的公告", "date": "2026-09-23", "source": "风险提示",
                 "url": "", "risk": "异常波动", "severe": False}],
}


@pytest.fixture(autouse=True)
def offline_fundamentals(monkeypatch):
    """筹码、业绩、个股新闻公告默认用固定数据，也不补齐日线，测试不联网。"""
    monkeypatch.setattr(fundamentals_mod, "fetch_chip_summary", lambda code, db_path: dict(CHIP))
    monkeypatch.setattr(fundamentals_mod.EarningsCache, "get", classmethod(lambda cls, code: dict(EARNINGS)))
    monkeypatch.setattr(stock_news_mod, "get_stock_news", lambda code, refresh=False, now=None: {k: list(v) for k, v in STOCK_NEWS.items()})
    monkeypatch.setattr(daily_history_mod, "ensure_daily_history", lambda code, db_path, name="", min_bars=60, now=None: 0)


class _FakeLLM:
    def __init__(self, reply: dict):
        self.reply = reply
        self.calls = []

    def chat_json(self, user_message: str, system_message: str = "", **kwargs) -> dict:
        self.calls.append(user_message)
        return self.reply


def _reset_db_engine():
    if db_module._engine:
        db_module._engine.dispose()
    db_module._engine = None
    db_module._SessionFactory = None


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "diag.db")
    _reset_db_engine()
    init_db(path)
    with get_db_session(path) as session:
        for i, d in enumerate(DAYS):
            close = 14 + i * 0.4
            session.add(StockDaily(code="sz002594", name="比亚迪", trade_date=d, open=close - 0.2, close=close,
                                   change_pct=2.0, volume=1000 + i, amount=5e9, turnover=3.2, circ_mv=6e11))
        session.add(StockFundFlow(code="002594", name="比亚迪", trade_date="2026-09-25", net_inflow=3e8, net_ratio=6.0,
                                  amount=5e9, source="同花顺"))
        session.add(LimitUpStock(code="002594", name="比亚迪", trade_date="2026-09-25", continuous_days=2, sector="汽车整车",
                                 first_limit_time="09:35", open_count=0, seal_amount=3e8, reason="固态电池"))
        session.add(FinanceNews(source="cailianshe", title="比亚迪固态电池量产提速", collected_at=datetime.now()))
        session.add(FinanceNews(source="cailianshe", title="无关新闻", collected_at=datetime.now()))
        session.add(DragonTigerBoard(code="002594", name="比亚迪", trade_date="2026-09-25", reason="日涨幅偏离7%", net_amount=5e7))
    yield path
    _reset_db_engine()


def _service(db_path: str, reply: dict) -> tuple[StockDiagnosisService, _FakeLLM]:
    llm = _FakeLLM(reply)
    return StockDiagnosisService({"database": {"sqlite_path": db_path}, "risk": {}, "trading": {}}, llm=llm), llm


GOOD_REPLY = {
    "score": 82, "action": "强烈买入", "confidence": "高", "one_sentence": "主线龙头二板，低吸参与",
    "position_advice": {"no_position": "回踩分时均线低吸", "has_position": "持有，破位止损"},
    "battle_plan": {"buy_price": 23.8, "stop_loss": 22.5, "target_price": 99, "suggested_position": "建议仓位：3成"},
    "catalysts": ["固态电池量产"], "risks": ["高位分歧"],
    "checklist": [{"item": "主线地位", "status": "pass", "note": "汽车整车龙头"}, "bad"],
    "analysis": "二板接力，封板早且未炸板。",
}


def test_context_collects_all_sections(db_path):
    context = _service(db_path, {})[0].build_context("002594")
    text = context["text"]
    assert context["name"] == "比亚迪" and context["quote"]["close"] == pytest.approx(23.6)
    assert "股票：比亚迪(002594) 主板" in text
    assert "【行情】2026-09-25 收盘 23.6（+2.00%），成交 50.0 亿，换手 3.2%，流通市值 6000.0 亿" in text
    assert StockDiagnosisService._quote_text({"trade_date": "2026-09-25", "close": 20.35, "change_pct": 10.0}) == "2026-09-25 收盘 20.35（+10.00%）"
    assert "2板 首封09:35 炸板0次 封单3.00亿 原因:固态电池" in text
    assert "比亚迪固态电池量产提速" in text and "无关新闻" not in text
    assert "2026-09-24 [证券时报网] 比亚迪9月销量创新高" in text
    assert "【近 30 天公告】2026-09-23 比亚迪:关于股票交易异常波动的公告（风险：异常波动）" in text
    assert "日涨幅偏离7% 净买入5000万" in text
    assert "【主线地位】龙头，所属行业汽车整车" in text
    assert "【持仓】未持仓" in text
    assert "【资金流】净流入3.00亿（占成交额6.0%，同花顺，2026-09-25）" in text
    assert "【筹码】获利盘85%，平均成本21.3" in text and "【业绩】业绩预告" in text
    assert context["data_quality"] == {"score": 85, "missing": ["大盘"], "core_ok": True, "bar_count": 25}  # 测试库行情样本不足，大盘为未知


def test_diagnose_applies_guardrails_saves_and_caches(db_path):
    service, llm = _service(db_path, GOOD_REPLY)
    result = service.diagnose("sz002594")

    assert (result["code"], result["action"], result["action_label"], result["score"]) == ("002594", "buy", "买入", 82)
    # 目标价 99 超出合理范围被丢弃，买入价和止损价保留
    assert result["battle_plan"] == {"buy_price": 23.8, "stop_loss": 22.5, "target_price": None, "suggested_position": "建议仓位：3成"}
    assert result["checklist"] == [{"item": "主线地位", "status": "pass", "note": "汽车整车龙头"}]
    assert result["theme_role"]["role"] == "龙头"
    with get_db_session(db_path) as session:
        assert session.query(StockDiagnosis).count() == 1

    assert service.diagnose("002594")["cached"] is True  # 30 分钟内复用
    assert len(llm.calls) == 1
    service.diagnose("002594", force=True)
    assert len(llm.calls) == 2

    markdown = render_markdown(result)
    assert "比亚迪(002594)：买入｜评分 82" in markdown and "止损 22.50" in markdown and "✅ 主线地位" in markdown


def test_guardrails_downgrade_inconsistent_or_freeze(db_path, monkeypatch):
    weak = _service(db_path, {**GOOD_REPLY, "score": 45, "action": "买入"})[0].diagnose("002594", force=True)
    assert weak["action"] == "watch" and "评分 45 与「买入」不一致" in weak["guardrails"][0]

    no_action = _service(db_path, {"score": 25, "one_sentence": "趋势走坏"})[0].diagnose("002594", force=True)
    assert no_action["action"] == "reduce"  # 缺少 action 时按评分推断

    monkeypatch.setattr(regime_mod.MarketRegimeAnalyzer, "analyze",
                        lambda self, **kw: MarketRegime(trade_date="2026-09-25", regime="冰点", position_factor=0.0))
    frozen = _service(db_path, GOOD_REPLY)[0].diagnose("002594", force=True)
    assert frozen["action"] == "watch" and "冰点" in frozen["guardrails"][0]


def test_diagnose_errors(db_path):
    assert "没有该股票的数据" in _service(db_path, GOOD_REPLY)[0].diagnose("600000")["error"]
    failed = _service(db_path, {})[0].diagnose("002594", force=True)
    assert "AI 未返回有效结果" in failed["error"]
    assert render_markdown(failed).startswith("**诊断失败**")


def _with(db_path: str, reply: dict) -> dict:
    return _service(db_path, reply)[0].diagnose("002594", force=True)


def test_low_data_quality_blocks_buy(db_path, monkeypatch):
    monkeypatch.setattr(fundamentals_mod, "fetch_chip_summary", lambda code, db_path: None)
    monkeypatch.setattr(fundamentals_mod.EarningsCache, "get", classmethod(lambda cls, code: None))
    with get_db_session(db_path) as session:
        session.query(StockFundFlow).delete()

    result = _with(db_path, GOOD_REPLY)
    assert (result["action"], result["confidence"]) == ("watch", "低")
    assert result["data_quality"]["score"] == 55
    assert "数据完整度 55%" in result["guardrails"][0]
    assert "数据完整度 55%（缺少：资金流、筹码、大盘、业绩）" in render_markdown(result)


def test_short_history_blocks_buy(db_path):
    with get_db_session(db_path) as session:
        session.query(StockDaily).filter(StockDaily.trade_date < DAYS[-10]).delete()
    result = _with(db_path, GOOD_REPLY)
    assert result["action"] == "watch" and "日线 10 根" in result["guardrails"][0]


def test_heavy_outflow_blocks_buy(db_path):
    with get_db_session(db_path) as session:
        session.query(StockFundFlow).update({"net_inflow": -4e8, "net_ratio": -8.0})
    result = _with(db_path, GOOD_REPLY)
    assert result["action"] == "watch" and "资金净流出占成交额 8.0%" in result["guardrails"][0]


def test_stability_avoids_flipping_from_bearish_to_bullish(db_path):
    service, _ = _service(db_path, {**GOOD_REPLY, "score": 72, "action": "卖出"})
    assert service.diagnose("002594", force=True)["action"] == "sell"

    flipped = _with(db_path, {**GOOD_REPLY, "score": 80})  # 分差 8 分
    assert flipped["action"] == "watch" and "方向相反" in flipped["guardrails"][-1]

    real_change = _with(db_path, {**GOOD_REPLY, "score": 95})  # 上次是观望，不算方向反转
    assert real_change["action"] == "buy"


def test_earnings_risk_is_listed(db_path, monkeypatch):
    monkeypatch.setattr(fundamentals_mod.EarningsCache, "get", classmethod(lambda cls, code: {**EARNINGS, "change_type": "首亏"}))
    result = _with(db_path, GOOD_REPLY)
    assert "业绩：最新业绩预告为「首亏」" in result["risks"]
    assert "公告：2026-09-23 比亚迪:关于股票交易异常波动的公告" in result["risks"]


def test_severe_notice_blocks_buy(db_path, monkeypatch):
    notice = {"kind": "公告", "title": "比亚迪:关于收到中国证监会立案告知书的公告", "date": "2026-09-22", "source": "",
              "url": "", "risk": "立案", "severe": True}
    monkeypatch.setattr(stock_news_mod, "get_stock_news", lambda code, refresh=False, now=None: {"news": [], "notices": [notice]})
    result = _with(db_path, GOOD_REPLY)
    assert result["action"] == "watch"
    assert any("近 30 天公告含「立案」" in g for g in result["guardrails"])


def test_context_backfills_short_history(db_path, monkeypatch):
    calls = []
    monkeypatch.setattr(daily_history_mod, "ensure_daily_history", lambda code, db_path, name="", min_bars=60, now=None: calls.append(code) or 0)
    _service(db_path, {})[0].build_context("002594")
    assert calls == ["002594"]
