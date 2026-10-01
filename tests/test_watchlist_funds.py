"""ETF 与指数加入自选股：解析、增删、行情总览、决策仪表盘、--stocks、盘中提醒、图片导入、API。"""

from __future__ import annotations

import sys
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

import main as main_mod
from api.app import create_app
from api.auth import AuthStore
from src import settings_store, trading_calendar
from src.database import db as db_module
from src.database.db import get_db_session, init_db
from src.database.models import FundDaily, FundInfo, StockDaily, StockInfo, Watchlist
from src.services import image_import
from src.services.alert_service import AlertService
from src.services.chat_tools import ChatTools
from src.services.fund_diagnosis import FundDiagnosisService
from src.services.stock_diagnosis import StockDiagnosisService
from src.services.stock_search import StockSearch
from src.services.watchlist import WatchlistService
from src.services.watchlist_report import WatchlistReportService


def _reset_db_engine():
    if db_module._engine:
        db_module._engine.dispose()
    db_module._engine = None
    db_module._SessionFactory = None


def _seed(path: str):
    with get_db_session(path) as session:
        session.add(StockInfo(code="000001", name="平安银行"))
        session.add(StockInfo(code="600519", name="贵州茅台"))
        session.add(StockDaily(code="000001", name="平安银行", trade_date="2026-09-25", close=11.0, change_pct=0.5))
        session.add(StockDaily(code="600519", name="贵州茅台", trade_date="2026-09-25", close=1500.0, change_pct=1.0))
        session.add(FundInfo(code="510300", name="沪深300ETF", kind="etf", exchange="sh", updated_at=datetime.now()))
        for code, name, close, pct in (("510300", "沪深300ETF", 4.05, 0.8), ("sh000300", "沪深300", 4006.0, 0.6),
                                       ("sh000001", "上证指数", 3300.0, -0.2)):
            session.add(FundDaily(code=code, name=name, trade_date="2026-09-24", open=close, high=close, low=close,
                                  close=close - 1, volume=1e8, amount=1e9, change_pct=0.1))
            session.add(FundDaily(code=code, name=name, trade_date="2026-09-25", open=close, high=close, low=close,
                                  close=close, volume=1e8, amount=1e9, change_pct=pct))


@pytest.fixture
def config(tmp_path):
    path = str(tmp_path / "wlf.db")
    _reset_db_engine()
    init_db(path)
    StockSearch.reset()
    _seed(path)
    yield {"database": {"sqlite_path": path}, "watchlist": {"max_stocks": 20}}
    StockSearch.reset()
    _reset_db_engine()


@pytest.fixture
def service(config):
    return WatchlistService(config)


# ---------- resolve ----------

def test_resolve_funds_and_stock_priority(service):
    assert service.resolve("510300") == ("510300", "沪深300ETF")
    assert service.resolve("沪深300ETF") == ("510300", "沪深300ETF")
    assert service.resolve("沪深300") == ("sh000300", "沪深300")
    assert service.resolve("sh000001") == ("sh000001", "上证指数")
    assert service.resolve("上证指数") == ("sh000001", "上证指数")
    assert service.resolve("000001") == ("000001", "平安银行")   # 裸 6 位数字优先当个股
    assert service.resolve("600519") == ("600519", "贵州茅台")
    assert service.resolve("pingan-not-exist") is None


def test_resolve_include_funds_false_keeps_old_behavior(service):
    for text in ("510300", "沪深300", "sh000300", "sh000001", "上证指数"):
        assert service.resolve(text, include_funds=False) is None
    assert service.resolve("000001", include_funds=False) == ("000001", "平安银行")
    assert service.resolve("贵州茅台", include_funds=False) == ("600519", "贵州茅台")


def test_resolve_blank(service):
    assert service.resolve("") is None and service.resolve("   ") is None


# ---------- add / list / remove ----------

def test_add_list_remove_funds_keep_prefix(service):
    assert service.add("510300") == {"ok": True, "code": "510300", "name": "沪深300ETF"}
    assert service.add("沪深300") == {"ok": True, "code": "sh000300", "name": "沪深300"}
    assert service.add("sh000001")["code"] == "sh000001"
    assert service.add("000001")["code"] == "000001"     # 同数字的个股与上证指数可共存
    assert service.codes() == ["510300", "sh000300", "sh000001", "000001"]
    kinds = {r["code"]: r["kind"] for r in service.list()}
    assert kinds == {"510300": "etf", "sh000300": "index", "sh000001": "index", "000001": "stock"}
    assert service.contains("sh000300") and service.contains("510300") and service.contains("sh000001")
    assert service.contains("000001")

    # 重复添加报错（含不同写法）
    assert "已在自选股中" in service.add("sh000300")["error"]
    assert "已在自选股中" in service.add("沪深300")["error"]
    assert "已在自选股中" in service.add("510300")["error"]

    # 删除指数不会误删同数字的个股，反之亦然
    assert service.remove("sh000001")
    assert service.codes() == ["510300", "sh000300", "000001"]
    assert not service.remove("sh000001")
    assert service.remove("000001")
    assert service.codes() == ["510300", "sh000300"]
    assert service.remove("510300") and service.remove("sh000300")
    assert service.codes() == []


