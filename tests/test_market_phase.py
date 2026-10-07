from __future__ import annotations

from datetime import date, datetime

import pandas as pd
import pytest

from src import trading_calendar
from src.analyzers.decision import BULLISH_ACTIONS
from src.collectors import daily_history as daily_history_mod
from src.collectors import fundamentals as fundamentals_mod
from src.collectors import stock_news as stock_news_mod
from src.database import db as db_module
from src.database.db import get_db_session, init_db
from src.database.models import StockDaily, StockFundFlow
from src.services import market_phase as mp
from src.services.stock_diagnosis import StockDiagnosisService, render_markdown

# 2026-09-25 周五，09-28 周一；10-01 至 10-07 国庆休市，10-08 周四恢复
TRADE_DAYS = {"2026-09-21", "2026-09-22", "2026-09-23", "2026-09-24", "2026-09-25", "2026-09-28", "2026-09-29",
              "2026-09-30", "2026-10-08", "2026-10-09"}


def _real():
    """取未被 conftest 替换的真实 current_phase（conftest 保存为 _real_current_phase）。"""
    real = getattr(mp, "_real_current_phase", None)
    assert real is not None, "conftest 应把原函数保存为 market_phase._real_current_phase"
    return real


@pytest.fixture(autouse=True)
def calendar():
    old = (set(trading_calendar._trade_days), trading_calendar._first_day, trading_calendar._last_day)
    trading_calendar._set_days(set(TRADE_DAYS))
    yield
    trading_calendar._trade_days, trading_calendar._first_day, trading_calendar._last_day = old


def _at(s: str) -> dict:
    return _real()(datetime.strptime(s, "%Y-%m-%d %H:%M"))


# ---------- 时段边界 ----------

@pytest.mark.parametrize("t,phase", [
    ("08:00", "premarket"), ("09:29", "premarket"), ("09:30", "intraday"), ("11:29", "intraday"),
    ("11:30", "lunch_break"), ("12:59", "lunch_break"), ("13:00", "intraday"), ("14:56", "intraday"),
    ("14:57", "closing_auction"), ("14:59", "closing_auction"), ("15:00", "postmarket"), ("20:00", "postmarket"),
])
def test_phase_boundaries(t, phase):
    r = _at(f"2026-09-25 {t}")
    assert r["phase"] == phase and r["label"] == mp.PHASE_LABELS[phase]
    assert r["is_trading_day"] is True
    assert r["now"] == f"2026-09-25 {t}"
    assert r["is_partial_bar"] is (phase in ("intraday", "lunch_break", "closing_auction"))


def test_labels():
    assert mp.PHASE_LABELS == {"premarket": "盘前", "intraday": "盘中", "lunch_break": "午间休市",
                               "closing_auction": "临近收盘", "postmarket": "盘后", "non_trading": "非交易日"}


def test_non_trading_weekend_and_holiday():
    for s in ("2026-09-26 10:00", "2026-10-02 10:00", "2026-10-05 16:00"):
        r = _at(s)
        assert r["phase"] == "non_trading" and r["is_trading_day"] is False and r["is_partial_bar"] is False
        assert r["minutes_to_open"] is None and r["minutes_to_close"] is None


def test_effective_daily_bar_date():
    assert _at("2026-09-28 08:30")["effective_daily_bar_date"] == "2026-09-25"   # 周一盘前 -> 上周五
    assert _at("2026-09-28 10:00")["effective_daily_bar_date"] == "2026-09-25"
    assert _at("2026-09-28 12:00")["effective_daily_bar_date"] == "2026-09-25"
    assert _at("2026-09-28 14:58")["effective_daily_bar_date"] == "2026-09-25"
    assert _at("2026-09-28 15:00")["effective_daily_bar_date"] == "2026-09-28"    # 盘后 = 今天
    assert _at("2026-09-26 11:00")["effective_daily_bar_date"] == "2026-09-25"    # 周六
    assert _at("2026-10-03 11:00")["effective_daily_bar_date"] == "2026-09-30"    # 国庆中
    assert _at("2026-10-08 08:00")["effective_daily_bar_date"] == "2026-09-30"    # 节后首日盘前


def test_prev_trade_day():
    assert mp.prev_trade_day(date(2026, 9, 28)) == date(2026, 9, 25)
    assert mp.prev_trade_day(date(2026, 9, 26)) == date(2026, 9, 25)
    assert mp.prev_trade_day(date(2026, 10, 8)) == date(2026, 9, 30)
    assert mp.prev_trade_day(date(2026, 9, 25)) == date(2026, 9, 24)


