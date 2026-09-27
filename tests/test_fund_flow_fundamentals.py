from __future__ import annotations

from datetime import date, datetime

import pandas as pd
import pytest

from src.collectors import fund_flow as ff
from src.collectors import fundamentals as fm
from src.collectors import source_chain as sc
from src.database import db as db_module
from src.database.db import get_db_session, init_db
from src.database.models import StockDaily, StockFundFlow
from src.strategy.capital_score import calculate_capital_score, fund_flow_score


def _reset_db_engine():
    if db_module._engine:
        db_module._engine.dispose()
    db_module._engine = None
    db_module._SessionFactory = None


@pytest.fixture
def db_path(tmp_path):
    sc._breakers.clear()
    sc._last_good.clear()
    ff._last_success = None
    path = str(tmp_path / "flow.db")
    _reset_db_engine()
    init_db(path)
    yield path
    ff._last_success = None
    sc._breakers.clear()
    _reset_db_engine()


# ---------- 资金流 ----------

def test_parse_cn_amount():
    assert [ff.parse_cn_amount(v) for v in ("2.99亿", "-2753.86万", "123", 5.0, "--", None)] == [
        2.99e8, pytest.approx(-2.75386e7), 123.0, 5.0, None, None,
    ]


def test_ths_fund_flow_parsing(monkeypatch):
    import akshare as ak

    df = pd.DataFrame([
        {"股票代码": 301311, "股票简称": "昆船智能", "净额": "2753.86万", "成交额": "5.70亿"},
        {"股票代码": "600000", "股票简称": "浦发银行", "净额": "-1.20亿", "成交额": "12.00亿"},
        {"股票代码": "600001", "股票简称": "无数据", "净额": "--", "成交额": "1亿"},
    ])
    monkeypatch.setattr(ak, "stock_fund_flow_individual", lambda symbol: df)
    rows = ff.fetch_ths_fund_flow()
    assert [(r["code"], round(r["net_inflow"]), r["net_ratio"]) for r in rows] == [
        ("301311", 27538600, 4.83), ("600000", -120000000, -10.0),
    ]


def test_collect_fund_flow_throttles_and_falls_back(db_path, monkeypatch):
    monkeypatch.setattr(ff, "fetch_ths_fund_flow", lambda: (_ for _ in ()).throw(ConnectionError("ths down")))
    monkeypatch.setattr(ff, "fetch_em_fund_flow", lambda: [
        {"code": "600519", "name": "贵州茅台", "net_inflow": 5e8, "amount": 5e9, "net_ratio": 10.0, "source": "东方财富"},
    ])
    assert ff.collect_fund_flow("2026-09-24", db_path) == 1
    assert ff.collect_fund_flow("2026-09-24", db_path) == 0  # 10 分钟内不重复采集

    monkeypatch.setattr(ff, "fetch_em_fund_flow", lambda: [
        {"code": "600519", "name": "贵州茅台", "net_inflow": 6e8, "amount": 5e9, "net_ratio": 12.0, "source": "东方财富"},
    ])
    assert ff.collect_fund_flow("2026-09-24", db_path, force=True) == 1  # 覆盖当天旧值
    with get_db_session(db_path) as session:
        rows = session.query(StockFundFlow).all()
        assert [(r.code, r.net_ratio, r.source) for r in rows] == [("600519", 12.0, "东方财富")]
        assert ff.describe(ff.latest_fund_flow(session, "sh600519")) == "净流入6.00亿（占成交额12.0%，东方财富，2026-09-24）"


def test_final_snapshot_after_close(monkeypatch):
    ff._last_success = datetime(2026, 9, 24, 14, 58)
    assert ff._due(datetime(2026, 9, 24, 15, 3)) is False
    assert ff._due(datetime(2026, 9, 24, 15, 6)) is True  # 收盘后补采最终数据
    ff._last_success = datetime(2026, 9, 24, 15, 6)
    assert ff._due(datetime(2026, 9, 24, 15, 10)) is False
    ff._last_success = None


