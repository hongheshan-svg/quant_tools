"""行情故障与策略口径回归；全部使用合成行情、临时库及离线替身。"""

import json
from contextlib import contextmanager
from datetime import datetime, timedelta
from types import SimpleNamespace as B

import pandas as pd
import pytest

from src import trading_calendar
from src.collectors import stock_data as sd, source_chain as sc
from src.database.db import get_db_session
from src.database.models import ResearchCache, StockDaily, StrategyBacktest, StrategyPick
from src.services.config_check import check_config
from src.services.premarket_predictor import LimitUpPredictor
from src.strategy.screener import BACKTEST_ENGINE_VERSION, StrategyScreener, compute_features
from src.strategy.screening_rules import load_rules, matches
from src.strategy.strategy_backtest import StrategyBacktester, strategy_weights
from tests.test_all_sources_stability import _make_orchestrator
from tests.test_screener import TRADE_DATE, _bar, config  # noqa: F401


@pytest.fixture(autouse=True)
def fresh_source_state(monkeypatch):
    sc._breakers.clear()
    sc._last_good.clear()
    sc.source_health.reset()
    monkeypatch.setattr(sc.time, "sleep", lambda _: None)
    monkeypatch.setattr(trading_calendar, "_trade_days", set())
    yield
    sc._breakers.clear()
    sc._last_good.clear()
    sc.source_health.reset()


def test_market_orchestrator_reports_total_quote_failure_and_closes_client(monkeypatch):
    orchestrator = _make_orchestrator(monkeypatch)
    orchestrator.config["data_sources"] = {"realtime": ["tencent"]}
    monkeypatch.setattr(trading_calendar, "market_data_ready", lambda: True)
    monkeypatch.setattr(sd.StockDataCollector, "_fetch_tencent_quotes", lambda self: pd.DataFrame())
    for name in ("_collect_limit_up_pool", "_collect_dragon_tiger", "_collect_northbound_flow"):
        monkeypatch.setattr(sd.StockDataCollector, name, lambda *args: None)
    monkeypatch.setattr("src.services.collector_orchestrator.collect_fund_flow", lambda *args: None)
    closed = []
    original_close = sd.StockDataCollector.close
    monkeypatch.setattr(sd.StockDataCollector, "close", lambda self: (closed.append(True), original_close(self)))
    result = orchestrator.collect_market_parallel()
    assert result["realtime_quotes"].startswith("error: 实时行情采集失败")
    assert "market.realtime_quotes" in orchestrator._check_missing_sources({"market": result})
    assert closed == [True]


def test_quote_write_failure_is_propagated_and_redacted(monkeypatch):
    @contextmanager
    def broken_session(*args):
        raise RuntimeError("api_key=private-token 数据库不可写")
        yield
    monkeypatch.setattr(sd, "get_db_session", broken_session)
    with sd.StockDataCollector({}) as collector:
        with pytest.raises(RuntimeError, match="入库失败") as error:
            collector._save_quotes(pd.DataFrame({"代码": ["600001"], "最新价": [10]}), TRADE_DATE, "unused")
    assert "private-token" not in str(error.value)


def test_quote_update_preserves_current_st_status_for_pool_filter(config):  # noqa: F811
    path = config["database"]["sqlite_path"]
    with sd.StockDataCollector(config) as collector:
        collector._save_quotes(pd.DataFrame({"代码": ["600003"], "名称": ["*ST龙回头"], "最新价": [11],
                                             "成交额": [5e7], "涨跌幅": [-0.9]}), TRADE_DATE, path)
    with get_db_session(path) as session:
        row = StrategyScreener._snapshot(session, TRADE_DATE)["600003"]
        assert row.name == "*ST龙回头" and not StrategyScreener(config)._in_pool(row)


