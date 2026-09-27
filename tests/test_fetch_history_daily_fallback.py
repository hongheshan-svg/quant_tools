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


def test_resume_start_fills_history_gap_for_explicit_start():
    # 日常采集只存了最新一天：显式 --start-date 时从起点补齐
    assert fh.resume_start("2026-09-24", "2026-09-24", "2026-06-01", fill_gaps=True) == "2026-06-01"
    # 已有从起点开始的历史（起点是周末/节假日也算）：从最新一天之后继续
    assert fh.resume_start("2026-06-02", "2026-09-24", "2026-06-01", fill_gaps=True) == "2026-09-25"
    # 默认起点（全历史）按最新日期增量追加
    assert fh.resume_start("2026-09-24", "2026-09-24", "2026-06-01", fill_gaps=False) == "2026-09-25"
    assert fh.resume_start(None, None, "2026-06-01", fill_gaps=True) == "2026-06-01"
    assert fh.resume_start("2026-01-05", "2026-03-01", "2026-06-01", fill_gaps=True) == "2026-06-01"


def test_tx_records_new_and_legacy_format():
    new = pd.DataFrame({"date": ["2026-09-22", "2026-09-23"], "open": [16.68, 16.5], "close": [16.56, 16.36],
                        "high": [16.72, 16.55], "low": [16.39, 16.29], "volume": [67682800.0, 47349300.0],
                        "turnover": [0.0054, 0.0038], "amount": [1.119125e9, 7.752728e8]})
    rec = fh.records_from_daily_df("601919", "中远海控", "tx", new)[1]
    assert rec["volume"] == 47349300.0 and rec["amount"] == 7.752728e8   # 成交量（股）、成交额（元）原样保存
    assert rec["turnover"] == pytest.approx(0.38) and rec["change_pct"] == pytest.approx(-1.2077, abs=1e-3)

    legacy = pd.DataFrame({"date": ["2026-09-23"], "open": [16.5], "close": [16.36], "high": [16.55], "low": [16.29],
                           "amount": [473493.0]})  # 旧版：amount 实为成交量（手）
    rec = fh.records_from_daily_df("601919", "中远海控", "tx", legacy)[0]
    assert rec["volume"] == 47349300.0 and rec["amount"] == pytest.approx(16.36 * 47349300.0)


def test_bulk_upsert_daily_repairs_rows(tmp_path):
    from sqlalchemy import create_engine, text

    from src.database.models import Base, StockDaily

    engine = create_engine(f"sqlite:///{tmp_path / 'h.db'}")
    Base.metadata.create_all(engine)
    fh.bulk_insert_ignore(engine, StockDaily, [
        {"code": "601919", "name": "中远海控", "trade_date": "2026-09-23", "close": 16.36, "volume": 7.7e10,
         "amount": 1.27e12, "turnover": 0.4, "circ_mv": 2.0e11},
    ])
    fixed = {"code": "601919", "name": "", "trade_date": "2026-09-23", "close": 16.36, "volume": 47349300.0,
             "amount": 7.752728e8, "turnover": 0.0, "circ_mv": 0.0}
    fh.bulk_insert_ignore(engine, StockDaily, [fixed])          # 普通模式不覆盖
    with engine.connect() as conn:
        assert conn.execute(text("select amount from stock_daily")).scalar() == 1.27e12
    fh.bulk_upsert_daily(engine, [fixed, {**fixed, "trade_date": "2026-09-24"}])
    with engine.connect() as conn:
        rows = conn.execute(text("select trade_date, name, volume, amount, turnover, circ_mv from stock_daily order by trade_date")).fetchall()
    assert rows[0] == ("2026-09-23", "中远海控", 47349300.0, 7.752728e8, 0.4, 2.0e11)  # 行情覆盖，空的换手率/市值/名称保留原值
    assert rows[1][0] == "2026-09-24"


def test_daily_download_computes_first_day_change(tmp_path, monkeypatch):
    from sqlalchemy import create_engine, text

    from src.database.models import Base

    engine = create_engine(f"sqlite:///{tmp_path / 'h.db'}")
    Base.metadata.create_all(engine)
    calls = []
    df = pd.DataFrame({"date": ["2026-09-21", "2026-09-22", "2026-09-23"], "open": [16.1, 16.68, 16.5],
                       "close": [16.71, 16.56, 16.36], "high": [16.74, 16.72, 16.55], "low": [16.06, 16.39, 16.29],
                       "volume": [1.0e8, 6.8e7, 4.7e7], "turnover": [0.0086, 0.0054, 0.0038], "amount": [1.77e9, 1.12e9, 7.75e8]})
    monkeypatch.setattr(fh, "fetch_stock_list", lambda: [{"code": "601919", "name": "中远海控"}])
    monkeypatch.setattr(fh, "fetch_daily_df_with_fallback", lambda code, start, end: calls.append(start) or ("tx", df))
    monkeypatch.setattr(fh, "SLEEP_INTERVAL", 0)

    fh.fetch_daily_ohlcv(engine, "2026-09-22", "2026-09-23", resume=False, workers=1)
    assert calls == ["2026-09-12"]  # 多取几天用于计算第一天的涨跌幅
    with engine.connect() as conn:
        rows = conn.execute(text("select trade_date, change_pct from stock_daily order by trade_date")).fetchall()
    assert [r[0] for r in rows] == ["2026-09-22", "2026-09-23"]
    assert rows[0][1] == pytest.approx((16.56 / 16.71 - 1) * 100)
