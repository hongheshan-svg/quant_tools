"""轮动研究：槽位、防守、缓冲、成交滞后、费用、缺价、截止日与价格口径。"""

import json
import numpy as np
import pandas as pd
import pytest

from src.strategy.etf_rotation import RotationParams, holdings_to_weights, select_holdings, run_backtest, parameter_sweep
from src.services.etf_rotation import ETFRotationService
from src.database.db import get_db_session
from src.database.models import FundDaily
from tests.test_real_portfolio import config  # noqa: F401


def prices():
    dates = pd.bdate_range("2025-01-01", periods=180)
    return pd.DataFrame({"510300": 10 * np.cumprod(np.full(180, 1.003)), "510500": 10 * np.cumprod(np.full(180, .997)), "511880": np.ones(180)}, index=dates)


def test_fixed_slots_absolute_momentum_and_switch_buffer():
    params = RotationParams(top_n=2)
    chosen = select_holdings(pd.Series({"A": .1, "B": -.1}), (), params)
    assert holdings_to_weights(chosen, 2, "SAFE") == {"A": .5, "SAFE": .5}
    assert holdings_to_weights((), 2, None) == {"CASH": 1}
    assert select_holdings(pd.Series({"A": .1, "B": .115}), ("A",), RotationParams(switch_buffer_pct=2)) == ("A",)


def test_next_session_execution_costs_and_common_sweep_window():
    frame = prices()
    free = run_backtest(frame, ["510300", "510500"], "511880", RotationParams(lookback_days=20, cost_bps=0))
    charged = run_backtest(frame, ["510300", "510500"], "511880", RotationParams(lookback_days=20, cost_bps=10))
    assert charged.equity.iloc[-1] < free.equity.iloc[-1]
    assert all(frame.index.get_loc(t.exec_date) == frame.index.get_loc(t.signal_date) + 1 for t in charged.trades)
    assert all(t.signal_date.weekday() == 4 for t in charged.trades)
    assert len(parameter_sweep(frame, ["510300"], None, RotationParams(lookback_days=20), lookbacks=[20, 40])) == 2


def test_missing_execution_quote_never_uses_forward_fill_to_trade():
    frame = prices()
    result = run_backtest(frame, ["510300"], None, RotationParams(lookback_days=20))
    first_day = result.trades[0].exec_date
    frame.loc[first_day, "510300"] = np.nan
    result = run_backtest(frame, ["510300"], None, RotationParams(lookback_days=20))
    assert all(t.exec_date != first_day for t in result.trades)


def test_service_frozen_pool_cutoff_and_finite_json(config, monkeypatch):
    monkeypatch.setattr("src.trading_calendar.load", lambda *a, **k: True)
    frame = prices()
    with get_db_session(config["database"]["sqlite_path"]) as session:
        session.add_all(FundDaily(code=code, trade_date=day.date().isoformat(), close=value, price_adjustment="forward", source="fixture") for code in frame.columns for day, value in frame[code].items())
    config["etf_rotation"] = {"risk_assets": ["510300", "510500"], "safe_asset": "511880", "start": "2025-01-01", "end": "2025-05-30", "lookback_days": 20, "min_years": 8}
    result = ETFRotationService(config).run()
    assert result["as_of"] == "2025-05-30"
    assert "insufficient_backtest_span" in result["limitations"]
    assert all(t["execution_date"] > t["signal_date"] for t in result["trades"])
    assert len(result["price_snapshot_hash"]) == 64
    json.dumps(result, allow_nan=False)


def test_unknown_price_basis_rejected(config):
    config["etf_rotation"] = {"risk_assets": ["510300"]}
    with get_db_session(config["database"]["sqlite_path"]) as session:
        session.add(FundDaily(code="510300", trade_date="2025-01-01", close=10))
    with pytest.raises(ValueError, match="前复权"):
        ETFRotationService(config).run()