def test_minutes_to_open_and_close():
    assert _at("2026-09-25 09:00")["minutes_to_open"] == 30
    assert _at("2026-09-25 08:00")["minutes_to_open"] == 90
    assert _at("2026-09-25 09:00")["minutes_to_close"] is None
    assert _at("2026-09-25 10:00")["minutes_to_open"] is None
    assert _at("2026-09-25 09:30")["minutes_to_close"] == 240
    assert _at("2026-09-25 10:00")["minutes_to_close"] == 210
    assert _at("2026-09-25 11:30")["minutes_to_close"] == 120   # 午休不计入
    assert _at("2026-09-25 12:00")["minutes_to_close"] == 120
    assert _at("2026-09-25 13:00")["minutes_to_close"] == 120
    assert _at("2026-09-25 14:57")["minutes_to_close"] == 3
    assert _at("2026-09-25 15:00")["minutes_to_close"] is None


def test_conftest_default_is_postmarket_not_stale():
    ctx = mp.current_phase()
    assert ctx["phase"] == "postmarket" and not ctx.get("effective_daily_bar_date")


# ---------- phase_prompt_section ----------

def test_prompt_section_each_phase():
    s = mp.phase_prompt_section(_at("2026-09-28 08:30"), "2026-09-25")
    assert s.startswith("【市场阶段】") and "盘前" in s and "2026-09-28 08:30" in s and "2026-09-25" in s
    assert "尚未开盘，不得描述今日走势已经发生" in s

    s = mp.phase_prompt_section(_at("2026-09-28 10:00"), "2026-09-28")
    assert "盘中" in s and "不是盘后复盘" in s and "今天的日线尚未走完" in s
    s = mp.phase_prompt_section(_at("2026-09-28 10:00"), "2026-09-25")
    assert "不是盘后复盘" in s and "今天的日线尚未走完" not in s

    s = mp.phase_prompt_section(_at("2026-09-28 12:00"), "2026-09-28")
    assert "午间休市" in s and "下午开盘后再确认" in s
    s = mp.phase_prompt_section(_at("2026-09-28 14:58"), "2026-09-28")
    assert "临近收盘" in s and "收盘前风控" in s and "隔夜" in s

    s = mp.phase_prompt_section(_at("2026-09-28 16:00"), "2026-09-28")
    assert "盘后" in s and "可以按完整交易日复盘" in s

    s = mp.phase_prompt_section(_at("2026-09-26 10:00"), "2026-09-25")
    assert "非交易日" in s and "今天不是交易日" in s and "不得编造今日走势" in s and "2026-09-25" in s


def test_prompt_section_single_line_and_stale():
    s = mp.phase_prompt_section(_at("2026-09-28 16:00"), "2026-09-28")
    assert "\n" not in s and "数据可能过时" not in s
    s = mp.phase_prompt_section(_at("2026-09-28 16:00"), "2026-09-24")
    assert "行情停留在 2026-09-24" in s and "2026-09-28" in s and "数据可能过时" in s
    s = mp.phase_prompt_section({**_at("2026-09-28 16:00"), "effective_daily_bar_date": None}, "2026-09-24")
    assert "数据可能过时" not in s


# ---------- phase_guardrails ----------

def _guard(now, action="buy", conf="高", pd_=None, quote="2026-09-25"):
    ctx = _at(now)
    return mp.phase_guardrails(action, conf, pd_, ctx, quote)


KEYS = {"phase", "phase_label", "trading_window", "immediate_action", "watch_conditions", "next_check_time", "data_limitations"}


def test_premarket_immediate_buy_rewritten():
    action, conf, pd_, notes = _guard("2026-09-28 08:30", pd_={"immediate_action": "立即买入该股", "watch_conditions": ["a"]})
    assert pd_["immediate_action"] == "等待开盘后确认承接，不追价"
    assert conf == "中" and action == "buy"
    assert any("当前为盘前，不支持立即买卖，已改为开盘后确认" in n for n in notes)
    assert set(pd_) == KEYS and pd_["phase"] == "premarket" and pd_["phase_label"] == "盘前"