def test_next_day_performance_keeps_index_and_equity_separate(config):  # noqa: F811
    path = config["database"]["sqlite_path"]
    with get_db_session(path) as session:
        session.add(StockDaily(code="000001", trade_date="2026-09-22", close=12, change_pct=-1))
        session.add(StockDaily(code="sh000001", trade_date="2026-09-22", close=3000, change_pct=2))
    with get_db_session(path) as session:
        assert StrategyScreener._next_day_changes(session, TRADE_DATE, {"000001"}) == {"000001": -1}


@pytest.mark.parametrize("field,value", [("close", float("inf")), ("close", float("nan")),
                                         ("change_pct", None), ("amount", float("inf")), ("amount", -1)])
def test_invalid_current_metrics_cannot_enter_screening(field, value):
    assert compute_features("600001", "测试", [B(**{**_bar(10), field: value})]) is None
    assert compute_features("600001", "测试", []) is None


def test_invalid_history_does_not_compress_the_moving_average_window():
    bars = [B(**_bar(10)) for _ in range(21)]
    bars[-4].close = None
    features = compute_features("600001", "测试", bars)
    assert features.bars == 3 and features.ma5 is None and features.vol_ratio is None


def test_adjustment_discontinuity_cannot_create_a_breakout():
    bars = [B(**_bar(5), price_adjustment="forward") for _ in range(21)]
    bars.append(B(**_bar(10, change=0), price_adjustment="none"))
    features = compute_features("600001", "测试", bars)
    assert features.bars == 1 and features.high_20 is None and features.ret_20 is None
    bars[-1] = B(**_bar(5.05, change=1), price_adjustment="none")
    assert compute_features("600001", "测试", bars).bars == 22


def test_index_alias_cannot_overwrite_equity_snapshot_or_inflate_universe(config):  # noqa: F811
    path = config["database"]["sqlite_path"]
    with get_db_session(path) as session:
        session.add(StockDaily(code="sh000001", trade_date=TRADE_DATE, close=3000, amount=1e12, change_pct=2))
        session.add(StockDaily(code="600001", name="规范股票", trade_date=TRADE_DATE, **_bar(10.9, 6.86, amount=3e8)))
    with get_db_session(path) as session:
        snapshot = StrategyScreener._snapshot(session, TRADE_DATE)
        assert "000001" not in snapshot and len(snapshot) == 8
        assert snapshot["600001"].name == "规范股票"
    dates, skipped = StrategyBacktester(config, min_universe=9).trade_dates(TRADE_DATE, TRADE_DATE)
    assert dates == [] and skipped == 1


def test_missing_history_preserves_last_good_and_excludes_ai_candidates(config, monkeypatch):  # noqa: F811
    screener = StrategyScreener(config)
    monkeypatch.setattr(StrategyScreener, "_regime", lambda *a: "均衡")
    screener.run(TRADE_DATE)
    with get_db_session(screener.db_path) as session:
        before = session.query(StrategyPick).count()
        session.query(StockDaily).filter(StockDaily.trade_date < TRADE_DATE).delete()
    result = screener.run(TRADE_DATE)
    assert result.status == "partial" and result.stats["with_history"] == 0
    assert screener.runs()[0]["status"] == "partial"
    with get_db_session(screener.db_path) as session:
        assert session.query(StrategyPick).count() == before
    monkeypatch.setattr(StrategyScreener, "run", lambda self: result)
    assert LimitUpPredictor._get_strategy_picks(B(config=config), "aftermarket") is None


