"""问股主讨论对象与会话状态（对齐 daily_stock_analysis c3349de4）：术语不当代码、否定/条件/疑问/改看、
普通追问沿用主对象、先接受后执行、版本冲突、策略选择三态、Web 与机器人交替、工具身份边界。全部离线。"""

import json
import threading

import pytest

from src.bot.models import BotMessage
from src.bot.router import CommandRouter
from src.collectors import daily_history as daily_history_mod
from src.database.db import get_db_session, init_db
from src.database.models import StockDaily, StockInfo
from src.services.chat_sessions import ChatSessionStore, ChatStateConflict, INTERRUPTED_TEXT
from src.services.chat_tools import STOCK_ONLY_TEXT, ChatTools
from src.services.stock_chat import StockChatSession
from src.services.stock_search import StockSearch
from src.services.web_intent import resolve
from tests.test_chat_stream import FakeTools, StreamLLM
from tests.test_self_learning import _reset_db_engine
from tests.test_web_intent_stages import names  # noqa: F401

STOCKS = {"600519": "贵州茅台", "300750": "宁德时代", "000001": "平安银行", "601318": "中国平安", "002594": "比亚迪"}


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = str(tmp_path / "chat.db")
    _reset_db_engine()
    init_db(path)
    StockSearch.reset()
    monkeypatch.setattr(daily_history_mod, "ensure_daily_history", lambda *a, **k: 0)
    with get_db_session(path) as session:
        for code, name in STOCKS.items():
            session.add(StockInfo(code=code, name=name))
            session.add(StockDaily(code=code, name=name, trade_date="2026-09-25", close=10.0, change_pct=1.0))
    yield {"database": {"sqlite_path": path}}
    StockSearch.reset()
    _reset_db_engine()


def _store(config, replies=None):
    """真实意图层（ChatTools）+ 假模型：每次恢复会话都给一个新的流式假模型。"""
    tools = ChatTools(config)
    return ChatSessionStore(config, session_factory=lambda cfg: StockChatSession(cfg, llm=StreamLLM(list(replies or [])), tools=tools))


def _targets(turn):
    return [[t["code"] for t in task["targets"]] for task in turn["intent_plan"]]


# ---------- 主讨论对象规则 ----------

@pytest.fixture
def more_names(monkeypatch):
    entries = [(code, name, (), "stock") for code, name in STOCKS.items()]
    monkeypatch.setattr(StockSearch, "_ensure_index", lambda self: entries)
    return entries


def _plan(question, state=None, ctx=None):
    plan = resolve(question, "unused", state, ctx)
    return plan, [[t["code"] for t in task["targets"]] for task in plan["tasks"]], (plan["state"].get("active") or {}).get("code")


@pytest.mark.parametrize("question,targets,active", [
    ("不要改看300750，继续分析600519", [["600519"]], "600519"),
    ("不要研究600519和300750的区别，继续分析600519", [["600519"]], "600519"),
    ("从600519改看300750", [["300750"]], "300750"),
    ("把600519换成300750", [["300750"]], "300750"),
    ("对比600519和300750", [["600519", "300750"]], None),
    ("能不能分析一下宁德时代", [["300750"]], "300750"),
    ("如果茅台跌破1500，要不要止损", [["600519"]], "600519"),
])
def test_object_decisions(more_names, question, targets, active):
    plan, got, got_active = _plan(question)
    assert not plan["requires_confirmation"]
    assert (got, got_active) == (targets, active)


@pytest.mark.parametrize("question", ["是否改看300750？", "要不要换成宁德时代", "如果600519下跌，就改看300750",
                                      "如果茅台跌破1500就改看宁德时代", "不要改看300750"])
def test_conditional_question_and_denied_switch_only_clarify(more_names, question):
    state = {"active": {"code": "600519", "name": "贵州茅台", "kind": "stock"}}
    plan, got, active = _plan(question, state)
    assert plan["requires_confirmation"] and got == [] and active == "600519"


def test_ambiguous_switch_destination_needs_confirmation(more_names):
    plan, _, _ = _plan("改看300750或002594", {"active": {"code": "600519"}})
    assert plan["requires_confirmation"] and "改看哪一只" in plan["message"]
    _, got, active = _plan("2", plan["state"])
    assert got == [["002594"]] and active == "002594"


def test_plain_followups_keep_active_object(more_names):
    base = _plan("分析600519")[0]["state"]
    for question in ["看看近期走势", "分析一下财务状况", "分析风险", "风险呢", "它的行情呢"]:
        plan, got, active = _plan(question, base)
        assert not plan["requires_confirmation"] and got == [["600519"]] and active == "600519", question
    plan, got, active = _plan("和宁德时代比呢", base)
    assert got == [["600519", "300750"]] and active == "600519"   # 比较不改主对象
    assert _plan("看看大盘", base)[1] == [[]]
    switched = _plan("改看宁德时代", base)[0]["state"]
    assert _plan("风险呢", switched)[1:] == ([["300750"]], "300750")


