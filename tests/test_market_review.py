from __future__ import annotations

from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pytest

from src import scheduler as scheduler_mod
from src import trading_calendar
from src.database import db as db_module
from src.database.db import get_db_session, init_db
from src.database.models import FinanceNews, TradeSignal
from src.services import market_context as context_mod
from src.services import market_review as review_mod
from src.services import trade_advisor as advisor_mod
from src.services.market_context import MarketFacts, build_market_facts
from src.services.market_review import MarketReviewService, render_markdown
from src.services.trade_advisor import TradeAdvisor

TODAY = date.today().strftime("%Y-%m-%d")


def _reset_db_engine():
    if db_module._engine:
        db_module._engine.dispose()
    db_module._engine = None
    db_module._SessionFactory = None


@pytest.fixture
def config(tmp_path):
    path = str(tmp_path / "review.db")
    _reset_db_engine()
    init_db(path)
    yield {"database": {"sqlite_path": path}, "llm": {}}
    _reset_db_engine()


def _regime(regime: str = "防守", score: float = 38, trade_date: str = "2026-09-24"):
    return SimpleNamespace(regime=regime, score=score, trade_date=trade_date, reasons=["跌多涨少"],
                           summary=lambda: f"大盘环境：{regime}（{score:.0f}分）")


class FakeLLM:
    def __init__(self, reply=None, error: Exception | None = None):
        self.reply, self.error, self.calls = reply or {}, error, []

    def chat_json(self, user_message, system_message="", **kwargs):
        self.calls.append(user_message)
        if self.error:
            raise self.error
        return self.reply


REVIEW_REPLY = {
    "headline": "缩量调整，高位股分歧", "trend": "三大指数同步回落", "emotion": "跌多涨少，连板高度 5",
    "main_lines": "“华”字辈仍是最强题材", "stance": "进攻", "position": "8 成",
    "focus": ["海峡两岸：10 家涨停"], "avoid": ["高位连板股"], "watch_points": ["新华文轩能否继续晋级"],
}


# ---------- 市场上下文 ----------

def test_facts_text():
    facts = MarketFacts(
        overview={"sh_index": "3250.12", "sh_change_pct": -0.85, "up_count": 1200, "down_count": 4000,
                  "limit_up_count": 40, "limit_down_count": 12, "total_amount_yi": 15320, "northbound_net_yi": -20.5,
                  "top_sectors": [{"name": "文化传媒", "pct": 2.1}]},
        regime=_regime(), concept_lines=["“华”字辈 持续发酵"], cooling=["算力"], news=["央行降准 0.5 个百分点"],
    )
    text = facts.text()
    assert "上证 3250.12（-0.85%）" in text
    assert "上涨 1200 / 下跌 4000；涨停 40 / 跌停 12" in text
    assert "两市成交额：15,320 亿；北向资金 -20.5 亿" in text
    assert "大盘环境：防守（38分）" in text and "环境依据：跌多涨少" in text
    assert "题材主线：\n- “华”字辈 持续发酵" in text and "降温/退潮：算力" in text
    assert "重要快讯：\n- 央行降准 0.5 个百分点" in text
    assert MarketFacts().text() == "暂无市场数据"


def test_build_market_facts_reads_recent_important_news(config):
    now = datetime.now()
    with get_db_session(config["database"]["sqlite_path"]) as session:
        session.add(FinanceNews(source="cailianshe", title="央行降准", category="red", collected_at=now))
        session.add(FinanceNews(source="cailianshe", title="旧闻", category="red", collected_at=now - timedelta(days=2)))
        session.add(FinanceNews(source="cailianshe", title="普通快讯", category="normal", collected_at=now))
        session.add(FinanceNews(source="ths", title="其他来源", category="red", collected_at=now))

    facts = build_market_facts(config, overview={})
    assert facts.news == ["央行降准"]
    assert facts.regime is None  # 没有行情数据时不给大盘环境
    assert build_market_facts(config, overview={}, news=False).news == []


# ---------- LLM 大盘复盘 ----------

def test_review_capped_by_regime_and_saved(config, monkeypatch):
    monkeypatch.setattr(review_mod, "build_market_facts", lambda cfg, overview=None: MarketFacts(overview={"up_count": 1}, regime=_regime()))
    llm = FakeLLM(REVIEW_REPLY)
    service = MarketReviewService(config, llm=llm)

    result = service.generate()
    assert result["trade_date"] == "2026-09-24"
    assert result["stance"] == "防守" and result["position"] == "0~3 成"  # 不能比量化环境更激进
    assert result["guardrails"] == ["量化大盘环境为「防守」（38分），姿态由「进攻」下调为「防守」"]
    assert "复盘日期：2026-09-24" in llm.calls[0] and "大盘环境：防守（38分）" in llm.calls[0]
    assert "🟢 次日姿态：**防守**，建议仓位 0~3 成" in result["markdown"]
    assert "**次日关注**\n- 海峡两岸：10 家涨停" in result["markdown"]

    assert service.get("2026-09-24")["stance"] == "防守"
    assert service.generate()["created_at"] == result["created_at"] and len(llm.calls) == 1  # 收盘后的复盘直接复用
    service.generate(force=True)
    assert len(llm.calls) == 2
    assert service.get()["trade_date"] == "2026-09-24"  # 重新生成会覆盖，不会多一条


