"""深度研究：ResearchService、API、机器人「研究」命令（离线，假 llm / 假 tools）。"""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timedelta
from types import SimpleNamespace
from urllib.parse import unquote

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from api.auth import AuthStore
from src import settings_store, trading_calendar
from src.analyzers import llm_usage
from src.bot.models import BotMessage
from src.bot.router import HELP_TEXT, CommandRouter
from src.collectors import news_search
from src.database import db as db_module
from src.database.db import get_db_session, init_db
from src.database.models import FinanceNews, StockDaily, StockInfo
from src.services.research import ResearchService
from src.services.stock_search import StockSearch


def _reset_db_engine():
    if db_module._engine:
        db_module._engine.dispose()
    db_module._engine = None
    db_module._SessionFactory = None


class FakeLLM:
    def __init__(self, plan=None, fail_plan=False):
        self.plan = plan if plan is not None else {
            "questions": ["固态电池产业链现状", "固态电池的主要风险"],
            "stocks": ["600519"], "keywords": ["固态电池"]}
        self.fail_plan = fail_plan
        self.chat_prompts: list[str] = []
        self.json_prompts: list[str] = []

    def chat_json(self, user_message, system_message="", **kw):
        self.json_prompts.append(user_message)
        if self.fail_plan:
            raise RuntimeError("规划失败BOOM")
        return self.plan

    def chat(self, user_message, system_message="", **kw):
        self.chat_prompts.append(user_message + "\n" + system_message)
        return "# 固态电池研究\n\n结论[E1]"


class FakeTools:
    def __init__(self, fail=()):
        self.calls: list[tuple[str, dict]] = []
        self.fail = set(fail)

    def call(self, name, args=None):
        self.calls.append((name, args or {}))
        if name in self.fail:
            raise RuntimeError(f"{name}挂了")
        return f"{name}结果:{args}"


@pytest.fixture
def env_db(tmp_path, monkeypatch):
    path = str(tmp_path / "r.db")
    _reset_db_engine()
    init_db(path)
    StockSearch.reset()
    news_search.reset_state()
    with get_db_session(path) as s:
        s.add(StockInfo(code="600519", name="贵州茅台"))
        s.add(StockDaily(code="600519", name="贵州茅台", trade_date="2026-09-25", close=10.0))
    config = {"database": {"sqlite_path": path}, "llm": {"cache_path": str(tmp_path / "llm.sqlite3")}}
    yield path, config
    news_search.reset_state()
    StockSearch.reset()
    _reset_db_engine()


@pytest.fixture
def search_calls(monkeypatch):
    calls = []
    monkeypatch.setattr(news_search, "is_enabled", lambda config: True)

    def fake(query, config, **kw):
        calls.append(query)
        return [SimpleNamespace(title=f"联网{len(calls)}", snippet="摘" * 1000, url=f"http://x/{len(calls)}",
                                source="s", published="2026-09-29", content="摘" * 1000)]

    monkeypatch.setattr(news_search, "search", fake)
    return calls


def _svc(config, llm=None, tools=None):
    return ResearchService(config, llm=llm or FakeLLM(), tools=tools or FakeTools())


def _add_news(path, title, days_ago, content="固态电池新进展"):
    with get_db_session(path) as s:
        s.add(FinanceNews(source="cailianshe", title=title, content=content,
                          news_time=datetime.now() - timedelta(days=days_ago)))


# ---------- 服务 ----------

def test_run_basic(env_db, monkeypatch):
    path, config = env_db
    monkeypatch.setattr(news_search, "is_enabled", lambda c: False)
    calls = []
    monkeypatch.setattr(news_search, "search", lambda *a, **k: calls.append(a) or [])
    llm, tools = FakeLLM(), FakeTools()
    out = _svc(config, llm, tools).run("固态电池")
    assert out["topic"] == "固态电池" and "结论" in out["markdown"] and out["id"]
    assert out["questions"] == ["固态电池产业链现状", "固态电池的主要风险"]
    assert out["created_at"]
    assert calls == []                                    # 未启用联网搜索
    names = [n for n, _ in tools.calls]
    assert {"quote", "technical", "theme"} <= set(names)
    assert names.count("market") == 1


def test_web_search_per_question(env_db, search_calls):
    _, config = env_db
    out = _svc(config).run("固态电池")
    assert len(search_calls) == 2
    assert out["evidence"]