def test_followup_without_active_asks_instead_of_guessing(more_names):
    legacy = {"recent_stocks": ["600519", "300750"], "last_kind": "stock_analysis"}   # 旧会话没有主对象
    plan, _, active = _plan("分析风险", legacy)
    assert plan["requires_confirmation"] and active is None
    assert [c["code"] for c in plan["state"]["pending"]["candidates"]] == ["600519", "300750"]
    assert _plan("1", plan["state"])[1:] == ([["600519"]], "600519")
    assert _plan("风险呢", {})[1:] == ([[]], None)


# ---------- 会话状态：先接受后执行、版本、主对象持久化 ----------

def test_accept_before_execute_and_persisted_object(db):
    store = _store(db, [{"answer": "茅台结论"}])
    sid = store.create()["id"]
    events = store.ask_stream(sid, "分析600519")
    accepted = next(events)
    assert accepted["type"] == "accepted" and accepted["session_state_version"] == 1
    assert accepted["active_stock_context"]["code"] == "600519" and accepted["active_stock_context"]["name"] == "贵州茅台"
    raw = store._load(sid)
    assert raw["turns"][-1]["question"] == "分析600519" and raw["turns"][-1]["status"] == "running"
    assert store.get(sid)["turns"][-1]["status"] == "running"   # 正在回答（本进程持有会话锁）
    rest = list(events)
    assert rest[-1]["type"] == "done"
    record = store.get(sid)
    assert record["turns"][-1]["status"] == "done" and "茅台结论" in record["turns"][-1]["answer"]
    assert record["active_stock_context"]["code"] == "600519" and record["session_state_version"] == 1
    # 换一个 store（相当于刷新页面或重启）：普通追问沿用服务端保存的主对象
    turn = _store(db).ask(sid, "分析风险", expected_version=1)
    assert _targets(turn) == [["600519"]] and turn["session_state_version"] == 2
    assert turn["active_stock_context"]["confirmed_at"] == record["active_stock_context"]["confirmed_at"]


def test_switch_updates_object_and_comparison_keeps_it(db):
    store = _store(db)
    sid = store.create()["id"]
    store.ask(sid, "分析600519")
    store.ask(sid, "对比600519和300750")
    assert store.get(sid)["active_stock_context"]["code"] == "600519"
    store.ask(sid, "从600519改看300750")
    assert store.get(sid)["active_stock_context"]["code"] == "300750"
    clarified = store.ask(sid, "是否改看600519？")
    assert clarified["answer"].startswith("「是否改看600519」") and store.get(sid)["active_stock_context"]["code"] == "300750"


def test_stale_version_conflicts_without_writing(db):
    store = _store(db)
    sid = store.create()["id"]
    store.ask(sid, "分析600519")
    with pytest.raises(ChatStateConflict):
        store.ask(sid, "分析风险", expected_version=0)
    events = list(store.ask_stream(sid, "分析风险", expected_version=0))
    assert events == [{"type": "error", "message": events[0]["message"], "error_code": "session_state_conflict", "session_id": sid}]
    record = store.get(sid)
    assert len(record["turns"]) == 1 and record["session_state_version"] == 1


def test_late_reply_after_delete_is_dropped(db):
    store = _store(db)
    sid = store.create()["id"]
    events = store.ask_stream(sid, "分析600519")
    next(events)
    assert store.delete(sid)
    list(events)
    assert store.get(sid) is None


def test_close_after_accept_keeps_question_and_interrupted_view(db):
    store = _store(db)
    sid = store.create()["id"]
    events = store.ask_stream(sid, "分析600519")
    next(events)
    events.close()
    turn = store.get(sid)["turns"][-1]
    assert turn["question"] == "分析600519" and turn["error"] == "已取消"
    # 服务重启：接受后没有写回最终回答的一轮显示为中断，只改展示不写回
    store._accept(sid, "看看近期走势", None, None, None, None)
    assert store.get(sid)["turns"][-1]["status"] == "interrupted" and store.get(sid)["turns"][-1]["error"] == INTERRUPTED_TEXT
    assert store._load(sid)["turns"][-1]["status"] == "running"


def test_skill_selection_is_tri_state(db):
    store = ChatSessionStore(db, session_factory=lambda cfg: StockChatSession(cfg, llm=StreamLLM([]), tools=FakeTools()))
    sid = store.create()["id"]
    assert store.get(sid)["session_state"] == {"selected_skill_ids": None}
    # 省略沿用已保存的选择（未选择时不加策略）；[] 显式清空；列表保存为新的选择
    for skills, expected_saved, expected_used in [(None, None, []), (["dragon_head"], ["dragon_head"], ["dragon_head"]),
                                                  (None, ["dragon_head"], ["dragon_head"]), ([], [], [])]:
        turn = store.ask(sid, "茅台", skills=skills)
        assert store.get(sid)["session_state"]["selected_skill_ids"] == expected_saved
        assert turn["skills"] == expected_used


# ---------- 接口 ----------

