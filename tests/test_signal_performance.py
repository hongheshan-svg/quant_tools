from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest

from src import trading_calendar
from src.database import db as db_module
from src.database.db import get_db_session, init_db
from src.database.models import LimitUpStock, StockDaily, TradeSignal
from src.services.signal_performance import SignalPerformanceService, simulate_exit

DAYS = ["2026-09-21", "2026-09-22", "2026-09-23", "2026-09-24", "2026-09-25", "2026-09-28", "2026-09-29"]


def _bar(o, h, low, c):
    return SimpleNamespace(open=o, high=h, low=low, close=c)


@pytest.fixture(autouse=True)
def _calendar():
    trading_calendar._set_days(set(DAYS))
    yield
    trading_calendar._set_days(set())


def _reset_db_engine():
    if db_module._engine:
        db_module._engine.dispose()
    db_module._engine = None
    db_module._SessionFactory = None


# ---------- 离场模拟 ----------

def test_exit_ignores_entry_day_because_of_t_plus_1():
    bars = [_bar(10, 10.2, 9.0, 10.1), _bar(10.1, 10.3, 9.9, 10.2), _bar(10.2, 10.4, 10.0, 10.3)]
    assert simulate_exit(bars, stop=9.5, take=11.5, last_index=2) == ("window_end", 10.3)


def test_exit_take_profit_and_gap_prices():
    bars = [_bar(10, 10, 10, 10), _bar(10.3, 11.0, 10.2, 10.9)]
    assert simulate_exit(bars, stop=9.5, take=10.8, last_index=1) == ("take_profit", 10.8)

    gap_up = [_bar(10, 10, 10, 10), _bar(11.2, 11.5, 11.1, 11.4)]
    assert simulate_exit(gap_up, stop=9.5, take=10.8, last_index=1) == ("take_profit", 11.2)

    gap_down = [_bar(10, 10, 10, 10), _bar(9.0, 9.2, 8.8, 9.1)]
    assert simulate_exit(gap_down, stop=9.5, take=11.5, last_index=1) == ("stop_loss", 9.0)


def test_exit_same_bar_hits_both_assumes_stop_loss_first():
    bars = [_bar(10, 10, 10, 10), _bar(10, 11.6, 9.4, 10.5)]
    assert simulate_exit(bars, stop=9.5, take=11.5, last_index=1) == ("ambiguous_stop_loss", 9.5)


def test_exit_pending_when_window_not_complete():
    bars = [_bar(10, 10, 10, 10), _bar(10.1, 10.3, 9.9, 10.2)]
    assert simulate_exit(bars, stop=9.5, take=11.5, last_index=4) == ("", None)


# ---------- 端到端统计 ----------