def test_local_news_window(env_db, monkeypatch):
    path, config = env_db
    monkeypatch.setattr(news_search, "is_enabled", lambda c: False)
    _add_news(path, "近期固态电池突破", 2)
    _add_news(path, "很久以前固态电池旧闻", 20)
    out = _svc(config).run("固态电池")
    blob = json.dumps(out["evidence"], ensure_ascii=False)
    assert "近期固态电池突破" in blob
    assert "很久以前固态电池旧闻" not in blob


def test_evidence_numbering_and_limits(env_db, search_calls):
    path, config = env_db
    for i in range(60):
        _add_news(path, f"固态电池新闻{i}", 1, content="长" * 2000)
    llm = FakeLLM()
    out = _svc(config, llm).run("固态电池")
    ev = out["evidence"]
    ids = [e.get("id") or e.get("no") for e in ev]
    assert ids == [f"E{i}" for i in range(1, len(ev) + 1)]
    assert all(len(str(e.get("content", ""))) <= 400 for e in ev)
    assert sum(len(str(e.get("content", ""))) for e in ev) <= 12000
    assert "E1" in llm.chat_prompts[0]


def test_progress_stages(env_db, monkeypatch):
    _, config = env_db
    monkeypatch.setattr(news_search, "is_enabled", lambda c: False)
    msgs = []
    _svc(config).run("固态电池", progress=msgs.append)
    joined = " ".join(str(m) for m in msgs)
    assert "拆解问题" in joined and "撰写报告" in joined


def test_cancel_before_start(env_db):
    path, config = env_db
    ev = threading.Event()
    ev.set()
    svc = _svc(config)
    with pytest.raises(RuntimeError):
        svc.run("固态电池", cancel=ev)
    assert svc.list() == []


def test_plan_failure(env_db):
    _, config = env_db
    with pytest.raises(RuntimeError):
        _svc(config, FakeLLM(fail_plan=True)).run("固态电池")


def test_source_failure_tolerated(env_db, monkeypatch):
    _, config = env_db
    monkeypatch.setattr(news_search, "is_enabled", lambda c: True)

    def boom(*a, **k):
        raise RuntimeError("搜索挂了")

    monkeypatch.setattr(news_search, "search", boom)
    out = _svc(config, tools=FakeTools(fail=("quote", "technical", "theme", "market"))).run("固态电池")
    assert out["markdown"]


def test_save_list_get_delete(env_db, monkeypatch):
    _, config = env_db
    monkeypatch.setattr(news_search, "is_enabled", lambda c: False)
    svc = _svc(config)
    out = svc.run("固态电池")
    rows = svc.list()
    assert len(rows) == 1
    row = rows[0]
    assert row["id"] == out["id"] and row["topic"] == "固态电池" and row["created_at"]
    assert any(k in row for k in ("summary", "excerpt", "snippet", "abstract"))
    got = svc.get(out["id"])
    assert got["markdown"] == out["markdown"] and got["evidence"] == out["evidence"]
    assert svc.delete(out["id"])
    assert svc.get(out["id"]) is None and svc.list() == []


def test_feature_label():
    assert "src.services.research" in llm_usage.FEATURE_LABELS
    assert llm_usage.FEATURE_LABELS["src.services.research"] == "深度研究"


# ---------- API ----------

@pytest.fixture
def api(tmp_path, monkeypatch):
    path = str(tmp_path / "api.db")
    _reset_db_engine()
    init_db(path)
    StockSearch.reset()
    monkeypatch.setattr(trading_calendar, "load", lambda db_path="", refresh=True: True)
    monkeypatch.setattr(settings_store, "SETTINGS_PATH", tmp_path / "settings.yaml")
    config = {"database": {"sqlite_path": path}, "web": {}, "risk": {},
              "llm": {"primary": {"provider": "deepseek", "api_key": "sk-secret-1234", "model": "deepseek-chat"},
                      "cache_path": str(tmp_path / "llm.sqlite3")}}
    static = tmp_path / "dist"
    static.mkdir()
    (static / "index.html").write_text("<html>app</html>", encoding="utf-8")
    app = create_app(config, start_scheduler=False, static_dir=static, auth=AuthStore(tmp_path / "auth.json"))
    with TestClient(app) as client:
        yield client, config
    StockSearch.reset()
    _reset_db_engine()


