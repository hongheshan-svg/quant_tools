"""ETF 与指数：AI 诊断、流水线分派、API、问股工具。离线运行。"""

from __future__ import annotations

import time
from datetime import datetime

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from api.auth import AuthStore
from src import settings_store, trading_calendar
from src.analyzers import llm_client as llm_client_mod
from src.analyzers import market_regime as regime_mod
from src.analyzers.market_regime import MarketRegime
from src.collectors import daily_history as daily_history_mod
from src.collectors import fundamentals as fundamentals_mod
from src.collectors import stock_news as stock_news_mod
from src.database import db as db_module
from src.database.db import get_db_session, init_db
from src.database.models import FundDaily, FundInfo, StockDaily, StockDiagnosis, StockInfo
from src.services.stock_search import StockSearch

DAYS = [d.strftime("%Y-%m-%d") for d in pd.bdate_range(end="2026-09-29", periods=70)]

GOOD_REPLY = {
    "score": 78, "action": "买入", "confidence": "中", "one_sentence": "指数趋势向上，逢低布局",
    "position_advice": {"no_position": "回踩均线低吸", "has_position": "持有"},
    "battle_plan": {"buy_price": None, "stop_loss": None, "target_price": None, "suggested_position": "建议仓位：3成"},
    "catalysts": ["政策预期"], "risks": ["外部扰动"], "checklist": [], "analysis": "均线多头排列。",
}


class _FakeLLM:
    def __init__(self, reply: dict | None = None):
        self.reply = GOOD_REPLY if reply is None else reply
        self.calls: list[str] = []

    def chat_json(self, user_message: str, system_message: str = "", **kwargs) -> dict:
        self.calls.append(user_message)
        return self.reply


def _reset_db_engine():
    if db_module._engine:
        db_module._engine.dispose()
    db_module._engine = None
    db_module._SessionFactory = None


def _seed(path: str):
    with get_db_session(path) as session:
        session.add(StockInfo(code="600519", name="贵州茅台"))
        session.add(StockDaily(code="sh600519", name="贵州茅台", trade_date="2026-09-25", close=1500.0, change_pct=1.0))
        session.add(FundInfo(code="510300", name="沪深300ETF", kind="etf", exchange="sh", updated_at=datetime.now()))
        for i, d in enumerate(DAYS):
            session.add(FundDaily(code="sh000300", name="沪深300", trade_date=d, open=4000 + i * 3, high=4010 + i * 3,
                                  low=3990 + i * 3, close=4002 + i * 3, volume=1e9, amount=3e11, change_pct=0.5))
            session.add(FundDaily(code="510300", name="沪深300ETF", trade_date=d, open=4 + i * 0.01, high=4.1 + i * 0.01,
                                  low=3.9 + i * 0.01, close=4.02 + i * 0.01, volume=1e8, amount=5e8, change_pct=0.3))


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    """所有联网入口置空：日线补齐、个股资料、新闻、大盘环境。"""
    from src.collectors import fund_data

    monkeypatch.setattr(fund_data, "ensure_fund_daily", lambda code, db_path, min_bars=60, **k: 0)
    monkeypatch.setattr(fund_data, "refresh_recent_fund_daily", lambda *a, **k: 0)  # 不依赖当前时间/网络
    monkeypatch.setattr(daily_history_mod, "ensure_daily_history", lambda *a, **k: 0)
    monkeypatch.setattr(fundamentals_mod, "fetch_chip_summary", lambda *a, **k: None)
    monkeypatch.setattr(fundamentals_mod.EarningsCache, "get", classmethod(lambda cls, code: None))
    monkeypatch.setattr(stock_news_mod, "get_stock_news", lambda *a, **k: {"news": [], "notices": []})
    monkeypatch.setattr(regime_mod.MarketRegimeAnalyzer, "analyze",
                        lambda self, **kw: MarketRegime(trade_date="2026-09-29", regime="均衡", position_factor=1.0))
    try:  # 联网新闻搜索按需关闭
        from src.services import fund_diagnosis

        monkeypatch.setattr(fund_diagnosis, "ensure_fund_daily", lambda *a, **k: 0, raising=False)
    except ImportError:
        pass


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "fdiag.db")
    _reset_db_engine()
    init_db(path)
    StockSearch.reset()
    _seed(path)
    yield path
    StockSearch.reset()
    _reset_db_engine()