def test_list_kind_for_unknown_fund_row_is_stock(service, config):
    """表里只有个股时 kind 为 stock。"""
    service.add("600519")
    assert service.list()[0]["kind"] == "stock"


def test_max_limit_applies_to_funds(config):
    svc = WatchlistService({**config, "watchlist": {"max_stocks": 1}})
    assert svc.add("510300")["ok"]
    assert "最多 1 只" in svc.add("sh000300")["error"]


# ---------- 批量导入保持只识别个股 ----------

def test_import_text_ignores_funds(service):
    result = service.import_text("510300 沪深300ETF\n600519 贵州茅台\nsh000300")
    assert result["added"] == ["贵州茅台(600519)"]
    assert "510300" in result["unknown"]
    assert all(code not in service.codes() for code in ("510300", "sh000300"))


# ---------- overview ----------

def test_overview_funds_use_fund_daily_and_fund_diagnosis(service, monkeypatch):
    for text in ("600519", "510300", "沪深300"):
        assert service.add(text)["ok"]
    stock_calls, fund_calls = [], []
    monkeypatch.setattr(StockDiagnosisService, "latest",
                        lambda self, code, max_age_minutes=None: stock_calls.append(code) or {
                            "action": "watch", "action_label": "观望", "score": 55, "created_at": "2026-09-25 16:00", "one_sentence": "个股"})
    monkeypatch.setattr(FundDiagnosisService, "latest",
                        lambda self, code, max_age_minutes=None: fund_calls.append(code) or {
                            "action": "buy", "action_label": "买入", "score": 70, "created_at": "2026-09-25 16:10", "one_sentence": "基金"})
    rows = {r["code"]: r for r in service.overview()}
    assert rows["600519"]["kind"] == "stock" and rows["600519"]["close"] == 1500.0 and rows["600519"]["diagnosis"]["one_sentence"] == "个股"
    assert rows["510300"]["kind"] == "etf" and rows["510300"]["close"] == 4.05 and rows["510300"]["change_pct"] == 0.8
    assert rows["510300"]["trade_date"] == "2026-09-25" and rows["510300"]["diagnosis"]["action_label"] == "买入"
    assert rows["sh000300"]["kind"] == "index" and rows["sh000300"]["close"] == 4006.0
    assert rows["sh000300"]["diagnosis"]["one_sentence"] == "基金"
    assert stock_calls == ["600519"] and sorted(fund_calls) == ["510300", "sh000300"]


def test_overview_fund_without_bars(service, config, monkeypatch):
    with get_db_session(config["database"]["sqlite_path"]) as session:
        session.add(FundInfo(code="159915", name="创业板ETF", kind="etf", exchange="sz", updated_at=datetime.now()))
    StockSearch.reset()
    assert service.add("159915")["ok"]
    monkeypatch.setattr(FundDiagnosisService, "latest", lambda self, code, max_age_minutes=None: None)
    row = service.overview()[0]
    assert row["kind"] == "etf" and row["close"] is None and row["diagnosis"] is None


def test_chat_tool_lists_funds_with_type(service, monkeypatch, config):
    service.add("600519")
    service.add("510300")
    service.add("sh000300")
    monkeypatch.setattr(StockDiagnosisService, "latest", lambda self, code, max_age_minutes=None: None)
    monkeypatch.setattr(FundDiagnosisService, "latest", lambda self, code, max_age_minutes=None: None)
    text = ChatTools(config).call("watchlist", {})
    assert "贵州茅台(600519)" in text and "沪深300ETF(510300)" in text and "沪深300(sh000300)" in text
    assert "ETF" in text.replace("沪深300ETF", "") and "指数" in text


# ---------- 决策仪表盘 ----------

class FakeStockDiagnosis:
    def __init__(self):
        self.diagnosed, self.latest_calls = [], []

    def latest(self, code, max_age_minutes=None):
        self.latest_calls.append(code)
        return None

    def diagnose(self, code, force=False):
        self.diagnosed.append(code)
        return {"code": code, "name": "贵州茅台", "action": "buy", "action_label": "买入", "score": 78,
                "created_at": "2026-09-25 16:30", "one_sentence": "个股结论", "risks": [], "guardrails": [], "catalysts": [], "battle_plan": {}}

    def history(self, code, limit=5):
        return []


