from __future__ import annotations

import pandas as pd
import pytest

from scripts import fetch_history as fh


def _raise(exc: Exception):
    raise exc


def test_fetch_daily_fallback_accepts_datetime_index(monkeypatch):
    monkeypatch.setattr(
        fh.ak,
        "stock_zh_a_hist_tx",
        lambda **_kwargs: _raise(IndexError("list index out of range")),
    )
    monkeypatch.setattr(
        fh.ak,
        "stock_zh_a_daily",
        lambda **_kwargs: pd.DataFrame(
            {
                "open": [10.0, 10.2],
                "close": [10.1, 10.4],
                "high": [10.3, 10.6],
                "low": [9.9, 10.1],
                "volume": [1000, 1200],
                "amount": [10100, 12480],
                "turnover": [0.01, 0.02],
            },
            index=pd.to_datetime(["2026-02-25", "2026-02-26"]),
        ),
    )
    monkeypatch.setattr(
        fh.ak,
        "stock_zh_a_hist",
        lambda **_kwargs: _raise(AssertionError("em fallback should not be used")),
    )

    source, df = fh.fetch_daily_df_with_fallback("000001", "2026-02-25", "2026-02-26")

    assert source == "daily"
    assert "date" in df.columns
    assert df["date"].tolist() == ["2026-02-25", "2026-02-26"]


def test_fetch_daily_fallback_retries_em_on_retryable_error(monkeypatch):
    calls = {"em": 0}

    monkeypatch.setattr(
        fh.ak,
        "stock_zh_a_hist_tx",
        lambda **_kwargs: _raise(IndexError("list index out of range")),
    )
    monkeypatch.setattr(
        fh.ak,
        "stock_zh_a_daily",
        lambda **_kwargs: _raise(KeyError("date")),
    )

    def _fake_em(**_kwargs):
        calls["em"] += 1
        if calls["em"] == 1:
            raise ConnectionError("Connection aborted. Remote end closed connection without response")
        return pd.DataFrame(
            [
                {
                    "日期": "2026-02-25",
                    "开盘": 10.0,
                    "收盘": 10.1,
                    "最高": 10.3,
                    "最低": 9.9,
                    "成交量": 1000,
                    "成交额": 10100,
                    "涨跌幅": 1.2,
                    "换手率": 0.9,
                }
            ]
        )

    monkeypatch.setattr(fh.ak, "stock_zh_a_hist", _fake_em)

    source, df = fh.fetch_daily_df_with_fallback("000001", "2026-02-25", "2026-02-26")

    assert source == "em"
    assert len(df) == 1
    assert calls["em"] == 2


def test_call_with_retry_only_retries_retryable_errors():
    calls = {"retryable": 0, "fatal": 0}

    def _retryable_fn():
        calls["retryable"] += 1
        if calls["retryable"] < 3:
            raise ConnectionError("Connection reset by peer")
        return "ok"

    assert fh.call_with_retry(_retryable_fn, attempts=3, base_sleep=0) == "ok"
    assert calls["retryable"] == 3

    def _fatal_fn():
        calls["fatal"] += 1
        raise ValueError("invalid symbol")

    with pytest.raises(ValueError):
        fh.call_with_retry(_fatal_fn, attempts=3, base_sleep=0)
    assert calls["fatal"] == 1


def test_to_ak_symbol_maps_bj_92_prefix():
    assert fh.to_ak_symbol("920001") == "bj920001"


def test_to_ak_symbol_keeps_sh_9_prefix_for_non_92():
    assert fh.to_ak_symbol("900901") == "sh900901"