def _service(db_path: str, reply: dict | None = None):
    from src.services.fund_diagnosis import FundDiagnosisService

    llm = _FakeLLM(reply)
    return FundDiagnosisService({"database": {"sqlite_path": db_path}, "risk": {}, "trading": {}}, llm=llm), llm


# ---------- 服务 ----------

def test_diagnose_index_saves_and_caches(db_path):
    service, llm = _service(db_path)
    result = service.diagnose("sh000300")
    assert not result.get("error"), result
    assert result["kind"] == "index" and result["code"] == "sh000300"
    assert result["action"] == "buy" and result["score"] == 78
    assert result["one_sentence"]
    with get_db_session(db_path) as session:
        rows = session.query(StockDiagnosis).all()
        assert len(rows) == 1 and rows[0].code == "sh000300"

    again = service.diagnose("sh000300")
    assert again.get("cached") is True and len(llm.calls) == 1
    service.diagnose("sh000300", force=True)
    assert len(llm.calls) == 2


def test_diagnose_etf(db_path):
    service, _ = _service(db_path)
    result = service.diagnose("510300")
    assert not result.get("error"), result
    assert result["kind"] == "etf" and result["code"] == "510300"


def test_diagnose_by_name(db_path):
    service, _ = _service(db_path)
    result = service.diagnose("沪深300")
    assert not result.get("error") and result["code"] == "sh000300"


def test_diagnosis_does_not_touch_stock_daily(db_path):
    with get_db_session(db_path) as session:
        before = session.query(StockDaily).count()
    _service(db_path)[0].diagnose("sh000300", force=True)
    with get_db_session(db_path) as session:
        assert session.query(StockDaily).count() == before
        assert session.query(StockDaily).filter(StockDaily.code.like("%000300%")).count() == 0


def test_context_has_tech_and_market_but_no_stock_only_sections(db_path):
    service, llm = _service(db_path)
    service.diagnose("sh000300", force=True)
    text = llm.calls[0]
    assert "沪深300" in text
    assert "大盘" in text
    assert "技术" in text
    for banned in ("【资金流】", "【筹码】", "【业绩】"):
        assert banned not in text, banned


def test_low_score_downgrades_buy(db_path):
    result = _service(db_path, {**GOOD_REPLY, "score": 45, "action": "买入"})[0].diagnose("sh000300", force=True)
    assert result["action"] == "watch"


def test_frozen_market_downgrades_buy(db_path, monkeypatch):
    monkeypatch.setattr(regime_mod.MarketRegimeAnalyzer, "analyze",
                        lambda self, **kw: MarketRegime(trade_date="2026-09-29", regime="冰点", position_factor=0.0))
    result = _service(db_path)[0].diagnose("sh000300", force=True)
    assert result["action"] == "watch"


def test_unknown_or_empty_fund(db_path):
    service, llm = _service(db_path)
    assert service.diagnose("sh000852", force=True).get("error")  # 指数存在但没有任何行情
    assert llm.calls == []
    assert _service(db_path, {})[0].diagnose("sh000300", force=True).get("error")  # AI 无返回


# ---------- 流水线分派 ----------

@pytest.fixture
def fake_llm(monkeypatch):
    llm = _FakeLLM()

    class _Factory:
        def __new__(cls, *a, **k):
            return llm

    monkeypatch.setattr(llm_client_mod, "LLMClient", _Factory)
    try:
        from src.services import fund_diagnosis

        monkeypatch.setattr(fund_diagnosis, "LLMClient", _Factory, raising=False)
    except ImportError:
        pass
    return llm


def test_pipeline_dispatch(db_path, fake_llm):
    from src.services.pipeline_service import PipelineService

    pipeline = PipelineService({"database": {"sqlite_path": db_path}, "risk": {}, "trading": {}})
    for text in ("沪深300", "sh000300"):
        result = pipeline.diagnose_stock(text, force=True)
        assert not result.get("error"), result
        assert result["kind"] == "index" and result["code"] == "sh000300"
    # 个股仍走原逻辑：没有诊断所需行情时返回个股口径的结果/错误，但不是基金
    stock = pipeline.diagnose_stock("600519", force=True)
    assert stock.get("kind") in (None, "stock")