def _seed(db_path: str):
    closes = {"2026-09-22": 10.5, "2026-09-23": 10.2, "2026-09-24": 11.0, "2026-09-25": 10.4, "2026-09-28": 9.9, "2026-09-29": 10.1}
    with get_db_session(db_path) as session:
        for d, c in closes.items():
            session.add(StockDaily(code="600001", name="评分样本", trade_date=d, open=10.0, high=c + 0.1, low=9.8, close=c))
            session.add(StockDaily(code="sz000002", name="预测样本", trade_date=d, open=10.0, high=11.0, low=9.8, close=c))
            session.add(StockDaily(code="300003", name="盘中样本", trade_date=d, open=10.0, high=c + 0.1, low=10.0, close=c))
        session.add(LimitUpStock(code="000002", name="预测样本", trade_date="2026-09-22"))

        # 评分信号：09-21 收盘后生成 → 09-22 开盘入场
        session.add(TradeSignal(code="600001", name="评分样本", signal_date="2026-09-21", signal_type="buy",
                                composite_score=80, reason="综合80分", created_at=datetime(2026, 9, 21, 16, 0)))
        # 盘后预测：09-21 晚上生成，目标 09-22 → 开盘入场；带价格计划
        session.add(TradeSignal(code="000002", name="预测样本", signal_date="2026-09-22", signal_type="premarket",
                                composite_score=90, ai_verdict="买入", stop_loss_price=9.0, target_price=10.8,
                                reason="##TYPE:龙头##TIME:开盘##SRC:热点驱动##x", created_at=datetime(2026, 9, 21, 20, 0)))
        # 盘中预测：09-22 10:30 生成 → 09-22 收盘入场
        session.add(TradeSignal(code="300003", name="盘中样本", signal_date="2026-09-22", signal_type="premarket",
                                composite_score=60, ai_verdict="观望",
                                reason="##TYPE:跟风##TIME:盘中##SRC:全市场##x", created_at=datetime(2026, 9, 22, 10, 30)))
        # 验证日无行情 / 验证日未到
        session.add(TradeSignal(code="688888", name="无行情", signal_date="2026-09-21", signal_type="buy",
                                composite_score=50, created_at=datetime(2026, 9, 21, 16, 0)))
        session.add(TradeSignal(code="600001", name="评分样本", signal_date="2026-09-29", signal_type="buy",
                                composite_score=70, created_at=datetime(2026, 9, 29, 16, 0)))


def test_evaluate_signals_end_to_end(tmp_path):
    db_path = str(tmp_path / "perf.db")
    _reset_db_engine()
    init_db(db_path)
    _seed(db_path)

    result = SignalPerformanceService({"database": {"sqlite_path": db_path}, "risk": {}}).evaluate(as_of="2026-09-29")
    details = {(d["code"], d["signal_date"]): d for d in result["details"]}

    scorer = details[("600001", "2026-09-21")]
    assert scorer["eval_date"] == "2026-09-22" and scorer["entry_at"] == "open" and scorer["entry_price"] == 10.0
    assert scorer["returns"] == {1: 5.0, 3: 10.0, 5: -1.0}
    assert scorer["exit_reason"] == "window_end" and scorer["simulated_return"] == -1.0
    assert scorer["status"] == "completed"

    predicted = details[("000002", "2026-09-22")]
    assert predicted["hit_limit_up"] is True
    assert predicted["source"] == "热点驱动"
    assert (predicted["stop_price"], predicted["take_price"]) == (9.0, 10.8)  # 用信号自带的价格计划
    assert predicted["exit_reason"] == "take_profit" and predicted["simulated_return"] == 8.0

    intraday = details[("300003", "2026-09-22")]
    assert intraday["entry_at"] == "close" and intraday["entry_price"] == 10.5
    # 收盘入场：第 1 天是 09-23，第 5 天是 09-29
    assert intraday["returns"][1] == pytest.approx((10.2 - 10.5) / 10.5 * 100, abs=1e-3)
    assert intraday["returns"][5] == pytest.approx((10.1 - 10.5) / 10.5 * 100, abs=1e-3)
    assert (intraday["exit_reason"], intraday["status"]) == ("window_end", "completed")

    assert details[("688888", "2026-09-21")]["status"] == "no_data"
    assert details[("600001", "2026-09-29")]["status"] == "pending"
    assert details[("600001", "2026-09-29")]["entry_price"] is None

    summary = {(r["dimension"], r["group"]): r for r in result["summary"]}
    overall = summary[("全部", "全部信号")]
    assert (overall["total"], overall["evaluated"]) == (5, 3)
    assert overall["limit_up_rate"] == pytest.approx(100 / 3, abs=0.1)
    assert overall["win_rate_1d"] == pytest.approx(200 / 3, abs=0.1)  # +5%、+5%、-2.9%
    assert summary[("信号类型", "AI涨停预测")]["total"] == 2
    assert summary[("来源", "热点驱动")]["take_profit_rate"] == 100.0
    assert summary[("AI研判", "观望")]["evaluated"] == 1
    _reset_db_engine()
