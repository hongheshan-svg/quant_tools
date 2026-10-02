"""个股研究聚合：各块独立的 status（fresh / partial / unavailable）和内容。"""

from __future__ import annotations

import json
from datetime import datetime

from src.database.db import get_db_session
from src.database.models import DecisionSignal, StockDaily, StockDiagnosis, Watchlist
from src.services.real_portfolio import RealPortfolioService
from tests.test_api import env  # noqa: F401

P = "/api/v1/stocks"


def test_profile_blocks(env):  # noqa: F811
    client, _, config = env
    path = config["database"]["sqlite_path"]
    with get_db_session(path) as s:
        s.add(StockDaily(code="601919", name="中远海控", trade_date="2026-09-26", close=12.0, change_pct=2.0))  # 全市场最新行情日
        s.add(StockDiagnosis(code="600519", name="贵州茅台", trade_date="2026-09-25", action="buy", score=78,
                             result_json=json.dumps({"action": "buy", "action_label": "买入", "score": 78,
                                                     "created_at": "2026-09-25 16:40", "one_sentence": "放量突破"}),
                             created_at=datetime(2026, 9, 25, 16, 40)))
        s.add(DecisionSignal(code="600519", name="贵州茅台", action="buy", trade_date="2026-09-25", status="active",
                             stop_loss=9.0, target_price=12.0, horizon_days=5, expires_on="2026-10-09"))
        s.add(Watchlist(code="600519", name="贵州茅台"))
    assert RealPortfolioService(config).add_trade("2026-09-24", "600519", "buy", 9.5, 200)["ok"]
    config["alerts"] = {"rules": [{"code": "600519", "type": "price_cross", "direction": "above", "price": 11, "note": "突破提醒"},
                                  {"code": "600519", "type": "change_pct", "enabled": False},
                                  {"code": "601919", "type": "price_cross", "price": 13}]}

    r = client.get(f"{P}/600519/profile").json()
    assert (r["code"], r["kind"], r["name"]) == ("600519", "stock", "贵州茅台")
    assert r["quote"]["status"] == "partial" and r["quote"]["limitations"] == ["stale_quote"]     # 比全市场最新行情日早
    assert r["research"]["status"] == "fresh" and r["research"]["data"]["one_sentence"] == "放量突破"
    sig = r["signals"]
    assert sig["status"] == "fresh" and sig["data"]["active"][0]["stop_loss"] == 9.0
    port = r["portfolio"]
    assert port["status"] == "fresh" and port["data"]["held"]
    real = next(h for h in port["data"]["holdings"] if h["source"] == "real")
    assert real["quantity"] == 200 and real["avg_cost"] == 9.5 and real["unrealized_pnl_pct"] == round((10 / 9.5 - 1) * 100, 2)
    mon = r["monitors"]["data"]
    assert mon["in_watchlist"] is True
    assert [x["text"] for x in mon["alert_rules"]] == ["价格突破：上破，价格（元） 11"]       # 停用的、别的股票的不算
    assert r["evidence_quality"] == {"fresh": 5, "partial": 1, "unavailable": 1}


def test_profile_empty_stock_and_fund(env):  # noqa: F811
    client, _, _ = env
    r = client.get(f"{P}/601919/profile").json()
    assert r["research"] == {"status": "unavailable", "limitations": ["no_diagnosis"]}
    assert r["signals"]["status"] == "unavailable" and r["portfolio"]["data"] == {"held": False, "holdings": []}
    idx = client.get(f"{P}/sh000300/profile").json()                                           # 指数在内置注册表里
    assert (idx["kind"], idx["code"], idx["name"]) == ("index", "sh000300", "沪深300")
    assert idx["portfolio"]["limitations"] == ["not_tradable"] and idx["monitors"]["limitations"] == ["fund_no_alerts"]
    assert idx["quote"]["status"] == "unavailable"                                             # 本地还没有指数日线