# ---------- API ----------

@pytest.fixture
def env(tmp_path, monkeypatch, fake_llm):
    path = str(tmp_path / "api.db")
    _reset_db_engine()
    init_db(path)
    StockSearch.reset()
    monkeypatch.setattr(trading_calendar, "load", lambda db_path="", refresh=True: True)
    monkeypatch.setattr(settings_store, "SETTINGS_PATH", tmp_path / "settings.yaml")
    _seed(path)
    config = {"database": {"sqlite_path": path}, "web": {}, "risk": {}, "trading": {},
              "llm": {"primary": {"provider": "deepseek", "api_key": "sk-x", "model": "m"}, "cache_path": str(tmp_path / "llm.sqlite3")}}
    static = tmp_path / "dist"
    static.mkdir()
    (static / "index.html").write_text("<html></html>", encoding="utf-8")
    app = create_app(config, start_scheduler=False, static_dir=static, auth=AuthStore(tmp_path / "auth.json"))
    with TestClient(app) as client:
        yield client, path
    StockSearch.reset()
    _reset_db_engine()


def _wait(client, task: dict, timeout: float = 8.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        current = client.get(f"/api/v1/tasks/{task['id']}").json()
        if current["status"] in ("done", "error"):
            return current
        time.sleep(0.02)
    raise AssertionError(f"任务超时: {task}")


def test_api_search_kind(env):
    client, _ = env
    found = client.get("/api/v1/stocks/search", params={"q": "沪深300"}).json()
    assert any(r["code"] == "sh000300" and r["kind"] == "index" for r in found)
    etf = client.get("/api/v1/stocks/search", params={"q": "510300"}).json()
    assert any(r["code"] == "510300" and r["kind"] == "etf" for r in etf)


def test_api_daily_reads_fund_daily(env):
    client, _ = env
    res = client.get("/api/v1/stocks/sh000300/daily")
    assert res.status_code == 200
    bars = res.json()
    assert len(bars) == len(DAYS) and bars[-1]["trade_date"] == DAYS[-1]
    assert bars[-1]["close"] == pytest.approx(4002 + 69 * 3)
    assert len(client.get("/api/v1/stocks/510300/daily").json()) == len(DAYS)
    # 原有个股接口不受影响
    stock = client.get("/api/v1/stocks/600519/daily").json()
    assert len(stock) == 1 and stock[0]["close"] == 1500.0


def test_api_diagnosis_roundtrip(env):
    client, path = env
    with get_db_session(path) as session:
        before = session.query(StockDaily).count()
    task = client.post("/api/v1/stocks/sh000300/diagnosis").json()
    done = _wait(client, task)
    assert done["status"] == "done", done
    assert done["result"]["kind"] == "index" and done["result"]["code"] == "sh000300"
    latest = client.get("/api/v1/stocks/sh000300/diagnosis").json()
    assert latest and latest["kind"] == "index" and latest["code"] == "sh000300"
    with get_db_session(path) as session:
        assert session.query(StockDaily).count() == before


def test_api_news_empty_for_funds(env):
    client, _ = env
    for code in ("sh000300", "510300"):
        res = client.get(f"/api/v1/stocks/{code}/news")
        assert res.status_code == 200, res.text
        body = res.json()
        assert not body.get("news") and not body.get("notices")


def test_api_watchlist_rejects_funds(env):
    client, _ = env
    for code in ("510300", "沪深300"):
        res = client.post("/api/v1/watchlist", json={"text": code})
        assert res.status_code == 200 and not res.json().get("ok")


# ---------- 问股工具 ----------

@pytest.fixture
def tools(db_path):
    from src.services.chat_tools import ChatTools

    return ChatTools({"database": {"sqlite_path": db_path}})


def test_chat_resolve_stock_includes_index(tools):
    text = tools.call("resolve_stock", {"query": "沪深300"})
    assert "沪深300" in text and "sh000300" in text and "index" in text


def test_chat_fund_flow_for_funds_is_stock_only(tools):
    for code in ("沪深300", "sh000300", "510300"):
        assert "只适用于个股" in tools.call("fund_flow", {"code": code})