@pytest.mark.parametrize("word", ["立即买入", "马上买入", "立即加仓", "马上加仓", "立即卖出", "马上卖出", "立即减仓", "马上减仓"])
def test_premarket_all_immediate_words(word):
    _, conf, pd_, notes = _guard("2026-09-28 08:30", pd_={"immediate_action": word})
    assert pd_["immediate_action"] == "等待开盘后确认承接，不追价" and conf == "中" and notes


@pytest.mark.parametrize("text", ["暂不立即买入", "不建议立即买入", "不要马上卖出", "无需立即加仓", "避免立即减仓", "不能立即买入", "不宜马上买入", "勿立即买入", "不立即买入"])
def test_negated_immediate_not_rewritten(text):
    _, conf, pd_, notes = _guard("2026-09-28 08:30", pd_={"immediate_action": text})
    assert pd_["immediate_action"] == text and conf == "高" and not notes


def test_non_trading_rewrite():
    _, conf, pd_, notes = _guard("2026-09-26 10:00", pd_={"immediate_action": "马上卖出"}, quote="2026-09-25")
    assert pd_["immediate_action"] == "非交易日，下一交易日开盘后再按计划执行"
    assert conf == "中" and any("当前为非交易日，不支持立即买卖，已改为开盘后确认" in n for n in notes)


def test_premarket_low_conf_not_raised():
    _, conf, _, _ = _guard("2026-09-28 08:30", conf="低", pd_={"immediate_action": "立即买入"})
    assert conf == "低"


def test_intraday_allows_immediate_action_and_adds_partial_bar_limit():
    action, conf, pd_, notes = _guard("2026-09-28 10:00", pd_={"immediate_action": "立即买入"}, quote="2026-09-28")
    assert pd_["immediate_action"] == "立即买入" and conf == "高" and action == "buy"
    assert "今日 K 线尚未走完，涨跌幅、成交额为盘中数据" in pd_["data_limitations"]
    for t in ("12:00", "14:58"):
        pd2 = _guard(f"2026-09-28 {t}", pd_={}, quote="2026-09-28")[2]
        assert "今日 K 线尚未走完，涨跌幅、成交额为盘中数据" in pd2["data_limitations"]


def test_intraday_quote_not_today_no_partial_limit():
    pd_ = _guard("2026-09-28 10:00", pd_={}, quote="2026-09-25")[2]
    assert not any("尚未走完" in x for x in pd_["data_limitations"])


def test_postmarket_no_changes():
    action, conf, pd_, notes = _guard("2026-09-28 16:00", pd_={"immediate_action": "立即买入"}, quote="2026-09-28")
    assert (action, conf, notes) == ("buy", "高", []) and pd_["immediate_action"] == "立即买入"


def test_stale_quote_downgrades():
    action, conf, pd_, notes = _guard("2026-09-28 16:00", pd_={}, quote="2026-09-24")
    assert action == "watch" and conf == "中"
    assert "行情停留在 2026-09-24（最近完整交易日 2026-09-28）" in pd_["data_limitations"]
    assert any("行情数据停留在 2026-09-24，未满足最近完整交易日 2026-09-28 的时效要求，无法确认买点，降级为观望" in n for n in notes)
    assert _guard("2026-09-28 16:00", action="add", pd_={}, quote="2026-09-24")[0] == "watch"


def test_stale_non_bullish_keeps_action():
    action, conf, pd_, notes = _guard("2026-09-28 16:00", action="sell", pd_={}, quote="2026-09-24")
    assert action == "sell" and conf == "中"
    assert any("行情停留在" in x for x in pd_["data_limitations"])
    assert not any("降级为观望" in n for n in notes)


def test_bullish_actions_constant_used():
    for a in BULLISH_ACTIONS:
        assert _guard("2026-09-28 16:00", action=a, pd_={}, quote="2026-09-20")[0] == "watch"


def test_stale_skipped_when_effective_date_empty():
    for eff in (None, ""):
        ctx = {**_at("2026-09-28 16:00"), "effective_daily_bar_date": eff}
        action, conf, pd_, notes = mp.phase_guardrails("buy", "高", {}, ctx, "2020-01-01")
        assert (action, conf, notes) == ("buy", "高", []) and pd_["data_limitations"] == []


def test_premarket_rejects_today_bar_but_accepts_last_close():
    assert _guard("2026-09-28 08:30", pd_={}, quote="2026-09-25")[3] == []
    result = _guard("2026-09-28 08:30", pd_={}, quote="2026-09-28")
    assert result[0] == "watch" and result[3]
    assert "incomplete_daily_bar" in result[2]["data_limitations"]


