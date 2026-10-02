"""科创板成交量单位修复 + 自选股导入 BOM 处理。"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from src.collectors import daily_history as dh
from src.collectors import source_chain as sc
from src.database import db as db_module
from src.database.db import get_db_session, init_db
from src.database.models import StockDaily
from src.services.watchlist import extract_tokens

ROOT = Path(__file__).resolve().parents[1]


def _reset_db_engine():
    if db_module._engine:
        db_module._engine.dispose()
    db_module._engine = None
    db_module._SessionFactory = None


@pytest.fixture
def db(tmp_path):
    path = str(tmp_path / "star.db")
    _reset_db_engine()
    init_db(path)
    yield path
    _reset_db_engine()


# ---------- 腾讯实时行情 ----------

def _tx_line(prefix: str, code: str, name: str, vol_lots: str, amt_wan: str) -> str:
    parts = [""] * 50
    parts[0] = "1"
    parts[1] = name
    parts[2] = code
    parts[3] = "100.00"
    parts[5] = "99.00"
    parts[30] = "20260929150000"
    parts[32] = "1.00"
    parts[33] = "101.00"
    parts[34] = "98.00"
    parts[36] = vol_lots
    parts[37] = amt_wan
    parts[38] = "0.5"
    return f'v_{prefix}{code}="' + "~".join(parts) + '";'


def test_tencent_realtime_star_volume_not_multiplied(db, monkeypatch):
    from src.collectors import stock_data as sd

    text = "\n".join([
        _tx_line("sh", "688981", "中芯国际", "50000", "5000"),   # 科创板：字段 36 已是股
        _tx_line("sh", "600519", "贵州茅台", "50000", "5000"),   # 主板：手 -> 股
    ])

    import httpx
    monkeypatch.setattr(httpx, "get", lambda *a, **k: SimpleNamespace(status_code=200, text=text))
    sc._breakers.clear(); sc._last_good.clear(); sc.source_health.reset()

    collector = sd.StockDataCollector({"database": {"sqlite_path": db}, "data_sources": {"realtime": ["tencent"]}})
    monkeypatch.setattr(collector, "_get_all_stock_codes", lambda: ["sh688981", "sh600519"])
    collector._collect_realtime_quotes("2026-09-29", db)

    with get_db_session(db) as s:
        rows = {r.code: (r.volume, r.amount) for r in s.query(StockDaily).all()}
    assert rows["688981"][0] == pytest.approx(50000)
    assert rows["600519"][0] == pytest.approx(5000000)
    assert rows["688981"][1] == pytest.approx(5000 * 10000)
    assert rows["600519"][1] == pytest.approx(5000 * 10000)
    sc._breakers.clear(); sc._last_good.clear(); sc.source_health.reset()


# ---------- normalize_volume_unit ----------

@pytest.mark.parametrize("volume,amount,close,expected", [
    (1000.0, 1000 * 10.0 * 0.01, 10.0, 10.0),       # ratio=0.01 -> /100
    (1000.0, 1000 * 10.0 * 100, 10.0, 100000.0),          # ratio=100 -> *100
    (1000.0, 1000 * 10.0 * 1.0, 10.0, 1000.0),            # ratio=1 正常
    (1000.0, 1000 * 10.0 * 0.005, 10.0, 10.0),            # 下界
    (1000.0, 1000 * 10.0 * 0.02, 10.0, 10.0),             # 上界
    (1000.0, 1000 * 10.0 * 50, 10.0, 100000.0),
    (1000.0, 1000 * 10.0 * 200, 10.0, 100000.0),
    (1000.0, 1000 * 10.0 * 0.004, 10.0, 1000.0),          # 界外
    (1000.0, 1000 * 10.0 * 0.03, 10.0, 1000.0),
    (0.0, 500.0, 10.0, 0.0),
    (1000.0, 0.0, 10.0, 1000.0),
    (1000.0, 500.0, 0.0, 1000.0),
    (-5.0, 500.0, 10.0, -5.0),
    (1000.0, -1.0, 10.0, 1000.0),
])
def test_normalize_volume_unit(volume, amount, close, expected):
    assert dh.normalize_volume_unit(volume, amount, close) == pytest.approx(expected)


def _df(volume, amount, close=10.0):
    return pd.DataFrame({"date": ["2026-09-22", "2026-09-23"], "open": [close] * 2, "close": [close] * 2,
                         "high": [close] * 2, "low": [close] * 2, "volume": [1000.0, volume],
                         "amount": [1000 * close, amount], "turnover": [0.01, 0.01]})


def test_tx_daily_applies_normalization():
    # 第二行：成交量被写成 100 倍（ratio 0.01），应被还原
    df = _df(volume=100000.0, amount=100000.0 * 10.0 * 0.01)
    recs = dh.records_from_daily_df("688981", "中芯国际", "tx", df)
    assert recs[0]["volume"] == pytest.approx(1000.0)      # ratio=1 不动
    assert recs[1]["volume"] == pytest.approx(1000.0)


def test_other_sources_not_normalized():
    df = _df(volume=100000.0, amount=100000.0 * 10.0 * 0.01)
    recs = dh.records_from_daily_df("688981", "中芯国际", "daily", df)
    assert recs[1]["volume"] == pytest.approx(100000.0)
    df_em = pd.DataFrame({"日期": ["2026-09-23"], "成交量": [100000.0], "成交额": [100000.0 * 10.0 * 0.01],
                          "收盘": [10.0]})
    recs = dh.records_from_daily_df("688981", "x", "em", df_em)
    assert recs[0]["volume"] == pytest.approx(100000.0)


# ---------- repair_star_volume ----------

def _seed(db):
    rows = [
        # code, date, close, volume, amount
        ("688981", "2026-09-22", 10.0, 100000.0, 100000 * 10.0 * 0.01),   # 错：ratio 0.01
        ("sh688111", "2026-09-22", 10.0, 200000.0, 200000 * 10.0 * 0.01),  # 错：带前缀写法
        ("688222.SH", "2026-09-22", 10.0, 300000.0, 300000 * 10.0 * 0.01),
        ("689009", "2026-09-22", 10.0, 400000.0, 400000 * 10.0 * 0.01),
        ("688333", "2026-09-22", 10.0, 1000.0, 1000 * 10.0 * 1.0),        # 正常股
        ("688444", "2026-09-22", 10.0, 10.0, 10 * 10.0 * 100),            # 正常（手）
        ("600519", "2026-09-22", 10.0, 100000.0, 100000 * 10.0 * 0.01),   # 非科创板不处理
    ]
    with get_db_session(db) as s:
        for code, d, close, vol, amt in rows:
            s.add(StockDaily(code=code, name=code, trade_date=d, close=close, volume=vol, amount=amt))


def _vols(db):
    with get_db_session(db) as s:
        return {r.code: r.volume for r in s.query(StockDaily).all()}


def test_repair_dry_run_does_not_write(db):
    _seed(db)
    before = _vols(db)
    res = dh.repair_star_volume(db, apply=False)
    assert res["matched"] == 4 and res["fixed"] == 0
    assert res["checked"] >= 4
    assert _vols(db) == before


def test_repair_apply_fixes_only_star_rows_and_idempotent(db):
    _seed(db)
    before = _vols(db)
    res = dh.repair_star_volume(db, apply=True)
    assert res["matched"] == 4 and res["fixed"] == 4
    assert len(res["codes"]) >= 1
    after = _vols(db)
    for code in ("688981", "sh688111", "688222.SH", "689009"):
        assert after[code] == pytest.approx(before[code] / 100)
    for code in ("688333", "688444", "600519"):
        assert after[code] == before[code]
    again = dh.repair_star_volume(db, apply=True)
    assert again["matched"] == 0 and again["fixed"] == 0


# ---------- 脚本 ----------

def test_repair_script_dry_run_and_apply(db):
    script = ROOT / "scripts" / "repair_star_volume.py"
    assert script.exists()
    _seed(db)
    before = _vols(db)
    run = lambda *extra: subprocess.run([sys.executable, str(script), "--db", db, *extra], cwd=ROOT,
                                        capture_output=True, text=True, timeout=120)
    r = run()
    assert r.returncode == 0, r.stderr
    assert _vols_fresh(db) == before
    r = run("--apply")
    assert r.returncode == 0, r.stderr
    after = _vols_fresh(db)
    assert after["688981"] == pytest.approx(before["688981"] / 100)
    assert after["600519"] == before["600519"]


def _vols_fresh(db):
    _reset_db_engine()
    init_db(db)
    return _vols(db)


# ---------- 自选股导入 BOM ----------

def test_extract_tokens_bom_table():
    text = "﻿代码\t名称\t数量\n600519\t贵州茅台\t100000\n"
    codes, _ = extract_tokens(text)
    assert codes == ["600519"]


def test_extract_tokens_bom_csv_code_header():
    codes, names = extract_tokens("﻿code,name\n600519,贵州茅台")
    assert codes == ["600519"]


def test_extract_tokens_without_bom_unchanged():
    assert extract_tokens("代码\t名称\t数量\n600519\t贵州茅台\t100000\n")[0] == ["600519"]
    codes, names = extract_tokens("600519 贵州茅台 000001 平安银行")
    assert codes == ["600519", "000001"] and "贵州茅台" in names