def _wait(client, task, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        cur = client.get(f"/api/v1/tasks/{task['id']}").json()
        if cur["status"] in ("done", "error"):
            return cur
        time.sleep(0.02)
    raise AssertionError("任务超时")


def _seed(config, topic="固态电池", md="# 报告\n正文"):
    from src.database.models import ResearchReport
    with get_db_session(config["database"]["sqlite_path"]) as s:
        r = ResearchReport(topic=topic, markdown=md, questions_json="[]", evidence_json="[]",
                           stocks_json="[]", created_at=datetime.now())
        s.add(r)
        s.flush()
        return r.id


def test_api_post_validation(api):
    client, _ = api
    assert client.post("/api/v1/research", json={"topic": ""}).status_code == 400
    assert client.post("/api/v1/research", json={"topic": "   "}).status_code == 400
    assert client.post("/api/v1/research", json={"topic": "长" * 101}).status_code == 400


def test_api_post_runs_task(api, monkeypatch):
    client, _ = api
    monkeypatch.setattr(ResearchService, "run", lambda self, topic, progress=None, cancel=None:
                        {"id": 1, "topic": topic, "markdown": "# 假报告", "questions": [], "evidence": [],
                         "stocks": [], "created_at": "2026-09-30 10:00:00"})
    resp = client.post("/api/v1/research", json={"topic": "固态电池"})
    assert resp.status_code == 200
    done = _wait(client, resp.json())
    assert done["status"] == "done"
    assert done["result"]["markdown"] == "# 假报告"


def test_api_list_get_delete(api):
    client, config = api
    rid = _seed(config)
    rows = client.get("/api/v1/research").json()
    rows = rows["items"] if isinstance(rows, dict) else rows
    assert [r["id"] for r in rows] == [rid]
    got = client.get(f"/api/v1/research/{rid}")
    assert got.status_code == 200 and got.json()["markdown"].startswith("# 报告")
    assert client.get("/api/v1/research/9999").status_code == 404
    assert client.delete("/api/v1/research/9999").status_code == 404
    assert client.delete(f"/api/v1/research/{rid}").status_code == 200
    assert client.get(f"/api/v1/research/{rid}").status_code == 404


def test_api_markdown_download(api):
    client, config = api
    rid = _seed(config, topic="固态电池")
    resp = client.get(f"/api/v1/research/{rid}/markdown")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/markdown")
    cd = resp.headers["content-disposition"]
    assert "attachment" in cd and "filename*=UTF-8''" in cd
    assert "固态电池" in unquote(cd)
    assert "正文" in resp.text
    assert client.get("/api/v1/research/9999/markdown").status_code == 404


# ---------- 机器人 ----------

class Chat:
    instances: list = []

    def __init__(self):
        self.calls = []
        Chat.instances.append(self)

    def ask(self, question, perspective="综合", progress=None):
        self.calls.append(question)
        return SimpleNamespace(answer="问股回答", error="")


def _msg(text, user="u1"):
    return BotMessage(platform="dingtalk", chat_id="c1", user_id=user, user_name="张三", text=text)


@pytest.fixture
def bot(monkeypatch):
    Chat.instances = []
    runs = []

    def fake_run(self, topic, progress=None, cancel=None):
        runs.append(topic)
        return {"id": 1, "topic": topic, "markdown": f"# 研究报告:{topic}", "questions": [], "evidence": [],
                "stocks": [], "created_at": "2026-09-30 10:00:00"}

    monkeypatch.setattr(ResearchService, "run", fake_run)
    return runs


def _router(allowed=None):
    cfg = {"database": {"sqlite_path": ":memory:"}}
    if allowed:
        cfg["bot"] = {"allowed_users": allowed}
    return CommandRouter(cfg, pipeline=SimpleNamespace(db_path=":memory:"), chat_factory=Chat)


def test_bot_research(bot):
    router = _router()
    progress = []
    reply = router.handle(_msg("研究 固态电池"), progress.append)
    assert bot == ["固态电池"]
    assert "研究报告:固态电池" in reply
    assert any("开始研究" in p for p in progress)
    assert Chat.instances == [] or all(not c.calls for c in Chat.instances)


def test_bot_research_usage(bot):
    router = _router()
    reply = router.handle(_msg("研究"))
    assert bot == []
    assert "研究" in reply and ("用法" in reply or "例" in reply)
    assert all(not c.calls for c in Chat.instances)


def test_bot_help_and_permission(bot):
    assert "研究" in HELP_TEXT
    router = _router(allowed=["boss"])
    reply = router.handle(_msg("研究 固态电池", user="stranger"))
    assert bot == [] and "没有使用权限" in reply
    assert "研究报告" in router.handle(_msg("研究 固态电池", user="boss"))