def test_review_keeps_stance_within_regime(config, monkeypatch):
    monkeypatch.setattr(review_mod, "build_market_facts", lambda cfg, overview=None: MarketFacts(regime=_regime("进攻", 75)))
    result = MarketReviewService(config, llm=FakeLLM({**REVIEW_REPLY, "stance": "均衡", "position": "5 成"})).generate()
    assert (result["stance"], result["position"], result["guardrails"]) == ("均衡", "5 成", [])

    monkeypatch.setattr(review_mod, "build_market_facts", lambda cfg, overview=None: MarketFacts(regime=_regime("冰点", 15, "2026-09-25")))
    result = MarketReviewService(config, llm=FakeLLM({**REVIEW_REPLY, "stance": "乱写"})).generate()
    assert result["stance"] == "防守"  # 无法识别的姿态按均衡处理，再被冰点下调


def test_review_errors(config, monkeypatch):
    monkeypatch.setattr(review_mod, "build_market_facts", lambda cfg, overview=None: MarketFacts())
    assert "没有足够的市场数据" in MarketReviewService(config, llm=FakeLLM(REVIEW_REPLY)).generate()["error"]

    monkeypatch.setattr(review_mod, "build_market_facts", lambda cfg, overview=None: MarketFacts(regime=_regime()))
    result = MarketReviewService(config, llm=FakeLLM(error=RuntimeError("所有LLM模型均调用失败"))).generate()
    assert "AI 未返回有效结果" in result["error"]
    assert render_markdown(result).startswith("**复盘失败**")
    assert MarketReviewService(config).get() is None


# ---------- AI 研判接入 ----------

def test_advisor_verdicts_and_reference_signals(config, monkeypatch):
    monkeypatch.setattr(advisor_mod, "LLMClient", lambda cfg: FakeLLM({
        "market_comment": "缩量防守",
        "stocks": [
            {"code": "600001", "verdict": "回避", "confidence": 3, "reason": "高位分歧"},
            {"code": "600002", "verdict": "买入", "confidence": 9, "reason": "龙头"},
            {"code": "600003", "verdict": "买入", "confidence": 8, "reason": "补涨"},
        ],
    }))
    with get_db_session(config["database"]["sqlite_path"]) as session:
        session.add(TradeSignal(code="600001", name="评分股", signal_date=TODAY, signal_type="buy"))
        session.add(TradeSignal(code="600002", name="预测股", signal_date=TODAY, signal_type="premarket", ai_verdict="观望"))

    stock = {"score": 80.0, "recommendation": "buy", "continuous_days": 1, "sector": "", "reason": "首板", "first_time": "",
             "open_count": 0, "change_pct": 10, "turnover": 5, "circ_mv_yi": 50.0, "sentiment_score": 60, "capital_score": 60}
    advisor = TradeAdvisor(config)
    monkeypatch.setattr(advisor, "_build_stock_context", lambda d: [
        {**stock, "rank": i, "code": code, "name": code} for i, code in enumerate(["600001", "600002", "600003"], 1)
    ])
    monkeypatch.setattr(advisor, "_get_news_context", lambda: "")
    monkeypatch.setattr(advisor, "_get_market_context", lambda: "")
    monkeypatch.setattr(advisor, "_get_us_market_context", lambda: "")

    assert len(advisor.advise_top_stocks(TODAY)) == 3
    with get_db_session(config["database"]["sqlite_path"]) as session:
        rows = {s.code: (s.signal_type, s.ai_verdict) for s in session.query(TradeSignal).all()}
    assert rows == {
        "600001": ("buy", "回避"),          # 观望/回避的信号不会生成订单
        "600002": ("premarket", "观望"),    # AI 涨停预测的信号保留自己的研判
        "600003": ("hold", "买入"),         # 评分未入选的股票只作参考，不会变成买入信号
    }


def test_advisor_market_context_uses_market_facts(config, monkeypatch):
    monkeypatch.setattr(advisor_mod, "LLMClient", lambda cfg: FakeLLM())
    calls = []
    monkeypatch.setattr(context_mod, "build_market_facts", lambda cfg, news=True: calls.append(news) or MarketFacts(
        overview={"total_amount_yi": 15000, "market_emotion": "冰点"}, regime=_regime()))
    text = TradeAdvisor(config)._get_market_context()
    assert calls == [False]  # 快讯由 _get_news_context 提供
    assert "大盘环境：防守（38分）" in text and "低于 2 万亿" in text and "市场情绪：冰点" in text


def test_scheduler_runs_advisor_between_signals_and_orders(monkeypatch):
    from src.strategy import scorer as scorer_mod
    from src.trading import execution_service as execution_mod

    steps = []
    monkeypatch.setattr(trading_calendar, "load", lambda db_path, refresh=True: True)
    monkeypatch.setattr(trading_calendar, "is_trade_day", lambda d=None: True)
    monkeypatch.setattr(scorer_mod, "CompositeScorer", lambda cfg: SimpleNamespace(generate_signals=lambda: steps.append("signals")))
    monkeypatch.setattr(advisor_mod, "TradeAdvisor", lambda cfg: SimpleNamespace(advise_top_stocks=lambda d: steps.append("advise")))
    monkeypatch.setattr(execution_mod, "ExecutionService", lambda cfg: SimpleNamespace(
        enabled=True, execute_signals=lambda signal_date=None: steps.append("orders")))

    scheduler_mod._run_signal_generation({})
    assert steps == ["signals", "advise", "orders"]
    steps.clear()
    scheduler_mod._run_signal_generation({"strategy": {"ai_advisor_enabled": False}})
    assert steps == ["signals", "orders"]
