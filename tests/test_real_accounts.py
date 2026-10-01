"""实盘记账多账户：数据隔离、汇总、导入去重、账户管理、索引迁移、组合风险与 API。"""

from __future__ import annotations

import sqlite3
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from src import settings_store, trading_calendar
from src.database import db as db_module
from src.database.db import get_db_session, init_db
from src.database.models import (
    RealAccount, RealCash, RealCorporateAction, RealPositionPlan, RealTrade, StockDaily, StockInfo,
)
from src.services.portfolio_risk import PortfolioRiskService
from src.services.real_portfolio import DEFAULT_ACCOUNT, RealPortfolioService
from src.services.stock_search import StockSearch
from api.app import create_app
from api.auth import AuthStore

CODE = "600519"
OTHER = "000001"
HEADER = "成交日期,成交时间,证券代码,证券名称,业务名称,成交数量,成交均价,成交金额,佣金,印花税,过户费,成交编号"
CSV = "\n".join([
    HEADER,
    "20260901,09:31:05,600519,贵州茅台,证券买入,1000,10.00,10000.00,0,0,0,A001",
    "20260910,15:00:00,600519,贵州茅台,红利入账,0,0,300.00,0,0,0,",
]) + "\n"


def _reset_db_engine():
    if db_module._engine:
        db_module._engine.dispose()
    db_module._engine = None
    db_module._SessionFactory = None


@pytest.fixture
def config(tmp_path, monkeypatch):
    path = str(tmp_path / "acc.db")
    _reset_db_engine()
    init_db(path)
    StockSearch.reset()
    monkeypatch.setattr(trading_calendar, "_trade_days", set())
    with get_db_session(path) as session:
        for code, name in ((CODE, "贵州茅台"), (OTHER, "平安银行")):
            session.add(StockInfo(code=code, name=name))
            session.add(StockDaily(code=code, name=name, trade_date="2026-09-25", close=10.0, change_pct=0.0))
    yield {"database": {"sqlite_path": path}, "risk": {"stop_loss_pct": -0.05, "take_profit_pct": 0.15}}
    StockSearch.reset()
    _reset_db_engine()


def _svc(config, account=None):
    return RealPortfolioService(config, account=account)


def _two(config):
    """两个账户：招商（茅台 1000@10）、华泰（茅台 500@16，平安 300@8）。"""
    base = _svc(config)
    assert base.add_account("招商", broker="招商证券")["ok"]
    assert base.add_account("华泰")["ok"]
    assert _svc(config, "招商").add_trade("2026-09-01", CODE, "buy", 10.0, 1000)["ok"]
    assert _svc(config, "华泰").add_trade("2026-09-02", CODE, "buy", 16.0, 500)["ok"]
    assert _svc(config, "华泰").add_trade("2026-09-02", OTHER, "buy", 8.0, 300)["ok"]


def _csv(tmp_path, text=CSV):
    p = tmp_path / "x.csv"
    p.write_bytes(text.encode("gbk"))
    return str(p)


# ---------- 旧数据与默认账户 ----------

def test_legacy_null_account_is_default(config):
    path = config["database"]["sqlite_path"]
    with get_db_session(path) as session:
        session.add(RealTrade(trade_date="2026-09-01", code=CODE, name="贵州茅台", side="buy", price=10.0, quantity=1000))
        session.add(RealCash(cash=5000.0, as_of=datetime(2026, 9, 25, 9, 0)))
        session.add(RealPositionPlan(code=CODE, stop_loss=9.0, target_price=12.0))
        session.add(RealCorporateAction(code=CODE, ex_date="2026-09-26", action="dividend", cash=100.0))
    default = _svc(config, DEFAULT_ACCOUNT)
    assert DEFAULT_ACCOUNT == "默认"
    assert default.positions()[0]["quantity"] == 1000
    assert default.positions()[0]["stop_loss"] == 9.0
    assert default.cash() == pytest.approx(5100.0)
    assert len(default.trades()) == 1 and len(default.corporate_actions()) == 1
    assert _svc(config).positions()[0]["quantity"] == 1000                  # None 汇总也含旧数据
    _svc(config).add_account("招商")
    assert _svc(config, "招商").positions() == [] and _svc(config, "招商").cash() is None


