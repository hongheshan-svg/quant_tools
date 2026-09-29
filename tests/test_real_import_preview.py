"""实盘记账：交割单中的公司行为识别、导入预览（不写库）与 API。"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from src import trading_calendar
from src.database import db as db_module
from src.database.db import get_db_session, init_db
from src.database.models import RealTrade, StockDaily, StockInfo
from src.services import real_portfolio as rp
from src import settings_store
from src.services.real_portfolio import RealPortfolioService, parse_trade_rows, read_rows
from src.services.stock_search import StockSearch
from api.app import create_app
from api.auth import AuthStore

HEADER = "成交日期,成交时间,证券代码,证券名称,业务名称,成交数量,成交均价,成交金额,佣金,印花税,过户费,成交编号"
BODY = [
    "20260901,09:31:05,600519,贵州茅台,证券买入,1000,10.00,10000.00,0,0,0,A001",
    "20260910,15:00:00,600519,贵州茅台,红利入账,0,0,300.00,0,0,0,",
    "20260910,15:00:00,600519,贵州茅台,红股入账,-500,0,0,0,0,0,",
    "20260911,15:00:00,600519,贵州茅台,股息红利税补缴,0,0,-30.00,0,0,0,",
    "20260912,10:00:00,600519,贵州茅台,银证转账,0,0,5000,0,0,0,",
]
CSV = "\n".join([HEADER, *BODY]) + "\n"


def _reset_db_engine():
    if db_module._engine:
        db_module._engine.dispose()
    db_module._engine = None
    db_module._SessionFactory = None


def _seed(path):
    with get_db_session(path) as session:
        session.add(StockInfo(code="600519", name="贵州茅台"))
        session.add(StockDaily(code="600519", name="贵州茅台", trade_date="2026-09-25", close=10.0, change_pct=0.0))


@pytest.fixture
def config(tmp_path, monkeypatch):
    path = str(tmp_path / "prev.db")
    _reset_db_engine()
    init_db(path)
    StockSearch.reset()
    monkeypatch.setattr(trading_calendar, "_trade_days", set())
    _seed(path)
    yield {"database": {"sqlite_path": path}, "risk": {}}
    StockSearch.reset()
    _reset_db_engine()


def _file(tmp_path, text=CSV, encoding="gbk", name="x.csv"):
    p = tmp_path / name
    p.write_bytes(text.encode(encoding))
    return str(p)


def _counts(svc):
    return len(svc.trades()), len(svc.corporate_actions())


def _extract_actions(rows):
    """公司行为来自 parse_import_rows -> (成交, 公司行为, 跳过行数, 错误)。"""
    return rp.parse_import_rows(rows)[1]


# ---------- 解析 ----------

@pytest.mark.parametrize("encoding", ["gbk", "utf-8-sig"])
def test_parse_actions(tmp_path, encoding):
    rows = read_rows(_file(tmp_path, encoding=encoding))
    trades, actions_, skipped, error = rp.parse_import_rows(rows)
    assert error == "" and len(trades) == 1 and trades[0]["side"] == "buy"
    assert parse_trade_rows(rows)[0] == trades                      # 向后兼容
    actions = _extract_actions(rows)
    by = {a["action"]: a for a in actions}
    assert set(by) == {"dividend", "bonus", "tax"}
    assert by["dividend"]["cash"] == pytest.approx(300.0) and by["dividend"]["code"] == "600519"
    assert by["bonus"]["shares"] == 500                              # 取绝对值
    assert by["tax"]["cash"] == pytest.approx(30.0)
    assert by["dividend"]["ex_date"] == "2026-09-10"
    assert skipped >= 1                                              # 银证转账跳过


@pytest.mark.parametrize("label,kind", [
    ("红利入账", "dividend"), ("股息入账", "dividend"), ("红利发放", "dividend"), ("派息", "dividend"),
    ("红股入账", "bonus"), ("送股", "bonus"), ("转增", "bonus"),
    ("股息红利税补缴", "tax"), ("红利税", "tax"), ("扣税", "tax"),
])
def test_parse_action_labels(label, kind):
    amount, qty = ("-100.00", "0") if kind != "bonus" else ("0", "-200")
    rows = [HEADER.split(","), ["20260910", "15:00:00", "600519", "贵州茅台", label, qty, "0", amount, "0", "0", "0", ""]]
    actions = _extract_actions(rows)
    assert len(actions) == 1 and actions[0]["action"] == kind


def test_parse_bom_header():
    header = ["﻿成交日期"] + HEADER.split(",")[1:]
    rows = [header, ["20260910", "15:00:00", "600519", "贵州茅台", "红利入账", "0", "0", "300", "0", "0", "0", ""]]
    error = rp.parse_import_rows(rows)[3]
    assert error == ""
    assert len(_extract_actions(rows)) == 1


# ---------- 预览与导入 ----------

def test_preview_does_not_write(config, tmp_path):
    svc = RealPortfolioService(config)
    path = _file(tmp_path)
    before = _counts(svc)
    result = svc.preview_import(path)
    assert _counts(svc) == before == (0, 0)
    for key in ("format", "trades", "actions", "new_trades", "new_actions", "duplicates", "skipped", "warnings"):
        assert key in result
    assert result["new_trades"] == 1 and result["new_actions"] == 3 and result["duplicates"] == 0
    assert len(result["trades"]) == 1 and len(result["actions"]) == 3
    assert result["skipped"] >= 1


def test_import_then_preview_all_duplicates(config, tmp_path):
    svc = RealPortfolioService(config)
    path = _file(tmp_path)
    first = svc.import_file(path)
    assert first["added"] == 1 and first["actions_added"] == 3 and not first.get("error")
    assert _counts(svc) == (1, 3)
    preview = svc.preview_import(path)
    assert preview["new_trades"] == 0 and preview["new_actions"] == 0 and preview["duplicates"] > 0
    second = svc.import_file(path)
    assert second["added"] == 0 and second["actions_added"] == 0 and second["duplicate"] >= 1
    assert _counts(svc) == (1, 3)


def test_import_effect_on_position(config, tmp_path):
    svc = RealPortfolioService(config)
    svc.import_file(_file(tmp_path))
    pos = svc.positions()[0]
    assert pos["quantity"] == 1500
    # 成本 10000 - 300(分红) + 30(补税)
    assert pos["avg_cost"] == pytest.approx((10000 - 300 + 30) / 1500, abs=1e-3)


def test_preview_error_file(config, tmp_path):
    svc = RealPortfolioService(config)
    result = svc.preview_import(_file(tmp_path, "日期,摘要\n20260901,转账\n"))
    assert result.get("error") or result.get("warnings") or result["new_trades"] == 0
    assert _counts(svc) == (0, 0)


# ---------- API ----------

@pytest.fixture
def env(tmp_path, monkeypatch):
    path = str(tmp_path / "api.db")
    _reset_db_engine()
    init_db(path)
    StockSearch.reset()
    monkeypatch.setattr(trading_calendar, "load", lambda db_path="", refresh=True: True)
    monkeypatch.setattr(settings_store, "SETTINGS_PATH", tmp_path / "settings.yaml")
    _seed(path)
    config = {"database": {"sqlite_path": path}, "web": {}, "risk": {},
              "llm": {"primary": {"provider": "deepseek", "api_key": "sk-secret-1234", "model": "deepseek-chat"},
                      "cache_path": str(tmp_path / "llm.sqlite3")}}
    static = tmp_path / "dist"
    (static / "assets").mkdir(parents=True)
    (static / "index.html").write_text("<html>app</html>", encoding="utf-8")
    app = create_app(config, start_scheduler=False, static_dir=static, auth=AuthStore(tmp_path / "auth.json"))
    with TestClient(app) as client:
        yield client, path
    StockSearch.reset()
    _reset_db_engine()


def _db_counts(path):
    svc = RealPortfolioService({"database": {"sqlite_path": path}})
    return _counts(svc)


def test_api_import_preview_and_commit(env):
    client, path = env
    files = {"file": ("x.csv", CSV.encode("gbk"), "text/csv")}
    preview = client.post("/api/v1/real/trades/import?preview=true", files=files)
    assert preview.status_code == 200
    body = preview.json()
    assert body["new_trades"] == 1 and body["new_actions"] == 3
    assert _db_counts(path) == (0, 0)
    done = client.post("/api/v1/real/trades/import", files={"file": ("x.csv", CSV.encode("gbk"), "text/csv")}).json()
    assert done["added"] == 1 and done["actions_added"] == 3
    assert _db_counts(path) == (1, 3)


def test_api_actions_crud(env):
    client, path = env
    trade = {"trade_date": "2026-09-01", "code": "600519", "side": "buy", "price": 10.0, "quantity": 1000}
    assert client.post("/api/v1/real/trades", json=trade).json() == {"ok": True}
    assert client.get("/api/v1/real/actions").json() == []

    direct = client.post("/api/v1/real/actions", json={"ex_date": "2026-09-10", "code": "600519", "action": "dividend", "cash": 300})
    assert direct.status_code == 200 and direct.json()["ok"] is True
    plan = client.post("/api/v1/real/actions", json={"ex_date": "2026-09-11", "code": "600519", "plan": {"bonus_per_10": 5}})
    if plan.status_code == 422:      # 契约未固定 plan 的字段形态：退而尝试平铺
        plan = client.post("/api/v1/real/actions", json={"ex_date": "2026-09-11", "code": "600519", "bonus_per_10": 5})
    assert plan.status_code == 200, plan.text

    rows = client.get("/api/v1/real/actions").json()
    assert [r["ex_date"] for r in rows] == ["2026-09-11", "2026-09-10"]
    position = client.get("/api/v1/real").json()["snapshot"]["positions"][0]
    assert position["quantity"] == 1500 and position["avg_cost"] == pytest.approx(9700 / 1500, abs=1e-3)

    assert client.post("/api/v1/real/actions", json={"ex_date": "2026-09-10", "code": "600519", "action": "bonus", "shares": 0}).status_code in (400, 422)
    assert client.post("/api/v1/real/actions", json={"ex_date": "bad", "code": "600519", "action": "dividend", "cash": 1}).status_code in (400, 422)
    assert client.post("/api/v1/real/actions", json={"ex_date": "2026-09-10", "code": "600519", "action": "xx", "cash": 1}).status_code in (400, 422)
    assert client.post("/api/v1/real/actions", json={"ex_date": "2026-08-10", "code": "600519", "plan": {"cash_per_10": 3}}).status_code == 400

    assert client.delete(f"/api/v1/real/actions/{rows[0]['id']}").status_code == 200
    assert client.delete("/api/v1/real/actions/99999").status_code == 404
    assert client.get("/api/v1/real").json()["snapshot"]["positions"][0]["quantity"] == 1000
