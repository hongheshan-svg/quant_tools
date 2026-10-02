"""验证闭环：策略历史回测、策略权重、AI 诊断事后验证。"""

from __future__ import annotations

import json
from datetime import datetime

import pytest

from src import trading_calendar
from src.database.db import get_db_session
from src.database.models import StockDaily, StockDiagnosis, StrategyBacktest
from src.services.diagnosis_outcome import DiagnosisOutcomeService, plan_result
from src.strategy import strategy_backtest as bt
from src.strategy.screener import StrategyScreener
from src.strategy.strategy_backtest import StrategyBacktester, max_drawdown, strategy_weights
from tests.test_screener import TRADE_DATE, _reset_db_engine, config  # noqa: F401  复用选股测试的合成行情

FORWARD_DAYS = ["2026-09-22", "2026-09-23", "2026-09-24", "2026-09-25", "2026-09-28", "2026-09-29"]


@pytest.fixture
def backtest_db(config, monkeypatch):  # noqa: F811
    monkeypatch.setattr(StrategyScreener, "_regime", lambda self, d, point_in_time=False: "均衡" if point_in_time else "进攻")
    with get_db_session(config["database"]["sqlite_path"]) as session:
        # 突破股：次日高开 11.0 后涨停
        for day, (o, c, ch) in zip(FORWARD_DAYS, [(11.0, 12.1, 10.0), (12.1, 12.0, -0.8), (12.0, 12.5, 4.2), (12.5, 12.0, -4.0), (12.0, 11.5, -4.2), (11.5, 11.0, -4.35)]):
            session.add(StockDaily(code="600001", name="突破股", trade_date=day, open=o, close=c, high=max(o, c), low=min(o, c), change_pct=ch))
        session.add(StockDaily(code="000005", name="趋势股", trade_date=FORWARD_DAYS[0], open=13.3, close=13.0, high=13.4, low=12.9, change_pct=-2.0))
        session.add(StockDaily(code="000005", name="趋势股", trade_date=FORWARD_DAYS[1], open=13.0, close=12.8, high=13.1, low=12.7, change_pct=-1.54))
    return config


def test_backtest_point_in_time_stats(backtest_db):
    calls = []
    report = StrategyBacktester(backtest_db, min_universe=5).run(days=0, end=TRADE_DATE, progress=lambda i, n: calls.append((i, n)))
    assert (report["start"], report["end"], report["dates"], report["skipped_dates"]) == (TRADE_DATE, TRADE_DATE, 1, 0)
    assert calls == [(1, 1)]
    stats = {s["strategy"]: s for s in report["strategies"]}

    breakout = stats["volume_breakout"]
    assert (breakout["picks"], breakout["evaluated"], breakout["days"]) == (1, 1, 1)
    assert breakout["avg_1d"] == 9.09 and breakout["win_1d"] == 100.0 and breakout["limit_up_rate"] == 100.0
    assert breakout["avg_3d"] == pytest.approx(9.09, abs=0.01) and breakout["avg_5d"] == 0.0
    assert breakout["total_return"] == 9.09 and breakout["max_drawdown"] == 0.0
    assert breakout["avg_1d_fit"] == 9.09          # 回测用当时的大盘环境（均衡），放量突破适配
    trend = stats["trend_pullback"]
    assert trend["avg_1d"] == pytest.approx(-3.76, abs=0.01) and trend["avg_1d_fit"] == pytest.approx(-3.76, abs=0.01)
    assert trend["max_drawdown"] == pytest.approx(-3.76, abs=0.01) and trend["avg_3d"] is None
    assert stats["dragon_pullback"]["picks"] == 1 and stats["dragon_pullback"]["evaluated"] == 0
    assert report["weights"] == {} and all(s["weight"] == 1.0 for s in report["strategies"])  # 样本不足不调权重

    with get_db_session(backtest_db["database"]["sqlite_path"]) as session:
        assert session.query(StrategyBacktest).count() == 1
    assert StrategyBacktester(backtest_db).latest()["dates"] == 1


def test_backtest_skips_partial_market_days(backtest_db):
    report = StrategyBacktester(backtest_db).run(days=0, end=TRADE_DATE)  # 默认要求 1000 只以上
    assert report["dates"] == 0 and report["skipped_dates"] == 1 and "fetch_history.py" in report["note"]
    assert StrategyBacktester(backtest_db).latest() is None  # 没有回测到任何一天时不保存


def test_strategy_weights_and_drawdown():
    stats = [
        {"strategy": "a", "evaluated": 40, "evaluated_days": 10, "avg_1d": 3.0},
        {"strategy": "b", "evaluated": 40, "evaluated_days": 10, "avg_1d": -1.0},
        {"strategy": "c", "evaluated": 10, "evaluated_days": 10, "avg_1d": 9.0},
    ]
    overall = 1
    weights = strategy_weights(stats)
    assert weights == {"a": round(1 + (3 - overall) * bt.WEIGHT_PER_PCT, 2), "b": round(1 + (-1 - overall) * bt.WEIGHT_PER_PCT, 2)}
    assert strategy_weights([{"strategy": "x", "evaluated": 50, "evaluated_days": 10, "avg_1d": 20.0}, {"strategy": "y", "evaluated": 50, "evaluated_days": 10, "avg_1d": -20.0}]) == {"x": 1.2, "y": 0.8}
    assert strategy_weights([]) == {}
    assert max_drawdown([10, -20, 5]) == -20.0 and max_drawdown([1, 2]) == 0.0