def test_default_account_listed_first(config):
    svc = _svc(config)
    assert [a["name"] for a in svc.accounts()] == ["默认"]
    svc.add_account("招商")
    svc.add_account("华泰")
    accounts = svc.accounts()
    assert accounts[0]["name"] == "默认" and {a["name"] for a in accounts} == {"默认", "招商", "华泰"}
    for key in ("name", "broker", "note", "trades", "positions", "market_value", "cash"):
        assert key in accounts[0]


def test_write_without_account_goes_to_default(config):
    svc = _svc(config)
    assert svc.add_trade("2026-09-01", CODE, "buy", 10.0, 100)["ok"]
    with get_db_session(config["database"]["sqlite_path"]) as session:
        assert session.query(RealTrade).one().account == "默认"
    assert _svc(config, "默认").positions()[0]["quantity"] == 100


# ---------- 隔离与汇总 ----------

def test_accounts_isolated(config):
    _two(config)
    zs, ht, default = _svc(config, "招商"), _svc(config, "华泰"), _svc(config, "默认")
    assert [p["quantity"] for p in zs.positions()] == [1000]
    assert {p["code"]: p["quantity"] for p in ht.positions()} == {CODE: 500, OTHER: 300}
    assert default.positions() == [] and default.trades() == []
    assert len(zs.trades()) == 1 and len(ht.trades()) == 2
    # 一个账户不能卖出另一个账户的持仓
    assert _svc(config, "招商").add_trade("2026-09-10", OTHER, "sell", 9.0, 100)["ok"] is False
    assert zs.add_trade("2026-09-10", CODE, "sell", 12.0, 600)["ok"]
    assert {p["code"]: p["quantity"] for p in ht.positions()} == {CODE: 500, OTHER: 300}
    # 现金锚点互不影响
    as_of = datetime(2026, 9, 25, 9, 0)
    zs.set_cash(1000.0, as_of=as_of)
    assert zs.cash() == 1000.0 and ht.cash() is None and default.cash() is None
    ht.set_cash(2000.0, as_of=as_of)
    assert zs.cash() == 1000.0 and ht.cash() == 2000.0
    # 计划互不影响
    zs.set_plan(CODE, 8.0, 20.0)
    assert zs.positions()[0]["stop_loss"] == 8.0
    ht_code = {p["code"]: p for p in ht.positions()}[CODE]
    assert ht_code["stop_loss"] == round(16.0 * 0.95, 2)
    # 公司行为互不影响
    assert zs.add_corporate_action("2026-09-10", CODE, "bonus", shares=100)["ok"]
    assert len(zs.corporate_actions()) == 1 and ht.corporate_actions() == []
    assert zs.positions()[0]["quantity"] == 500                              # 1000-600+100
    assert {p["code"]: p["quantity"] for p in ht.positions()}[CODE] == 500


def test_aggregate_positions(config):
    _two(config)
    rows = {p["code"]: p for p in _svc(config).positions()}
    merged = rows[CODE]
    assert merged["quantity"] == 1500
    assert merged["avg_cost"] == pytest.approx((10 * 1000 + 16 * 500) / 1500, abs=1e-3)
    assert sorted(merged["accounts"]) == ["华泰", "招商"]
    assert merged["market_value"] == pytest.approx(15000)
    assert rows[OTHER]["quantity"] == 300 and rows[OTHER]["accounts"] == ["华泰"]
    # 单账户视图：accounts 为该账户
    assert _svc(config, "招商").positions()[0]["accounts"] == ["招商"]
    # 未设置计划：按合并成本计算
    assert merged["stop_loss"] == pytest.approx(round(merged["avg_cost"] * 0.95, 2))
    # 其中一个账户设置计划：取设置了计划的账户的值
    _svc(config, "华泰").set_plan(CODE, 12.5, 30.0)
    merged = {p["code"]: p for p in _svc(config).positions()}[CODE]
    assert merged["stop_loss"] == 12.5 and merged["target_price"] == 30.0


def test_aggregate_realized_pnl(config):
    _two(config)
    assert _svc(config, "招商").add_trade("2026-09-10", CODE, "sell", 12.0, 500)["ok"]   # 盈利 1000
    assert _svc(config, "华泰").add_trade("2026-09-10", CODE, "sell", 14.0, 100)["ok"]   # 亏损 200
    total = _svc(config).snapshot()["account"]["realized_pnl"]
    parts = sum(_svc(config, a).snapshot()["account"]["realized_pnl"] for a in ("招商", "华泰"))
    assert total == pytest.approx(parts) == pytest.approx(800.0)