def _fund_result(code, name):
    return {"code": code, "name": name, "action": "watch", "action_label": "观望", "score": 60,
            "created_at": "2026-09-25 16:30", "one_sentence": f"{name}结论", "risks": [], "guardrails": [], "catalysts": [], "battle_plan": {}}


def test_dashboard_mixed_stock_etf_index(config, service, monkeypatch):
    for text in ("600519", "510300", "sh000300"):
        assert service.add(text)["ok"]
    fund_diagnosed = []
    names = {"510300": "沪深300ETF", "sh000300": "沪深300"}
    monkeypatch.setattr(FundDiagnosisService, "latest", lambda self, code, max_age_minutes=None: None)
    monkeypatch.setattr(FundDiagnosisService, "history", lambda self, code, limit=5: [])
    monkeypatch.setattr(FundDiagnosisService, "diagnose",
                        lambda self, code, force=False: fund_diagnosed.append(code) or _fund_result(code, names[code]))
    fake = FakeStockDiagnosis()
    result = WatchlistReportService(config, diagnosis=fake).run(push=False, now=datetime(2026, 9, 25, 16, 30))

    assert fake.diagnosed == ["600519"] and all(c == "600519" for c in fake.latest_calls)   # 基金不走个股诊断
    assert sorted(fund_diagnosed) == ["510300", "sh000300"]
    assert (result["total"], result["done"], result["failed"]) == (3, 3, [])
    md = result["markdown"]
    assert "沪深300ETF(510300)" in md and "沪深300(sh000300)" in md and "贵州茅台(600519)" in md
    # 基金行在名称后标注类型
    assert "ETF" in md.replace("沪深300ETF", "") and "指数" in md
    line_etf = next(line for line in md.splitlines() if "510300" in line and "结论" in line)
    line_idx = next(line for line in md.splitlines() if "sh000300" in line and "结论" in line)
    line_stock = next(line for line in md.splitlines() if "600519" in line and "结论" in line)
    assert "ETF" in line_etf.replace("沪深300ETF", "") and "指数" in line_idx
    assert "ETF" not in line_stock and "指数" not in line_stock


def test_dashboard_fund_failure_listed(config, service, monkeypatch):
    service.add("sh000300")
    monkeypatch.setattr(FundDiagnosisService, "latest", lambda self, code, max_age_minutes=None: None)
    monkeypatch.setattr(FundDiagnosisService, "history", lambda self, code, limit=5: [])
    monkeypatch.setattr(FundDiagnosisService, "diagnose", lambda self, code, force=False: {"code": code, "error": "行情不足"})
    result = WatchlistReportService(config, diagnosis=FakeStockDiagnosis()).run(push=False, now=datetime(2026, 9, 25, 16, 30))
    assert result["done"] == 0 and result["failed"] == [{"code": "sh000300", "name": "沪深300", "error": "行情不足"}]


def test_stocks_of_funds_names_and_no_bare_code(config):
    svc = WatchlistReportService(config, diagnosis=FakeStockDiagnosis())
    stocks = svc._stocks_of(["510300", "sh000300", "sh000001", "000001", "sh000300"])
    assert [(x["code"], x["name"]) for x in stocks] == [("510300", "沪深300ETF"), ("sh000300", "沪深300"),
                                                        ("sh000001", "上证指数"), ("000001", "平安银行")]


def test_report_reuses_recent_fund_diagnosis(config, service, monkeypatch):
    service.add("510300")
    calls = []
    monkeypatch.setattr(FundDiagnosisService, "latest", lambda self, code, max_age_minutes=None: _fund_result(code, "沪深300ETF"))
    monkeypatch.setattr(FundDiagnosisService, "history", lambda self, code, limit=5: [])
    monkeypatch.setattr(FundDiagnosisService, "diagnose", lambda self, code, force=False: calls.append(code) or _fund_result(code, "x"))
    result = WatchlistReportService(config, diagnosis=FakeStockDiagnosis()).run(push=False, now=datetime(2026, 9, 25, 16, 30))
    assert calls == [] and result["done"] == 1     # 收盘后已有当天 16:30 诊断，直接复用


# ---------- main --stocks ----------

def test_run_stocks_resolves_funds(config, monkeypatch):
    captured = []
    monkeypatch.setattr(WatchlistReportService, "run",
                        lambda self, push=True, progress=None, now=None, codes=None: captured.append(list(codes)) or
                        {"total": len(codes), "done": len(codes), "failed": [], "pushed": False})
    assert main_mod.run_stocks(config, "510300,沪深300,600519") == 0
    assert captured == [["510300", "sh000300", "600519"]]