def test_screener_applies_latest_backtest_weights(backtest_db):
    path = backtest_db["database"]["sqlite_path"]
    report = {"engine_version": bt.BACKTEST_ENGINE_VERSION, "strategy_signature": StrategyScreener(backtest_db).strategy_signature(),
              "status": "success", "calendar_verified": True, "weights": {"volume_breakout": 0.8, "strong_close": 1.2},
              "strategies": [{"strategy": "volume_breakout", "evaluated": 40, "evaluated_days": 10, "avg_1d": -5},
                             {"strategy": "strong_close", "evaluated": 40, "evaluated_days": 10, "avg_1d": 5}]}
    with get_db_session(path) as session:
        session.add(StrategyBacktest(start_date="2026-08-01", end_date="2026-09-18",
                                     result_json=json.dumps(report)))
    result = StrategyScreener(backtest_db).run(TRADE_DATE, save=False)
    pick = next(p for p in result.picks if p.code == "600001")
    assert result.weights == {"volume_breakout": 0.8, "strong_close": 1.2}
    assert pick.labels == ["强势未板", "放量突破"] and pick.scores[1] == pytest.approx(68.0)  # 85 × 0.8
    assert StrategyScreener(backtest_db).run(TRADE_DATE, save=False, point_in_time=True).weights == {}  # 回测不用权重
    backtest_db["screening"] = {"adaptive_strategy_weights": False}
    assert StrategyScreener(backtest_db).run(TRADE_DATE, save=False).weights == {}


# ---------- AI 诊断事后验证 ----------

def _diag(code, name, trade_date, action, score, created_at, plan=None, error=None):
    labels = {"buy": "买入", "avoid": "回避", "watch": "观望", "sell": "卖出"}
    result = {"code": code, "name": name, "trade_date": trade_date, "action": action, "action_label": labels[action],
              "score": score, "created_at": created_at, "battle_plan": plan or {}}
    if error:
        result = {"code": code, "error": error}
    return StockDiagnosis(code=code, name=name, trade_date=trade_date, action=action, score=score,
                          result_json=json.dumps(result, ensure_ascii=False), created_at=datetime.now())


@pytest.fixture
def diag_db(tmp_path, monkeypatch):
    from src.database.db import init_db

    path = str(tmp_path / "diag.db")
    _reset_db_engine()
    init_db(path)
    monkeypatch.setattr(trading_calendar, "_trade_days", set())  # 按周一至周五判断
    days = ["2026-08-10", "2026-08-11", "2026-08-12", "2026-08-13", "2026-08-14", "2026-08-17"]
    with get_db_session(path) as session:
        for code, closes in {"600001": [10.0, 10.5, 11.0, 10.8, 10.6, 10.4], "000002": [20.0, 19.0, 18.5, 18.0, 17.0, 16.0],
                             "000003": [5.0, 5.1, 5.2, 5.3, 5.4, 5.5]}.items():
            for d, c in zip(days, closes):
                session.add(StockDaily(code=code, trade_date=d, close=c, high=c + 0.3, low=c - 0.2))
        session.add(_diag("600001", "看多股", days[0], "sell", 40, "2026-08-10 10:00"))   # 同一行情日后面又诊断了一次，只算最后一次
        session.add(_diag("600001", "看多股", days[0], "buy", 78, "2026-08-10 15:30", {"stop_loss": 9.5, "target_price": 11.2}))
        session.add(_diag("000002", "看空股", days[0], "avoid", 30, "2026-08-10 15:30"))
        session.add(_diag("000003", "观望股", days[0], "watch", 55, "2026-08-10 15:30"))
        session.add(_diag("000004", "", days[0], "buy", 60, "", error="行情库中没有该股票的数据"))
    yield {"database": {"sqlite_path": path}}
    _reset_db_engine()


def test_diagnosis_outcomes(diag_db):
    result = DiagnosisOutcomeService(diag_db).evaluate(lookback_days=3650)
    details = {d["code"]: d for d in result["details"]}
    assert set(details) == {"600001", "000002", "000003"}

    bull = details["600001"]
    assert bull["action"] == "buy" and (bull["r1"], bull["r3"], bull["r5"]) == (5.0, 8.0, 4.0)
    assert (bull["hit1"], bull["hit5"]) == (True, True) and bull["plan"] == "止盈先到"   # 第 2 天最高 11.3 ≥ 11.2
    bear = details["000002"]
    assert bear["r1"] == -5.0 and bear["hit1"] is True and bear["plan"] == ""
    watch = details["000003"]
    assert watch["hit1"] is None and watch["r1"] == 2.0

    summary = {(r["dimension"], r["group"]): r for r in result["summary"]}
    everything = summary[("全部", "全部")]
    assert (everything["total"], everything["evaluated"], everything["accuracy_1d"]) == (3, 3, 100.0)
    assert summary[("操作建议", "买入")]["target_first_rate"] == 100.0
    assert summary[("评分区间", "70 分以上")]["total"] == 1 and summary[("评分区间", "50 分以下")]["accuracy_3d"] == 100.0
    assert result["summary"][0]["dimension"] == "全部"


def test_plan_result():
    from types import SimpleNamespace as B

    assert plan_result([B(high=10.5, low=9.4)], 9.5, 11.0) == "止损先到"
    assert plan_result([B(high=11.5, low=9.4)], 9.5, 11.0) == "止损先到"      # 同一天都碰到按止损
    assert plan_result([B(high=10.5, low=9.8)] * 5, 9.5, 11.0) == "未触及"
    assert plan_result([B(high=10.5, low=9.8)] * 2, 9.5, 11.0) == "进行中"
    assert plan_result([B(high=12, low=9)], None, 11.0) == ""