def test_aggregate_cash_and_cash_known(config):
    _two(config)
    as_of = datetime(2026, 9, 25, 9, 0)
    _svc(config, "招商").set_cash(1000.0, as_of=as_of)
    snap = _svc(config).snapshot()["account"]
    assert snap["cash_known"] is False                                      # 华泰有成交但没设置资金
    _svc(config, "华泰").set_cash(2500.0, as_of=as_of)
    snap = _svc(config).snapshot()["account"]
    assert snap["cash_known"] is True and snap["cash"] == pytest.approx(3500.0)
    assert _svc(config).cash() == pytest.approx(3500.0)
    assert snap["total_assets"] == pytest.approx(3500.0 + snap["market_value"])
    # 没有成交的账户未设置资金不影响 cash_known
    _svc(config).add_account("空账户")
    assert _svc(config).snapshot()["account"]["cash_known"] is True


def test_aggregate_trades_actions_have_account(config):
    _two(config)
    _svc(config, "招商").add_corporate_action("2026-09-10", CODE, "dividend", cash=50.0)
    trades = _svc(config).trades()
    assert len(trades) == 3 and {t["account"] for t in trades} == {"招商", "华泰"}
    actions = _svc(config).corporate_actions()
    assert len(actions) == 1 and actions[0]["account"] == "招商"
    assert _svc(config, "华泰").corporate_actions() == []
    assert len(_svc(config).fills()) == 3
    assert len(_svc(config, "招商").fills()) == 1


# ---------- 导入去重 ----------

def test_import_dedup_per_account(config, tmp_path):
    path = _csv(tmp_path)
    _svc(config).add_account("招商")
    first = _svc(config).import_file(path)
    assert first["added"] == 1 and first["actions_added"] == 1
    again = _svc(config).import_file(path)
    assert again["added"] == 0 and again["duplicate"] >= 1                  # 默认账户重复导入只入一次
    assert len(_svc(config, "默认").trades()) == 1
    other = _svc(config, "招商")
    preview = other.preview_import(path)
    assert preview["new_trades"] == 1 and preview["new_actions"] == 1 and preview["duplicates"] == 0
    result = other.import_file(path)                                        # 不被默认账户的记录挡住
    assert result["added"] == 1 and result["actions_added"] == 1
    assert other.import_file(path)["added"] == 0                            # 该账户自己重复仍去重
    assert len(_svc(config, "招商").trades()) == 1 and len(_svc(config, "默认").trades()) == 1
    assert len(_svc(config).trades()) == 2
    with get_db_session(config["database"]["sqlite_path"]) as session:
        keys = {t.account: t.import_key for t in session.query(RealTrade).all()}
    assert keys["招商"].startswith("招商|")
    assert not keys["默认"].startswith("默认|") and "|" not in keys["默认"].split("|", 1)[0]


def test_default_import_key_format_unchanged(config, tmp_path):
    """已有数据（无前缀 import_key）仍能挡住默认账户的重复导入。"""
    path = _csv(tmp_path)
    from src.services.real_portfolio import parse_import_rows, read_rows

    trades = parse_import_rows(read_rows(path))[0]
    with get_db_session(config["database"]["sqlite_path"]) as session:
        session.add(RealTrade(**trades[0], source="import"))                # 旧数据：account 为 NULL
    assert _svc(config).import_file(path)["added"] == 0


# ---------- 账户管理 ----------

@pytest.mark.parametrize("name", ["", "   ", "全部", "x" * 31, "默认"])
def test_add_account_rejects(config, name):
    result = _svc(config).add_account(name)
    assert result["ok"] is False and result["error"]
    assert [a["name"] for a in _svc(config).accounts()] == ["默认"]


def test_add_account_ok_and_duplicate(config):
    svc = _svc(config)
    assert svc.add_account("x" * 30)["ok"] is True
    assert svc.add_account("招商", broker="招商证券", note="主账户")["ok"] is True
    dup = svc.add_account("招商")
    assert dup["ok"] is False and dup["error"]
    row = next(a for a in svc.accounts() if a["name"] == "招商")
    assert row["broker"] == "招商证券" and row["note"] == "主账户" and row["trades"] == 0