@pytest.mark.parametrize("bad", [None, "文字", 5, [], ["x"]])
def test_normalize_non_dict(bad):
    _, _, pd_, _ = _guard("2026-09-28 16:00", pd_=bad, quote="2026-09-28")
    assert set(pd_) == KEYS
    assert pd_["trading_window"] == pd_["immediate_action"] == pd_["next_check_time"] == ""
    assert pd_["watch_conditions"] == [] and pd_["data_limitations"] == []


def test_normalize_types_and_truncation():
    raw = {"trading_window": "窗" * 200, "immediate_action": 123, "watch_conditions": ["c" * 200] + [str(i) for i in range(9)],
           "next_check_time": ["x"], "data_limitations": "字符串不是列表"}
    pd_ = _guard("2026-09-28 16:00", pd_=raw, quote="2026-09-28")[2]
    assert pd_["trading_window"] == "窗" * 120
    assert isinstance(pd_["immediate_action"], str) and isinstance(pd_["next_check_time"], str)
    assert len(pd_["watch_conditions"]) == 5 and pd_["watch_conditions"][0] == "c" * 120
    assert isinstance(pd_["data_limitations"], list)


def test_data_limitations_capped_at_five():
    raw = {"data_limitations": [f"限制{i}" for i in range(8)]}
    pd_ = _guard("2026-09-28 10:00", pd_=raw, quote="2026-09-28")[2]
    assert len(pd_["data_limitations"]) <= 5 or "今日 K 线尚未走完，涨跌幅、成交额为盘中数据" in pd_["data_limitations"]


# ---------- 诊断集成 ----------

DAYS = [d.strftime("%Y-%m-%d") for d in pd.bdate_range(end="2026-09-25", periods=25)]
EARNINGS = {"type": "业绩预告", "period": "20260930", "change_type": "预增", "summary": "预计净利润增长50%",
            "change_pct": 50.0, "notice_date": "2026-09-20"}
NEWS = {"news": [{"kind": "新闻", "title": "比亚迪销量创新高", "date": "2026-09-24 18:00", "source": "证券时报网", "url": "", "risk": "", "severe": False}],
        "notices": []}
CHIP = {"date": "2026-09-25", "profit_ratio": 85.0, "avg_cost": 21.3, "cost_90_low": 18.6, "cost_90_high": 23.9,
        "concentration_90": 12.5, "source": "东方财富"}


@pytest.fixture
def offline(monkeypatch):
    monkeypatch.setattr(fundamentals_mod, "fetch_chip_summary", lambda code, db_path: dict(CHIP))
    monkeypatch.setattr(fundamentals_mod.EarningsCache, "get", classmethod(lambda cls, code: dict(EARNINGS)))
    monkeypatch.setattr(stock_news_mod, "get_stock_news", lambda code, refresh=False, now=None: {k: list(v) for k, v in NEWS.items()})
    monkeypatch.setattr(daily_history_mod, "ensure_daily_history", lambda code, db_path, name="", min_bars=60, now=None: 0)


class _LLM:
    def __init__(self, reply):
        self.reply, self.calls, self.systems = reply, [], []

    def chat_json(self, user_message, system_message="", **kw):
        self.calls.append(user_message)
        self.systems.append(system_message)
        return self.reply


@pytest.fixture
def db_path(tmp_path, offline):
    path = str(tmp_path / "phase.db")
    if db_module._engine:
        db_module._engine.dispose()
    db_module._engine = None
    db_module._SessionFactory = None
    init_db(path)
    with get_db_session(path) as session:
        for i, d in enumerate(DAYS):
            c = 14 + i * 0.4
            session.add(StockDaily(code="sz002594", name="比亚迪", trade_date=d, open=c - 0.2, close=c, change_pct=2.0,
                                   volume=1000 + i, amount=5e9, turnover=3.2, circ_mv=6e11))
        session.add(StockFundFlow(code="002594", name="比亚迪", trade_date="2026-09-25", net_inflow=3e8, net_ratio=6.0,
                                  amount=5e9, source="同花顺"))
    yield path
    if db_module._engine:
        db_module._engine.dispose()
    db_module._engine = None
    db_module._SessionFactory = None