def test_run_stocks_parse_and_skip_unknown(config, monkeypatch):
    captured = []
    monkeypatch.setattr(WatchlistReportService, "run",
                        lambda self, push=True, progress=None, now=None, codes=None: captured.append(list(codes)) or
                        {"total": len(codes), "done": len(codes), "failed": [], "pushed": False})
    assert main_mod.run_stocks(config, "sh000001，不存在的东西, 000001") == 0
    assert captured == [["sh000001", "000001"]]
    assert main_mod.run_stocks(config, "不存在的东西") == 2


def test_stocks_help_mentions_funds():
    src = open(main_mod.__file__, encoding="utf-8").read()
    assert "ETF" in src[src.index("--stocks"):src.index("--stocks") + 400]


# ---------- 盘中提醒、图片导入 ----------

def test_alert_watchlist_skips_funds(config, service, monkeypatch):
    for text in ("600519", "510300", "sh000300", "sh000001"):
        service.add(text)
    monkeypatch.setattr(AlertService, "_positions", lambda self: [])
    watch = AlertService({**config, "alerts": {}}).watchlist()
    assert watch == {"600519": "贵州茅台"}


def test_alert_run_does_not_fail_with_fund_watchlist(config, service, monkeypatch):
    service.add("510300")
    service.add("sh000300")
    monkeypatch.setattr(AlertService, "_positions", lambda self: [])
    monkeypatch.setattr(trading_calendar, "in_trade_session", lambda *a, **k: True)
    AlertService({**config, "alerts": {}}).run()   # 不应抛异常


def test_image_import_does_not_add_funds(config):
    class FakeLLM:
        def __init__(self, *a, **k):
            pass

        def chat_vision(self, prompt, images, system_message=""):
            return '{"stocks": [{"code": "510300", "name": "沪深300ETF"}, {"code": "", "name": "沪深300"}, {"code": "600519", "name": "贵州茅台"}]}'

    result = image_import.extract_stocks(b"\x89PNG\r\n\x1a\n" + b"0" * 20, "image/png", config, llm=FakeLLM())
    codes = [c["code"] for c in result["candidates"]]
    assert codes == ["600519"]
    assert result["unresolved"]


# ---------- API ----------

@pytest.fixture
def env(tmp_path, monkeypatch):
    path = str(tmp_path / "api.db")
    _reset_db_engine()
    init_db(path)
    StockSearch.reset()
    monkeypatch.setattr(trading_calendar, "load", lambda db_path="", refresh=True: True)
    monkeypatch.setattr(settings_store, "SETTINGS_PATH", tmp_path / "settings.yaml")
    monkeypatch.setattr(StockDiagnosisService, "latest", lambda self, code, max_age_minutes=None: None)
    monkeypatch.setattr(FundDiagnosisService, "latest", lambda self, code, max_age_minutes=None: None)
    _seed(path)
    config = {"database": {"sqlite_path": path}, "web": {}, "risk": {}, "trading": {}}
    static = tmp_path / "dist"
    static.mkdir()
    (static / "index.html").write_text("<html></html>", encoding="utf-8")
    app = create_app(config, start_scheduler=False, static_dir=static, auth=AuthStore(tmp_path / "auth.json"))
    with TestClient(app) as client:
        yield client, path
    StockSearch.reset()
    _reset_db_engine()


def test_api_add_funds_and_kind(env):
    client, path = env
    assert client.post("/api/v1/watchlist", json={"text": "510300"}).json() == {"ok": True, "code": "510300", "name": "沪深300ETF"}
    assert client.post("/api/v1/watchlist", json={"text": "沪深300"}).json() == {"ok": True, "code": "sh000300", "name": "沪深300"}
    assert client.post("/api/v1/watchlist", json={"text": "600519"}).json()["ok"] is True
    dup = client.post("/api/v1/watchlist", json={"text": "sh000300"}).json()
    assert dup["ok"] is False and "已在自选股中" in dup["error"]

    rows = {r["code"]: r for r in client.get("/api/v1/watchlist").json()}
    assert {c: r["kind"] for c, r in rows.items()} == {"510300": "etf", "sh000300": "index", "600519": "stock"}
    assert rows["sh000300"]["close"] == 4006.0 and rows["510300"]["close"] == 4.05

    assert client.delete("/api/v1/watchlist/sh000300").json() == {"ok": True}
    assert sorted(r["code"] for r in client.get("/api/v1/watchlist").json()) == ["510300", "600519"]
    with get_db_session(path) as session:
        assert session.query(Watchlist).count() == 2


def test_api_import_text_skips_funds(env):
    client, _ = env
    result = client.post("/api/v1/watchlist/import", json={"text": "510300\n600519"}).json()
    assert result["added"] == ["贵州茅台(600519)"]
    assert "510300" in result["unknown"]


def test_entrypoint_argv_not_polluted(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["main.py"])
    assert main_mod.parse_args(["--stocks", "510300,沪深300"]).stocks == "510300,沪深300"