def test_accounts_stats(config):
    _two(config)
    _svc(config, "华泰").set_cash(100.0, as_of=datetime(2026, 9, 25, 9, 0))
    rows = {a["name"]: a for a in _svc(config).accounts()}
    assert rows["招商"]["trades"] == 1 and rows["招商"]["positions"] == 1 and rows["招商"]["market_value"] == pytest.approx(10000)
    assert rows["招商"]["cash"] is None
    assert rows["华泰"]["trades"] == 2 and rows["华泰"]["positions"] == 2 and rows["华泰"]["cash"] is not None
    assert rows["默认"]["trades"] == 0 and rows["默认"]["positions"] == 0


def test_rename_account_updates_all_tables(config):
    _two(config)
    zs = _svc(config, "招商")
    zs.set_cash(1000.0)
    zs.set_plan(CODE, 8.0, 20.0)
    zs.add_corporate_action("2026-09-10", CODE, "dividend", cash=50.0)
    result = _svc(config).rename_account("招商", "招商新")
    assert result["ok"] is True
    with get_db_session(config["database"]["sqlite_path"]) as session:
        for model in (RealTrade, RealCash, RealPositionPlan, RealCorporateAction):
            accounts = {r.account for r in session.query(model).all()}
            assert "招商" not in accounts and "招商新" in accounts, model.__name__
        assert session.query(RealAccount).filter_by(name="招商").count() == 0
        assert session.query(RealAccount).filter_by(name="招商新").count() == 1
    assert _svc(config, "招商新").positions()[0]["quantity"] == 1000
    assert _svc(config, "招商").positions() == []


def test_rename_account_rejects(config):
    _two(config)
    svc = _svc(config)
    assert svc.rename_account("默认", "主账户")["ok"] is False
    assert svc.rename_account("招商", "默认")["ok"] is False
    assert svc.rename_account("招商", "华泰")["ok"] is False                 # 重名
    assert svc.rename_account("招商", "")["ok"] is False
    assert svc.rename_account("招商", "全部")["ok"] is False
    assert svc.rename_account("招商", "y" * 31)["ok"] is False
    assert svc.rename_account("不存在", "新名")["ok"] is False
    assert _svc(config, "招商").positions()[0]["quantity"] == 1000


def test_delete_account(config):
    _two(config)
    svc = _svc(config)
    assert svc.delete_account("默认")["ok"] is False
    refused = svc.delete_account("招商")
    assert refused["ok"] is False and refused["error"]                      # 有成交
    assert any(a["name"] == "招商" for a in svc.accounts())
    assert svc.add_account("空账户")["ok"]
    assert svc.delete_account("空账户")["ok"] is True
    assert all(a["name"] != "空账户" for a in svc.accounts())
    assert svc.delete_account("空账户")["ok"] is False                      # 已不存在


def test_delete_account_with_only_corporate_action_refused(config):
    svc = _svc(config)
    svc.add_account("A")
    with get_db_session(config["database"]["sqlite_path"]) as session:
        session.add(RealCorporateAction(code=CODE, ex_date="2026-09-10", action="dividend", cash=10.0, account="A"))
    assert svc.delete_account("A")["ok"] is False


# ---------- 索引迁移 ----------

def test_plan_index_migration(config):
    path = config["database"]["sqlite_path"]
    with get_db_session(path) as session:
        session.add(RealPositionPlan(code=CODE, stop_loss=9.0, target_price=12.0, account=None))   # 旧数据
    _reset_db_engine()
    conn = sqlite3.connect(path)
    conn.execute("DROP INDEX IF EXISTS idx_real_position_plan_account_code")
    conn.execute("CREATE UNIQUE INDEX idx_real_position_plan_code ON real_position_plan(code)")
    conn.commit()
    conn.close()
    init_db(path)
    conn = sqlite3.connect(path)
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='real_position_plan'")}
    conn.close()
    assert "idx_real_position_plan_code" not in names and "idx_real_position_plan_account_code" in names
    svc = _svc(config)
    svc.add_account("招商")
    _svc(config, "招商").set_plan(CODE, 8.0, 20.0)
    _svc(config, "华泰").set_plan(CODE, 7.0, 21.0)
    with get_db_session(path) as session:
        assert session.query(RealPositionPlan).filter_by(code=CODE).count() == 3
    _reset_db_engine()
    init_db(path)                                                           # 幂等：再次迁移不报错
    _svc(config, "招商").set_plan(CODE, 8.5, 20.0)                          # 同账户同代码更新而不是重复
    with get_db_session(path) as session:
        assert session.query(RealPositionPlan).filter_by(code=CODE, account="招商").count() == 1


