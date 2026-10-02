"""Web API：鉴权、后台任务、各业务接口、前端托管。"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from api.auth import AuthStore, check_request
from api.tasks import TaskManager
from src import settings_store
from src import trading_calendar
from src.database import db as db_module
from src.database.db import get_db_session, init_db
from src.database.models import StockDaily, StockInfo, StockScore
from src.services.stock_search import StockSearch


def _reset_db_engine():
    if db_module._engine:
        db_module._engine.dispose()
    db_module._engine = None
    db_module._SessionFactory = None


@pytest.fixture
def env(tmp_path, monkeypatch):
    path = str(tmp_path / "api.db")
    _reset_db_engine()
    init_db(path)
    StockSearch.reset()
    monkeypatch.setattr(trading_calendar, "load", lambda db_path="", refresh=True: True)
    monkeypatch.setattr(settings_store, "SETTINGS_PATH", tmp_path / "settings.yaml")
    with get_db_session(path) as session:
        for code, name in {"600519": "贵州茅台", "601919": "中远海控"}.items():
            session.add(StockInfo(code=code, name=name))
            session.add(StockDaily(code=code, name=name, trade_date="2026-09-25", close=10.0, change_pct=1.0))
    config = {"database": {"sqlite_path": path}, "web": {}, "risk": {},
              "llm": {"primary": {"provider": "deepseek", "api_key": "sk-secret-1234", "model": "deepseek-chat"},
                      "cache_path": str(tmp_path / "llm.sqlite3")},
              "notifier": {"email": {"enabled": False, "password": "mail-pass"}}}
    static = tmp_path / "dist"
    (static / "assets").mkdir(parents=True)
    (static / "index.html").write_text("<html>app</html>", encoding="utf-8")
    (static / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
    app = create_app(config, start_scheduler=False, static_dir=static, auth=AuthStore(tmp_path / "auth.json"))
    with TestClient(app) as client:
        yield client, app, config
    StockSearch.reset()
    _reset_db_engine()


def _wait(client, task: dict, timeout: float = 5.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        current = client.get(f"/api/v1/tasks/{task['id']}").json()
        if current["status"] in ("done", "error"):
            return current
        time.sleep(0.02)
    raise AssertionError(f"任务超时: {task}")


# ---------- 鉴权 ----------

def test_api_version_matches_release_packages(env):
    import json

    from src import __version__

    _, app, _ = env
    assert app.openapi()["info"]["version"] == __version__
    root = Path(__file__).resolve().parents[1]
    for app_name in ("web", "desktop"):
        package = json.loads((root / "apps" / app_name / "package.json").read_text())
        lock = json.loads((root / "apps" / app_name / "package-lock.json").read_text())
        assert package["version"] == lock["version"] == lock["packages"][""]["version"] == __version__


def test_access_rules(tmp_path):
    store = AuthStore(tmp_path / "auth.json")
    assert check_request({}, store, "/api/v1/dashboard", "127.0.0.1", None, None) == ""
    assert "只允许本机访问" in check_request({}, store, "/api/v1/dashboard", "192.168.1.8", None, None)
    assert check_request({}, store, "/api/v1/health", "192.168.1.8", None, None) == ""
    assert check_request({}, store, "/", "192.168.1.8", None, None) == ""               # 前端页面本身不拦
    assert check_request({"api_token": "t0k"}, store, "/api/v1/dashboard", "192.168.1.8", None, "Bearer t0k") == ""
    enabled = {"auth_enabled": True}
    assert check_request(enabled, store, "/api/v1/dashboard", "127.0.0.1", None, None) == "请先登录"
    store.set_password("abcdef")
    assert store.verify_password("abcdef") and not store.verify_password("x")
    token = store.issue_session(1)
    assert check_request(enabled, store, "/api/v1/dashboard", "10.0.0.2", token, None) == ""
    assert not store.verify_session(token[:-1] + ("0" if token[-1] != "0" else "1"))    # 签名被改
    assert not store.verify_session(store.issue_session(-1))                           # 已过期


def test_login_flow(env):
    client, app, config = env
    config["web"]["auth_enabled"] = True
    assert client.get("/api/v1/dashboard").status_code == 401
    assert client.get("/api/v1/auth/status").json() == {"auth_enabled": True, "password_set": False, "logged_in": False}
    assert client.post("/api/v1/auth/login", json={"password": "123"}).status_code == 400   # 首次设置太短
    assert client.post("/api/v1/auth/login", json={"password": "secret1"}).json() == {"ok": True}
    assert client.get("/api/v1/auth/status").json()["logged_in"] is True
    assert client.get("/api/v1/market/themes").status_code == 200
    client.post("/api/v1/auth/logout")
    client.cookies.clear()
    assert client.post("/api/v1/auth/login", json={"password": "wrong"}).json()["detail"] == "密码错误"
    config["web"]["auth_enabled"] = False


# ---------- 后台任务 ----------

def test_task_manager():
    tasks = TaskManager(workers=2)

    def work(n, progress=None):
        progress(1, 2)
        progress("一半了")
        return {"n": n}

    task = tasks.submit("demo", work, 3, label="演示")
    for _ in range(100):
        if tasks.get(task["id"])["status"] == "done":
            break
        time.sleep(0.01)
    done = tasks.get(task["id"])
    assert done["result"] == {"n": 3} and done["progress"] == {"text": "一半了"} and done["label"] == "演示"

    def slow():
        time.sleep(0.2)
        return 1

    first = tasks.submit("slow", slow, dedupe_key="slow")
    assert tasks.submit("slow", slow, dedupe_key="slow")["id"] == first["id"]           # 运行中不重复提交
    failing = tasks.submit("boom", lambda: 1 / 0)
    for _ in range(100):
        if tasks.get(failing["id"])["status"] == "error":
            break
        time.sleep(0.01)
    assert "division by zero" in tasks.get(failing["id"])["error"]
    assert tasks.list()[0]["result"] is None                                             # 列表不带结果
    tasks.shutdown()


# ---------- 业务接口 ----------

def test_stock_and_market_endpoints(env, monkeypatch):
    client, app, _ = env
    assert client.get("/api/v1/health").json() == {"status": "ok"}
    assert client.get("/api/v1/stocks/search", params={"q": "gzmt"}).json()[0] == {"code": "600519", "name": "贵州茅台", "kind": "stock"}
    assert client.get("/api/v1/stocks/sh600519/daily").json()[0]["close"] == 10.0
    assert client.get("/api/v1/stocks/600519/diagnosis").json() is None
    monkeypatch.setattr(app.state.pipeline, "diagnose_stock", lambda code, force=False: {"code": code, "action": "watch", "force": force})
    task = client.post("/api/v1/stocks/600519/diagnosis").json()
    assert _wait(client, task)["result"] == {"code": "600519", "action": "watch", "force": True}

    assert isinstance(client.get("/api/v1/dashboard").json()["top_stocks"], list)
    assert client.get("/api/v1/market/themes", params={"dimension": "industry"}).json() == []
    assert client.get("/api/v1/market/themes", params={"dimension": "bad"}).status_code == 422
    assert client.get("/api/v1/market/review").json() is None
    assert client.get("/api/v1/tasks/nope").status_code == 404



def test_dashboard_with_scores_and_daily(env):
    """有评分且评分日有行情时，交易焦点要带涨幅；曾在会话关闭后读 ORM 属性导致首页接口 500"""
    client, _, config = env
    with get_db_session(config["database"]["sqlite_path"]) as s:
        s.add(StockScore(code="600519", name="贵州茅台", score_date="2026-09-25", composite_score=80, rank=1, recommendation="buy"))
    resp = client.get("/api/v1/dashboard")
    assert resp.status_code == 200
    focus = resp.json()["trade_focus"]
    assert focus[0]["code"] == "600519" and focus[0]["change_pct"] == 1.0


def test_daily_fills_missing_names(env):
    """历史回补的日线没有名称，接口用股票列表补上，个股页标题才有名称"""
    client, _, config = env
    with get_db_session(config["database"]["sqlite_path"]) as s:
        s.add(StockDaily(code="600519", name="", trade_date="2026-09-24", close=9.8, change_pct=-0.5))
    bars = client.get("/api/v1/stocks/600519/daily").json()
    assert [b["name"] for b in bars] == ["贵州茅台", "贵州茅台"]

def test_watchlist_and_real_endpoints(env):
    client, _, _ = env
    assert client.post("/api/v1/watchlist", json={"text": "gzmt"}).json()["ok"] is True
    assert client.post("/api/v1/watchlist/import", json={"text": "601919 中远海控"}).json()["added"] == ["中远海控(601919)"]
    csv = "证券代码,证券名称\n600519,贵州茅台\n".encode("gbk")
    result = client.post("/api/v1/watchlist/import-file", files={"file": ("list.csv", csv, "text/csv")}).json()
    assert result["existing"] == ["贵州茅台(600519)"]
    assert client.post("/api/v1/watchlist/import-file", files={"file": ("x.exe", b"1", "application/octet-stream")}).status_code == 400
    assert [r["code"] for r in client.get("/api/v1/watchlist").json()] == ["600519", "601919"]
    assert client.delete("/api/v1/watchlist/601919").json() == {"ok": True}

    trade = {"trade_date": "2026-09-24", "code": "600519", "side": "buy", "price": 10.0, "quantity": 100}
    assert client.post("/api/v1/real/trades", json=trade).json() == {"ok": True}
    assert client.post("/api/v1/real/trades", json={**trade, "side": "sell", "quantity": 500}).status_code == 400
    assert client.post("/api/v1/real/trades", json={**trade, "price": -1}).status_code == 422
    assert client.put("/api/v1/real/cash", json={"cash": 50000}).json() == {"ok": True}
    assert client.put("/api/v1/real/plans/600519", json={"stop_loss": 9.5}).json() == {"ok": True}
    real = client.get("/api/v1/real").json()
    position = real["snapshot"]["positions"][0]
    assert (position["code"], position["quantity"], position["stop_loss"]) == ("600519", 100, 9.5)
    assert real["snapshot"]["account"]["cash_known"] is True and real["risk"]["account"] == "real"


def test_chat_endpoints(env, monkeypatch):
    client, app, _ = env
    from src.services.stock_chat import StockChatSession

    class LLM:
        def chat_json(self, user_message, system_message="", **kwargs):
            return {"answer": "观望。仅供学习研究，不构成投资建议"}

    app.state.chat_store._factory = lambda cfg: StockChatSession(cfg, llm=LLM())
    session = client.post("/api/v1/chat/sessions", json={"perspective": "龙回头"}).json()
    task = client.post(f"/api/v1/chat/sessions/{session['id']}/ask", json={"question": "茅台能买吗"}).json()
    assert _wait(client, task)["result"]["answer"].startswith("观望")
    record = client.get(f"/api/v1/chat/sessions/{session['id']}").json()
    assert record["title"] == "茅台能买吗" and record["turns"][0]["perspective"] == "龙回头"
    assert client.get("/api/v1/chat/sessions").json()[0]["turns"] == 1
    assert "## 问：茅台能买吗" in client.get(f"/api/v1/chat/sessions/{session['id']}/export").text
    pushed = []
    monkeypatch.setattr(app.state.pipeline, "push_message", lambda title, content, kind="chat": pushed.append(title) or {"pushed": True})
    assert client.post(f"/api/v1/chat/sessions/{session['id']}/push").json() == {"pushed": True} and pushed == ["AI 问股：茅台能买吗"]
    assert client.post("/api/v1/chat/sessions/none/ask", json={"question": "x"}).status_code == 404
    assert client.delete(f"/api/v1/chat/sessions/{session['id']}").json() == {"ok": True}


def test_settings_endpoints(env, monkeypatch):
    client, app, config = env
    from src import config_loader

    llm = client.get("/api/v1/settings/llm").json()
    assert llm["llm"]["primary"]["api_key"] == "******1234" and "deepseek" in llm["platforms"]
    saved = {}
    monkeypatch.setattr(config_loader, "reload_config", lambda: saved.setdefault("config", {**config, "reloaded": True}))
    body = {"llm": {"primary": {"provider": "deepseek", "api_key": "******1234", "model": "deepseek-reasoner"}}}
    assert client.put("/api/v1/settings/llm", json=body).json() == {"ok": True}
    written = settings_store.read_settings()
    assert written["llm"]["primary"]["api_key"] == "sk-secret-1234"                   # 掩码回传时保留原值
    assert written["llm"]["primary"]["model"] == "deepseek-reasoner"
    assert app.state.pipeline.config.get("reloaded") and app.state.chat_store.config.get("reloaded")

    notifier = client.get("/api/v1/settings/notifier").json()
    assert notifier["notifier"]["email"]["password"] == "******" and notifier["kinds"]["alert"] == "盘中提醒"
    body = {"notifier": {"email": {"enabled": True, "smtp_host": "smtp.qq.com", "password": "******", "to": ["a@x.com"]}}}
    assert client.put("/api/v1/settings/notifier", json=body).json() == {"ok": True}
    assert settings_store.read_settings()["notifier"]["email"]["password"] == "mail-pass"
    issues = client.post("/api/v1/settings/notifier/diagnose", json=body).json()
    assert next(c for c in issues["channels"] if c["channel"] == "email")["configured"] is True


def test_usage_endpoint(env):
    client, _, config = env
    from src.analyzers.llm_usage import record_usage

    record_usage(config["llm"]["cache_path"], provider="deepseek", model="deepseek-chat", feature="个股诊断",
                 prompt_tokens=100, completion_tokens=20)
    usage = client.get("/api/v1/usage", params={"days": 7}).json()
    assert usage["total"]["tokens"] == 120 and usage["by_feature"][0]["key"] == "个股诊断"


def test_enable_web_auth(env, monkeypatch):
    client, app, config = env
    from src import config_loader

    monkeypatch.setattr(config_loader, "reload_config", lambda: {**config, "web": settings_store.read_settings()["web"]})
    assert client.put("/api/v1/settings/web-auth", json={"auth_enabled": True, "password": "123"}).status_code == 400
    assert client.put("/api/v1/settings/web-auth", json={"auth_enabled": True, "password": "secret1"}).json() == {"ok": True}
    assert settings_store.read_settings()["web"] == {"auth_enabled": True}
    assert client.get("/api/v1/auth/status").json() == {"auth_enabled": True, "password_set": True, "logged_in": True}  # 当前浏览器直接登录
    assert client.get("/api/v1/market/themes").status_code == 200
    client.cookies.clear()
    assert client.get("/api/v1/market/themes").status_code == 401


def test_frontend_hosting(env):
    client, _, _ = env
    assert client.get("/").text == "<html>app</html>"
    assert client.get("/watchlist").text == "<html>app</html>"                       # 前端路由
    assert client.get("/assets/app.js").text == "console.log(1)"
    assert client.get("/api/v1/nothing").status_code == 404
    assert client.get("/..%2F..%2Fapi.db").text == "<html>app</html>"                # 不能读到静态目录以外的文件


def test_frontend_not_built(tmp_path, monkeypatch):
    monkeypatch.setattr(trading_calendar, "load", lambda db_path="", refresh=True: True)
    _reset_db_engine()
    config = {"database": {"sqlite_path": str(tmp_path / "x.db")}}
    app = create_app(config, start_scheduler=False, static_dir=Path(tmp_path / "missing"), auth=AuthStore(tmp_path / "a.json"))
    with TestClient(app) as client:
        assert "前端还没有构建" in client.get("/").json()["detail"]
    _reset_db_engine()


def test_bot_settings(env, monkeypatch):
    client, app, config = env
    from src import config_loader
    from src.bot import manager

    config["bot"] = {"dingtalk": {"enabled": False, "client_id": "key", "client_secret": "ding-secret"}}
    got = client.get("/api/v1/settings/bot").json()
    assert got["bot"]["dingtalk"]["client_secret"] == "******" and got["running"] == []
    monkeypatch.setattr(config_loader, "reload_config", lambda: {**config, "bot": settings_store.read_settings()["bot"]})
    monkeypatch.setattr(manager, "start_bots", lambda *a, **k: (_ for _ in ()).throw(AssertionError("不该启动")))
    body = {"bot": {"dingtalk": {"enabled": True, "client_id": "key", "client_secret": "******"}, "allowed_users": ["u1"]}}
    result = client.put("/api/v1/settings/bot", json=body).json()
    assert result == {"ok": True, "started": [], "restart_required": False, "background": False}  # 测试里没有运行定时任务
    written = settings_store.read_settings()["bot"]
    assert written["dingtalk"] == {"enabled": True, "client_id": "key", "client_secret": "ding-secret"}
    assert written["allowed_users"] == ["u1"]