def test_financial_cache_is_batched_and_bad_or_future_records_are_ignored(config):  # noqa: F811
    screener = StrategyScreener(config)
    with get_db_session(screener.db_path) as session:
        session.add(ResearchCache(key="fundamentals:600001", payload_json="broken json"))
        session.add(ResearchCache(key="fundamentals:000005", payload_json=json.dumps({"reports": [
            {"report_date": "2026-03-31", "roe": 9}, {"report_date": "2026-06-30", "roe": 15, "profit_yoy": "bad"},
            {"report_date": "2026-99-99", "roe": 100}], "dividend": {"events": [{"ex_dividend_date": "2026-09-01", "cash_per_share": "bad"}]}}),
            updated_at=datetime(2026, 9, 20)))
        session.add(ResearchCache(key="fundamentals:600003", payload_json='{"reports":[]}', updated_at=datetime(2026, 9, 22)))
        session.add(ResearchCache(key="fundamentals:000004", payload_json='{"reports":[]}', updated_at=datetime(2026, 1, 1)))
    from sqlalchemy import event
    from src.database import db
    queries = []
    def capture(conn, cursor, statement, parameters, context, executemany):
        if "research_cache" in statement.lower():
            queries.append(statement)
    event.listen(db._engine, "before_cursor_execute", capture)
    try:
        with get_db_session(screener.db_path) as session:
            payloads = screener._financial_payloads(session, ["600001", "000005", "600003", "000004"], TRADE_DATE, True)
    finally:
        event.remove(db._engine, "before_cursor_execute", capture)
    assert set(payloads) == {"000005"} and len(queries) == 1
    features = compute_features("000005", "测试", [B(**_bar(10))])
    screener._financial_features(features, TRADE_DATE, payloads["000005"])
    assert features.roe == 15 and features.profit_yoy is None and features.dividend_yield is None


@pytest.mark.parametrize("payload", [[], {"rules": "wrong"}, {"rules": ["wrong"]}, {"rules": [{"name": "x", "enabled": "false"}]},
                                    {"rules": [{"name": "x", "regimes": "均衡"}]},
                                    {"rules": [{"name": "x", "conditions": [{"field": "close", "op": "gt", "ref": "ma20", "value": 5}]}]}])
def test_malformed_rules_fail_with_clear_validation_error(tmp_path, payload):
    import yaml
    path = tmp_path / "rules.yaml"
    path.write_text(yaml.safe_dump(payload, allow_unicode=True))
    with pytest.raises(ValueError):
        load_rules(str(path))


@pytest.mark.parametrize("target", [None, float("nan"), float("inf"), "bad", True])
def test_reference_to_invalid_indicator_does_not_match(target):
    assert not matches(B(close=10, ma20=target), [{"field": "close", "op": "gte", "ref": "ma20"}])


def test_invalid_builtin_parameters_fail_configuration_check():
    result = check_config({"screening": {"strategies": {"volume_breakout": {"vol_ratio_min": float("nan")}}}}, example={})
    assert not result["ok"] and any(issue["path"] == "screening" for issue in result["issues"])


def _forward_bar(day, opening=10, close=11, change=1, adjustment=None):
    return B(trade_date=day, open=opening, close=close, high=max(opening or close, close) + 0.1,
             low=min(opening or close, close) - 0.1, change_pct=change, volume=10000, amount=100000, price_adjustment=adjustment)


def test_backtest_obeys_t1_and_does_not_fabricate_missing_entry():
    days = StrategyBacktester._expected_days(TRADE_DATE)
    bars = [_forward_bar(day, close=11 + i) for i, day in enumerate(days)]
    result = StrategyBacktester._outcome("600001", "测试", 8, bars, trade_date=TRADE_DATE)
    assert result["r1"] == pytest.approx(20) and result["r5"] == pytest.approx(60)
    bars[0].open = None
    assert "r1" not in StrategyBacktester._outcome("600001", "测试", 8, bars, trade_date=TRADE_DATE)


def test_missing_next_day_remains_a_gap_instead_of_shifting_horizons(config):  # noqa: F811
    tester = StrategyBacktester(config, min_universe=5)
    days = tester._expected_days(TRADE_DATE)
    with get_db_session(tester.db_path) as session:
        for day in days[1:]:
            session.add(StockDaily(code="600001", trade_date=day, **_bar(12)))
    forward = tester._forward_bars(TRADE_DATE, {"600001"})["600001"]
    assert forward[0] is None and forward[1].trade_date == days[1]
    result = tester._outcome("600001", "测试", 10, forward, trade_date=TRADE_DATE)
    assert result["unavailable"] and "r1" not in result and "r5" not in result