def test_set_plan_twice_same_account_updates(config):
    svc = _svc(config, "默认")
    svc.set_plan(CODE, 8.0, 20.0)
    svc.set_plan(CODE, 9.0, 21.0)
    with get_db_session(config["database"]["sqlite_path"]) as session:
        rows = [(r.account, r.stop_loss) for r in session.query(RealPositionPlan).all()]
    assert rows == [("默认", 9.0)]


# ---------- 组合风险 ----------

def test_portfolio_risk_real_and_account(config, monkeypatch):
    _two(config)
    monkeypatch.setattr(PortfolioRiskService, "_regime_limit", lambda self: ("均衡", 60.0))
    allr = PortfolioRiskService(config, account="real").report()
    one = PortfolioRiskService(config, account="real:招商").report()
    other = PortfolioRiskService(config, account="real:华泰").report()
    assert allr["account"] == "real" and one["account"] == "real:招商" and other["account"] == "real:华泰"
    assert one["total_assets"] == pytest.approx(10000)
    assert other["total_assets"] == pytest.approx(8000)
    assert allr["total_assets"] == pytest.approx(one["total_assets"] + other["total_assets"])
    assert one["drawdown"]["days"] >= 1
    empty = PortfolioRiskService(config, account="real:默认").report()
    assert empty["account"] == "real:默认" and empty["total_assets"] == 0


# ---------- API ----------

@pytest.fixture
def env(tmp_path, monkeypatch):
    path = str(tmp_path / "api.db")
    _reset_db_engine()
    init_db(path)
    StockSearch.reset()
    monkeypatch.setattr(trading_calendar, "load", lambda db_path="", refresh=True: True)
    monkeypatch.setattr(settings_store, "SETTINGS_PATH", tmp_path / "settings.yaml")
    monkeypatch.setattr(PortfolioRiskService, "_regime_limit", lambda self: ("均衡", 60.0))
    with get_db_session(path) as session:
        for code, name in {CODE: "贵州茅台", OTHER: "平安银行"}.items():
            session.add(StockInfo(code=code, name=name))
            session.add(StockDaily(code=code, name=name, trade_date="2026-09-25", close=10.0, change_pct=1.0))
    config = {"database": {"sqlite_path": path}, "web": {}, "risk": {}, "llm": {"cache_path": str(tmp_path / "llm.sqlite3")}}
    static = tmp_path / "dist"
    static.mkdir()
    (static / "index.html").write_text("<html>app</html>", encoding="utf-8")
    app = create_app(config, start_scheduler=False, static_dir=static, auth=AuthStore(tmp_path / "auth.json"))
    with TestClient(app) as client:
        yield client, tmp_path
    StockSearch.reset()
    _reset_db_engine()


P = "/api/v1"


def _trade(client, account=None, code=CODE, qty=100, price=10.0):
    body = {"trade_date": "2026-09-01", "code": code, "side": "buy", "price": price, "quantity": qty}
    if account is not None:
        body["account"] = account
    return client.post(f"{P}/real/trades", json=body)


def test_api_accounts_crud(env):
    client, _ = env
    rows = client.get(f"{P}/real/accounts").json()
    assert [a["name"] for a in rows] == ["默认"]
    assert client.post(f"{P}/real/accounts", json={"name": "招商", "broker": "招商证券", "note": "n"}).status_code == 200
    assert client.post(f"{P}/real/accounts", json={"name": "招商"}).status_code == 400
    assert client.post(f"{P}/real/accounts", json={"name": ""}).status_code == 400
    assert client.post(f"{P}/real/accounts", json={"name": "全部"}).status_code == 400
    assert client.post(f"{P}/real/accounts", json={"name": "x" * 31}).status_code == 400
    assert [a["name"] for a in client.get(f"{P}/real/accounts").json()] == ["默认", "招商"]
    assert client.put(f"{P}/real/accounts/招商", json={"name": "招商新", "broker": "b", "note": ""}).status_code == 200
    assert client.put(f"{P}/real/accounts/默认", json={"name": "主"}).status_code == 400
    assert client.put(f"{P}/real/accounts/不存在", json={"name": "新"}).status_code == 400
    assert client.delete(f"{P}/real/accounts/默认").status_code == 400
    assert _trade(client, "招商新").status_code == 200
    assert client.delete(f"{P}/real/accounts/招商新").status_code == 400
    assert client.post(f"{P}/real/accounts", json={"name": "空"}).status_code == 200
    assert client.delete(f"{P}/real/accounts/空").status_code == 200