REPLY = {
    "score": 82, "action": "买入", "confidence": "高", "one_sentence": "结论",
    "position_advice": {"no_position": "低吸", "has_position": "持有"},
    "battle_plan": {"buy_price": 23.8, "stop_loss": 22.5, "target_price": 26.0, "suggested_position": "3成"},
    "catalysts": [], "risks": [], "checklist": [], "analysis": "分析",
    "phase_decision": {"trading_window": "开盘后 30 分钟观察承接", "immediate_action": "立即买入", "watch_conditions": ["放量突破", "不破均线"],
                       "next_check_time": "下一交易日 9:25"},
    "signal_attribution": {"technical": "40%", "news": 30, "fundamentals": 20, "market": "10", "strongest_bullish": "龙头二板", "strongest_bearish": "高位分歧"},
}


def _svc(db_path, reply):
    llm = _LLM(reply)
    return StockDiagnosisService({"database": {"sqlite_path": db_path}, "risk": {}, "trading": {}}, llm=llm), llm


def test_diagnose_outputs_normalized_fields(db_path):
    svc, llm = _svc(db_path, REPLY)
    r = svc.diagnose("002594", force=True)
    assert r["action"] == "buy"  # 盘后不改
    assert r["phase_decision"]["immediate_action"] == "立即买入"
    assert r["phase_decision"]["watch_conditions"] == ["放量突破", "不破均线"]
    att = r["signal_attribution"]
    assert [att[k] for k in ("technical", "news", "fundamentals", "market")] == [40, 30, 20, 10]
    assert r["market_phase"]["phase"] == "postmarket"
    assert {"phase", "label", "now", "effective_daily_bar_date"} <= set(r["market_phase"])
    md = render_markdown(r)
    assert "### 阶段决策" in md and "### 信号归因" in md
    assert "开盘后 30 分钟观察承接" in md and "龙头二板" in md and "高位分歧" in md and "40%" in md
    sysmsg = llm.systems[0]
    assert "phase_decision" in sysmsg and "signal_attribution" in sysmsg and "【市场阶段】" in sysmsg
    assert "【市场阶段】" in llm.calls[0]


def test_market_phase_line_after_stock_line(db_path):
    text = _svc(db_path, {})[0].build_context("002594")["text"].split("\n")
    assert text[0].startswith("股票：") and text[1].startswith("【市场阶段】")


def test_context_has_phase(db_path):
    assert _svc(db_path, {})[0].build_context("002594")["phase"]["phase"] == "postmarket"


def test_markdown_omits_sections_when_empty(db_path):
    svc, _ = _svc(db_path, {k: v for k, v in REPLY.items() if k not in ("phase_decision", "signal_attribution")})
    r = svc.diagnose("002594", force=True)
    assert r["signal_attribution"] == {}
    md = render_markdown(r)
    assert "### 信号归因" not in md


def test_premarket_rewrites_immediate_buy(db_path, monkeypatch):
    ctx = {"phase": "premarket", "label": "盘前", "now": "2026-09-28 08:30", "is_trading_day": True, "is_partial_bar": False,
           "minutes_to_open": 60, "minutes_to_close": None, "effective_daily_bar_date": "2026-09-25"}
    monkeypatch.setattr(mp, "current_phase", lambda now=None: dict(ctx))
    svc, llm = _svc(db_path, REPLY)
    r = svc.diagnose("002594", force=True)
    assert r["phase_decision"]["immediate_action"] == "等待开盘后确认承接，不追价"
    assert r["confidence"] == "中"
    assert any("当前为盘前，不支持立即买卖" in g for g in r["guardrails"])
    assert r["market_phase"]["phase"] == "premarket"
    assert "尚未开盘" in llm.calls[0]


def test_stale_quote_downgrades_in_diagnose(db_path, monkeypatch):
    ctx = {"phase": "postmarket", "label": "盘后", "now": "2026-09-29 16:00", "is_trading_day": True, "is_partial_bar": False,
           "minutes_to_open": None, "minutes_to_close": None, "effective_daily_bar_date": "2026-09-29"}
    monkeypatch.setattr(mp, "current_phase", lambda now=None: dict(ctx))
    r = _svc(db_path, REPLY)[0].diagnose("002594", force=True)
    assert r["action"] == "watch" and any("降级为观望" in g for g in r["guardrails"])


def test_context_without_phase_skips_guardrail(db_path):
    svc, _ = _svc(db_path, REPLY)
    ctx = svc.build_context("002594")
    ctx.pop("phase")
    out = svc._apply_guardrails(dict(REPLY), ctx)
    assert out["action"] == "buy"