def test_mid_window_gap_and_adjustment_mismatch_are_not_evaluated():
    days = StrategyBacktester._expected_days(TRADE_DATE)
    bars = [_forward_bar(day) for day in days]
    bars[2] = None
    result = StrategyBacktester._outcome("600001", "测试", 10, bars, trade_date=TRADE_DATE)
    assert "r1" in result and "r3" not in result and "r5" not in result
    bars = [_forward_bar(days[0], close=5, opening=5, change=0, adjustment="forward"),
            _forward_bar(days[1], close=10, change=0, adjustment="none")]
    assert "r1" not in StrategyBacktester._outcome("600001", "测试", 10, bars, trade_date=TRADE_DATE)


def test_locked_limit_up_is_an_unfilled_entry():
    day = StrategyBacktester._expected_days(TRADE_DATE)[0]
    bar = _forward_bar(day, opening=11, close=11, change=10)
    bar.high = bar.low = 11
    result = StrategyBacktester._outcome("600001", "测试", 10, [bar], trade_date=TRADE_DATE)
    assert result["entry_blocked"] and result["limit_up"] and not result["unavailable"] and "r1" not in result


def test_weight_update_requires_independent_days_and_complete_mature_samples():
    assert strategy_weights([{"strategy": "x", "evaluated": 50, "evaluated_days": 1, "avg_1d": 9}]) == {}
    assert strategy_weights([{"strategy": "x", "evaluated": 50, "evaluated_days": 15, "unavailable": 1, "avg_1d": 9}]) == {}


@pytest.mark.parametrize("change", ["legacy", "signature", "status", "samples", "nonfinite", "calendar"])
def test_unusable_backtests_do_not_supply_live_weights(config, change):  # noqa: F811
    screener = StrategyScreener(config)
    report = {"status": "success", "engine_version": BACKTEST_ENGINE_VERSION, "strategy_signature": screener.strategy_signature(),
              "calendar_verified": True,
              "weights": {"volume_breakout": 1.0}, "strategies": [{"strategy": "volume_breakout", "evaluated": 50, "evaluated_days": 10, "avg_1d": 2}]}
    if change == "legacy":
        report.pop("engine_version")
    elif change == "signature":
        report["strategy_signature"] = "old-rules"
    elif change == "status":
        report["status"] = "partial"
    elif change == "samples":
        report["strategies"][0]["evaluated_days"] = 1
    elif change == "calendar":
        report["calendar_verified"] = False
    else:
        report["weights"]["volume_breakout"] = float("nan")
    with get_db_session(screener.db_path) as session:
        session.add(StrategyBacktest(start_date=TRADE_DATE, end_date=TRADE_DATE, result_json=json.dumps(report)))
    assert screener.strategy_weights() == {}


def test_failed_selection_is_not_a_completed_backtest(config, monkeypatch):  # noqa: F811
    tester = StrategyBacktester(config, min_universe=5)
    monkeypatch.setattr(tester.screener, "run", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("bad history")))
    progress = []
    report = tester.run(days=0, end=TRADE_DATE, progress=lambda i, n: progress.append((i, n)))
    assert report["status"] == "partial" and report["dates"] == 0 and report["failed_dates"] == 1
    assert report["weights"] == {} and progress == [(1, 1)] and tester.latest() is None


def test_entire_missing_market_day_is_counted_as_skipped(config):  # noqa: F811
    tester = StrategyBacktester(config, min_universe=5)
    with get_db_session(tester.db_path) as session:
        session.query(StockDaily).filter_by(trade_date="2026-09-18").delete()
    dates, skipped = tester.trade_dates("2026-09-18", TRADE_DATE)
    assert dates == [TRADE_DATE] and skipped == 1