def test_api_real_account_param(env):
    client, _ = env
    client.post(f"{P}/real/accounts", json={"name": "招商"})
    assert _trade(client, "招商", qty=300).status_code == 200
    assert _trade(client, None, code=OTHER, qty=200).status_code == 200      # 空为默认账户
    allr = client.get(f"{P}/real").json()
    assert {p["code"] for p in allr["snapshot"]["positions"]} == {CODE, OTHER}
    assert allr["risk"]["account"] == "real"
    zs = client.get(f"{P}/real", params={"account": "招商"}).json()
    assert [p["code"] for p in zs["snapshot"]["positions"]] == [CODE]
    assert {t["account"] for t in zs["trades"]} == {"招商"}
    assert zs["risk"]["account"] == "real:招商"
    default = client.get(f"{P}/real", params={"account": "默认"}).json()
    assert [p["code"] for p in default["snapshot"]["positions"]] == [OTHER]
    assert client.get(f"{P}/real", params={"account": ""}).json()["risk"]["account"] == "real"


def test_api_cash_plan_actions_per_account(env):
    client, _ = env
    client.post(f"{P}/real/accounts", json={"name": "招商"})
    _trade(client, "招商", qty=1000)
    assert client.put(f"{P}/real/cash", json={"cash": 5000, "account": "招商"}).status_code == 200
    assert client.put(f"{P}/real/plans/{CODE}", json={"stop_loss": 8.5, "target_price": 13, "account": "招商"}).status_code == 200
    zs = client.get(f"{P}/real", params={"account": "招商"}).json()
    assert zs["snapshot"]["account"]["cash"] == pytest.approx(5000.0) and zs["snapshot"]["account"]["cash_known"] is True
    assert zs["snapshot"]["positions"][0]["stop_loss"] == 8.5
    default = client.get(f"{P}/real", params={"account": "默认"}).json()
    assert default["snapshot"]["account"]["cash_known"] is False
    # 分红送转
    r = client.post(f"{P}/real/actions", json={"ex_date": "2026-09-10", "code": CODE, "action": "dividend", "cash": 100, "account": "招商"})
    assert r.status_code == 200
    assert len(client.get(f"{P}/real/actions", params={"account": "招商"}).json()) == 1
    assert client.get(f"{P}/real/actions", params={"account": "默认"}).json() == []
    assert len(client.get(f"{P}/real/actions").json()) == 1
    assert client.get(f"{P}/real/actions").json()[0]["account"] == "招商"
    r = client.post(f"{P}/real/actions", json={"ex_date": "2026-09-10", "code": CODE, "cash": 0, "account": "默认",
                                               "plan": {"cash_per_10": 1.0, "bonus_per_10": 0.0}})
    assert r.status_code == 400                                              # 默认账户没有持仓


def test_api_import_with_account(env):
    client, tmp = env
    client.post(f"{P}/real/accounts", json={"name": "招商"})
    data = CSV.encode("gbk")

    def up(**params):
        return client.post(f"{P}/real/trades/import", params=params, files={"file": ("x.csv", data, "text/csv")})

    assert up().json()["added"] == 1                                         # 默认账户
    assert up().json()["added"] == 0
    preview = up(account="招商", preview="true").json()
    assert preview["new_trades"] == 1 and preview["duplicates"] == 0
    assert up(preview="true").json()["new_trades"] == 0                      # 默认账户预览：全部重复
    assert up(account="招商").json()["added"] == 1
    assert len(client.get(f"{P}/real", params={"account": "招商"}).json()["trades"]) == 1
    assert len(client.get(f"{P}/real").json()["trades"]) == 2


# ---------- 出入金流水 ----------