def test_chat_api_state_and_conflicts(db, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from api.app import create_app
    from api.auth import AuthStore
    from src import trading_calendar

    monkeypatch.setattr(trading_calendar, "load", lambda db_path="", refresh=True: True)
    config = {**db, "web": {}, "llm": {"cache_path": str(tmp_path / "llm.sqlite3")}}
    static = tmp_path / "dist"
    static.mkdir()
    (static / "index.html").write_text("<html></html>", encoding="utf-8")
    app = create_app(config, start_scheduler=False, static_dir=static, auth=AuthStore(tmp_path / "auth.json"))
    with TestClient(app) as client:
        app.state.chat_store._factory = lambda cfg: StockChatSession(cfg, llm=StreamLLM([]), tools=ChatTools(cfg))
        sid = client.post("/api/v1/chat/sessions", json={}).json()["id"]
        detail = client.get(f"/api/v1/chat/sessions/{sid}").json()
        assert detail["active_stock_context"] is None and detail["session_state_version"] == 0
        assert detail["session_state"] == {"selected_skill_ids": None}
        body = client.post(f"/api/v1/chat/sessions/{sid}/ask/stream",
                           json={"question": "分析600519", "state_version": 0, "skills": ["dragon_head"]}).text
        events = [json.loads(line[6:]) for line in body.splitlines() if line.startswith("data: ")]
        assert events[0]["type"] == "accepted" and events[-1]["type"] == "done"
        detail = client.get(f"/api/v1/chat/sessions/{sid}").json()
        assert detail["active_stock_context"]["code"] == "600519" and detail["session_state_version"] == 1
        assert detail["session_state"] == {"selected_skill_ids": ["dragon_head"]}
        stale = {"question": "分析风险", "state_version": 0}
        assert client.post(f"/api/v1/chat/sessions/{sid}/ask/stream", json=stale).status_code == 409
        assert client.post(f"/api/v1/chat/sessions/{sid}/ask", json=stale).status_code == 409
        assert client.get("/api/v1/chat/sessions").json()[0]["source"] == "web"


# ---------- 工具身份边界 ----------

def _tool_turn(tools, call, scope=None):
    chat = StockChatSession({}, llm=StreamLLM([{"tool_calls": [call]}, {"answer": "完"}]), tools=tools)
    return list(chat.ask_stream("问题", stock_context=scope))[-1]["turn"]


def test_scoped_web_search_cannot_name_other_securities(db):
    tools = FakeTools()
    tools.db_path = db["database"]["sqlite_path"]
    _tool_turn(tools, {"name": "web_search", "args": {"query": "宁德时代 利好"}}, {"code": "600519"})
    assert tools.calls == []
    _tool_turn(tools, {"name": "web_search", "args": {"query": "贵州茅台 利好"}}, {"code": "600519"})
    assert tools.calls and tools.calls[0][0] == "web_search"


def test_index_scope_keeps_index_identity(db):
    tools = ChatTools(db)
    chat = StockChatSession({}, llm=StreamLLM([]), tools=tools)
    turn = chat.ask_stream("问题", stock_context={"code": "sh000001"})
    list(turn)
    from src.services.stock_chat import ChatTurn
    scoped = ChatTurn(question="q", stock_context={"code": "sh000001"})
    assert chat._call_tool("daily_bars", {"code": "000001"}, scoped) == "工具被拒绝：超出当前股票范围"   # 同码个股不能顶替指数
    assert chat._call_tool("chips", {"code": "sh000001"}, scoped) == STOCK_ONLY_TEXT


# ---------- Web 与机器人交替 ----------

def test_bot_and_web_share_session_object(db):
    def message(text):
        return BotMessage(platform="dingtalk", chat_id="c1", user_id="u1", user_name="张三", text=text)

    router = CommandRouter(db, pipeline=object(), chat_factory=lambda: StockChatSession(db, llm=StreamLLM([]), tools=ChatTools(db)))
    router.handle(message("600519怎么样"))
    web = _store(db)
    [summary] = web.list()
    assert summary["source"] == "dingtalk"
    record = web.get(summary["id"])
    assert record["active_stock_context"]["code"] == "600519"
    assert _targets(web.ask(summary["id"], "分析风险", expected_version=record["session_state_version"])) == [["600519"]]
    router.handle(message("改看宁德时代"))
    assert web.get(summary["id"])["active_stock_context"]["code"] == "300750"
    with pytest.raises(ChatStateConflict):   # Web 页面停留在旧版本：不覆盖机器人刚确认的对象
        web.ask(summary["id"], "风险呢", expected_version=record["session_state_version"] + 1)
    router.handle(message("风险呢"))
    turns = web.get(summary["id"])["turns"]
    assert _targets(turns[-1]) == [["300750"]] and len(turns) == 4


def test_concurrent_questions_on_one_session_are_serialized(db):
    store = _store(db)
    sid = store.create()["id"]
    threads = [threading.Thread(target=store.ask, args=(sid, question)) for question in ("分析600519", "分析300750")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    record = store.get(sid)
    assert record["session_state_version"] == 2 and [t["status"] for t in record["turns"]] == ["done", "done"]