def test_capital_score_uses_fund_flow(db_path):
    today = date.today().strftime("%Y-%m-%d")
    assert calculate_capital_score("600519", db_path) == 50  # 无数据：中性
    with get_db_session(db_path) as session:
        session.add(StockFundFlow(code="600519", trade_date=today, net_inflow=5e8, net_ratio=12.0, source="同花顺"))
    assert calculate_capital_score("sh600519", db_path) == pytest.approx(90 * 0.5 + 50 * 0.3 + 50 * 0.2)
    assert [fund_flow_score(r) for r in (12, 6, 2, 0, -3, -8)] == [90, 78, 65, 50, 35, 20]


# ---------- 筹码 ----------

def _bars(closes: list[float], turnover: float = 5.0) -> list[tuple]:
    return [(f"d{i:03d}", c, c * 1.01, c * 0.99, c, turnover) for i, c in enumerate(closes)]


def test_local_chip_distribution():
    rising = fm.compute_chip_distribution(_bars([10 + i * 0.1 for i in range(80)]))
    assert rising["profit_ratio"] > 90  # 一路上涨，绝大多数筹码获利
    assert rising["cost_90_low"] < rising["avg_cost"] < rising["cost_90_high"] <= 18.1
    assert rising["source"] == "本地估算"

    falling = fm.compute_chip_distribution(_bars([20 - i * 0.1 for i in range(80)]))
    assert falling["profit_ratio"] < 10

    assert fm.compute_chip_distribution(_bars([10.0] * 30)) is None  # 不足 60 根


def test_chip_falls_back_to_local(db_path, monkeypatch):
    with get_db_session(db_path) as session:
        for i, c in enumerate([10 + i * 0.1 for i in range(80)]):
            session.add(StockDaily(code="600519", trade_date=f"2026-{6 + i // 28:02d}-{i % 28 + 1:02d}",
                                   open=c, high=c * 1.01, low=c * 0.99, close=c, turnover=5.0))
    monkeypatch.setattr(fm, "_chip_from_akshare", lambda code: (_ for _ in ()).throw(ConnectionError("em blocked")))
    chip = fm.fetch_chip_summary("600519", db_path)
    assert chip["source"] == "本地估算" and chip["profit_ratio"] > 90
    assert fm.describe_chips(chip).startswith("获利盘")


# ---------- 业绩 ----------

def test_recent_report_periods():
    assert fm.recent_report_periods(date(2026, 9, 28)) == ["20260930", "20260630", "20260331"]
    assert fm.recent_report_periods(date(2026, 2, 3)) == ["20260331", "20251231", "20250930"]


def test_earnings_cache_keeps_latest_notice(monkeypatch):
    import akshare as ak

    forecasts = {
        "20260630": pd.DataFrame([{"股票代码": "600519", "预告类型": "略增", "业绩变动": "H1 增长 5%", "业绩变动幅度": 5,
                                   "公告日期": date(2026, 7, 10)}]),
        "20260930": pd.DataFrame([{"股票代码": "600519", "预告类型": "预减", "业绩变动": "1-9月下降 40%", "业绩变动幅度": -40,
                                   "公告日期": date(2026, 9, 20)}]),
    }
    monkeypatch.setattr(ak, "stock_yjyg_em", lambda date: forecasts.get(date, pd.DataFrame()))
    monkeypatch.setattr(ak, "stock_yjkb_em", lambda date: (_ for _ in ()).throw(TypeError("no data")))
    fm.EarningsCache.reset()
    try:
        record = fm.EarningsCache.get("sh600519")
        assert (record["period"], record["change_type"]) == ("20260930", "预减")
        assert fm.earnings_risk(record) == "最新业绩预告为「预减」"
        assert fm.EarningsCache.get("000001") is None
    finally:
        fm.EarningsCache.reset()