def test_cash_flows_ledger_mode(config):
    """没设置可用资金、只记出入金：从零按出入金、成交、分红重放；累计收益 = 总资产 − 净入金"""
    svc = _svc(config)
    assert svc.add_cash_flow("2026-09-01", "in", 100000)["ok"]
    assert svc.add_trade("2026-09-02", CODE, "buy", 10.0, 1000, fee=5)["ok"]
    assert svc.add_trade("2026-09-03", CODE, "sell", 11.0, 500, fee=5)["ok"]
    assert svc.add_cash_flow("2026-09-04", "out", 10000, note="转出")["ok"]
    assert svc.cash() == 100000 - 10005 + 5495 - 10000
    acct = svc.snapshot()["account"]
    assert acct["cash_known"] and acct["ledger_mode"] and acct["net_deposit"] == 90000
    assert acct["total_assets"] == pytest.approx(85490 + 500 * 10.0)
    assert acct["total_return"] == pytest.approx(acct["total_assets"] - 90000)
    rows = svc.cash_flows()
    assert [r["direction_label"] for r in rows] == ["出金", "入金"] and rows[0]["note"] == "转出"   # 按日期倒序


def test_cash_flows_after_anchor(config):
    """设置过可用资金后，只计入设置当天及之后日期的出入金（之前的已包含在设置的金额里）"""
    svc = _svc(config)
    svc.set_cash(50000, as_of=datetime(2026, 9, 10, 14, 0))
    svc.add_cash_flow("2026-09-09", "in", 1000)        # 设置之前：不计
    svc.add_cash_flow("2026-09-10", "in", 2000)        # 设置当天：计入
    svc.add_cash_flow("2026-09-12", "out", 500)
    assert svc.cash() == 50000 + 2000 - 500
    acct = svc.snapshot()["account"]
    assert acct["ledger_mode"] is False and acct["total_return"] is None and acct["net_deposit"] == 2500


def test_cash_flow_validation_accounts_and_delete(config):
    svc = _svc(config)
    assert svc.add_cash_flow("2026/9/1", "in", 100)["ok"]                          # 日期写法宽松
    assert svc.add_cash_flow("昨天", "in", 100) == {"ok": False, "error": "日期格式不对，应为 YYYY-MM-DD"}
    assert not svc.add_cash_flow("2026-09-01", "transfer", 100)["ok"]
    assert svc.add_cash_flow("2026-09-01", "in", 0)["error"] == "金额必须大于 0"
    assert svc.add_account("华泰")["ok"]
    fid = _svc(config, "华泰").add_cash_flow("2026-09-05", "in", 3000)["id"]
    assert _svc(config, "华泰").cash() == 3000 and _svc(config).cash() == 3100       # 全部账户汇总
    assert not _svc(config, DEFAULT_ACCOUNT).delete_cash_flow(fid)                   # 别的账户删不到
    err = svc.delete_account("华泰")["error"]
    assert "1 笔出入金" in err
    assert svc.rename_account("华泰", "华泰证券")["ok"]
    assert _svc(config, "华泰证券").cash_flows()[0]["account"] == "华泰证券"
    assert _svc(config, "华泰证券").delete_cash_flow(fid) and svc.delete_account("华泰证券")["ok"]


def test_api_cash_flows(env):
    client, _ = env
    assert client.post("/api/v1/real/accounts", json={"name": "华泰"}).status_code == 200
    r = client.post("/api/v1/real/cash-flows", json={"flow_date": "2026-09-01", "direction": "in", "amount": 20000, "account": "华泰"})
    assert r.status_code == 200 and r.json()["ok"]
    assert client.post("/api/v1/real/cash-flows", json={"flow_date": "2026-09-01", "direction": "bank", "amount": 1}).status_code == 422
    assert client.post("/api/v1/real/cash-flows", json={"flow_date": "坏日期", "direction": "in", "amount": 1}).status_code == 400
    rows = client.get("/api/v1/real/cash-flows", params={"account": "华泰"}).json()
    assert len(rows) == 1 and rows[0]["direction_label"] == "入金"
    assert client.get("/api/v1/real/cash-flows").json()[0]["account"] == "华泰"                 # 全部账户
    snap = client.get("/api/v1/real", params={"account": "华泰"}).json()["snapshot"]["account"]
    assert snap["cash"] == 20000 and snap["ledger_mode"] and snap["net_deposit"] == 20000
    assert client.delete(f"/api/v1/real/cash-flows/{rows[0]['id']}").json() == {"ok": True}
    assert client.delete(f"/api/v1/real/cash-flows/{rows[0]['id']}").status_code == 404
