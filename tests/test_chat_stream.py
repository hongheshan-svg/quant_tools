"""AI 问股流式输出与取消：会话事件流、会话仓库、SSE 接口。"""

from __future__ import annotations

import json
import threading

import pytest

from src.database import db as db_module
from src.database.db import init_db
from src.services.chat_sessions import ChatSessionStore
from src.services.stock_chat import MAX_CALLS_PER_ROUND, MAX_TOOL_ROUNDS, StockChatSession

from tests.test_api import env  # noqa: F401  (fixture)


def _reset_db_engine():
    if db_module._engine:
        db_module._engine.dispose()
    db_module._engine = None
    db_module._SessionFactory = None


def _chunks(text: str, size: int = 3):
    return [text[i:i + size] for i in range(0, len(text), size)]


class StreamLLM:
    """按轮次返回预设的 JSON 文本，并切成小块。replies 项可以是 dict、str、异常或块列表。"""

    def __init__(self, replies, size: int = 3):
        self.replies, self.size, self.prompts = list(replies), size, []

    def chat_stream(self, user_message, system_message="", temperature=None, max_tokens=None, response_format=None):
        self.prompts.append(user_message)
        reply = self.replies.pop(0) if self.replies else {"answer": "默认"}
        if isinstance(reply, Exception):
            raise reply
        if isinstance(reply, list):
            yield from reply
            return
        text = reply if isinstance(reply, str) else json.dumps(reply, ensure_ascii=False)
        yield from _chunks(text, self.size)


class JsonOnlyLLM:
    """没有 chat_stream，只有 chat_json。"""

    def __init__(self, replies):
        self.replies = list(replies)

    def chat_json(self, user_message, system_message="", **kwargs):
        return self.replies.pop(0)


class FakeTools:
    def __init__(self, on_call=None):
        self.calls, self.on_call = [], on_call

    def call(self, name, args):
        self.calls.append((name, args))
        if self.on_call:
            self.on_call(name, args)
        return f"{name} 结果" + "长" * 300


def _run(chat, question="茅台", **kwargs):
    events = list(chat.ask_stream(question, **kwargs))
    return events, events[-1]


def _deltas(events):
    return "".join(e["text"] for e in events if e["type"] == "delta")


# ---------- StockChatSession.ask_stream ----------

def test_stream_answer_only():
    chat = StockChatSession({}, llm=StreamLLM([{"answer": "**观望**：放量不足。"}]), tools=FakeTools())
    events, done = _run(chat, perspective="龙回头")
    assert done["type"] == "done" and [e["type"] for e in events].count("done") == 1
    assert _deltas(events) == "**观望**：放量不足。" == done["turn"]["answer"]
    assert sum(e["type"] == "delta" for e in events) > 1                # 确实是分块输出
    assert done["turn"]["question"] == "茅台" and done["turn"]["perspective"] == "龙回头" and not done["turn"]["error"]
    assert done["turn"]["asked_at"] and done["turn"]["tools"] == []
    assert chat.turns[-1].answer == "**观望**：放量不足。"


def test_stream_tools_then_answer():
    llm = StreamLLM([
        {"thought": "先查", "tool_calls": [{"name": "resolve_stock", "args": {"query": "gzmt"}}, {"name": "quote", "args": {"code": "600519"}}]},
        {"answer": "结论：观望"},
    ])
    tools = FakeTools()
    chat = StockChatSession({}, llm=llm, tools=tools)
    events, done = _run(chat)
    types = [e["type"] for e in events]
    assert types[-1] == "done"
    assert types.count("tool") == 2 and types.count("tool_result") == 2
    # 工具轮不产出 delta；delta 都在最后一个 tool_result 之后
    first_delta = types.index("delta")
    assert first_delta > max(i for i, t in enumerate(types) if t == "tool_result")
    tool_ev = next(e for e in events if e["type"] == "tool")
    assert tool_ev["name"] == "resolve_stock" and tool_ev["label"] == "查找股票" and tool_ev["args"] == {"query": "gzmt"}
    for e in events:
        if e["type"] == "tool_result":
            assert e["name"] and e["label"] and len(e["summary"]) <= 200 and e["summary"]
    assert any(e["type"] == "status" for e in events)
    assert _deltas(events) == "结论：观望" == done["turn"]["answer"]
    assert [t["name"] for t in done["turn"]["tools"]] == ["resolve_stock", "quote"]
    assert [c[0] for c in tools.calls] == ["resolve_stock", "quote"]


@pytest.mark.parametrize("answer", [
    "第一行\n第二行",
    '他说"买入"，路径 C:\\data',
    "中文\u4e2d\u6587与emoji 混合 / 斜杠",
    "多种：\n\t\"引号\"\\反斜杠\\\\",
])
@pytest.mark.parametrize("size", [1, 2, 3, 5])
def test_stream_escapes_split_across_chunks(answer, size):
    text = json.dumps({"answer": answer})                   # ensure_ascii=True，含 \uXXXX
    chat = StockChatSession({}, llm=StreamLLM([text], size=size), tools=FakeTools())
    events, done = _run(chat)
    assert done["turn"]["answer"] == answer.strip()
    assert _deltas(events) == done["turn"]["answer"]


def test_stream_escapes_ensure_ascii_false():
    answer = "换行\n引号\"反斜杠\\完"
    chat = StockChatSession({}, llm=StreamLLM([json.dumps({"answer": answer}, ensure_ascii=False)], size=2), tools=FakeTools())
    events, done = _run(chat)
    assert _deltas(events) == answer == done["turn"]["answer"]


def test_stream_tolerates_code_fence_and_prefix_fields():
    text = '```json\n{"thought": "直接答", "answer": "好的\\n收到"}\n```'
    chat = StockChatSession({}, llm=StreamLLM([text], size=4), tools=FakeTools())
    events, done = _run(chat)
    assert done["turn"]["answer"] == "好的\n收到" and _deltas(events) == "好的\n收到"


def test_stream_max_rounds_and_calls_limits():
    many = [{"name": "quote", "args": {"code": str(i)}} for i in range(MAX_CALLS_PER_ROUND + 4)]
    llm = StreamLLM([{"tool_calls": many}] * MAX_TOOL_ROUNDS + [{"answer": "根据已有数据：观望"}])
    tools = FakeTools()
    events, done = _run(StockChatSession({}, llm=llm, tools=tools))
    assert len(llm.prompts) == MAX_TOOL_ROUNDS + 1 and "工具调用次数已用完" in llm.prompts[-1]
    assert len(tools.calls) == MAX_TOOL_ROUNDS * MAX_CALLS_PER_ROUND
    assert sum(e["type"] == "tool" for e in events) == MAX_TOOL_ROUNDS * MAX_CALLS_PER_ROUND
    assert done["turn"]["answer"] == "根据已有数据：观望"


def test_stream_always_tool_calls_gives_fallback_answer():
    events, done = _run(StockChatSession({}, llm=StreamLLM([{"tool_calls": [{"name": "quote"}]}] * 10), tools=FakeTools()))
    assert "没能根据现有数据得出回答" in done["turn"]["answer"]
    assert events[-1]["type"] == "done"


def test_stream_includes_history_in_followup():
    llm = StreamLLM([{"answer": "先观望"}, {"answer": "止损 1450"}])
    chat = StockChatSession({}, llm=llm, tools=FakeTools())
    _run(chat, "茅台能买吗")
    _run(chat, "止损放哪")
    assert "用户：茅台能买吗\n助手：先观望" in llm.prompts[-1] and len(chat.turns) == 2


def test_stream_fallback_without_chat_stream():
    llm = JsonOnlyLLM([{"tool_calls": [{"name": "quote", "args": {"code": "1"}}]}, {"answer": "完整回答"}])
    events, done = _run(StockChatSession({}, llm=llm, tools=FakeTools()))
    deltas = [e for e in events if e["type"] == "delta"]
    assert [d["text"] for d in deltas] == ["完整回答"]
    assert done["turn"]["answer"] == "完整回答" and any(e["type"] == "tool" for e in events)


def test_stream_cancel_mid_stream_keeps_partial_answer():
    cancel = threading.Event()

    class CancelLLM:
        def chat_stream(self, user_message, system_message="", **kwargs):
            yield '{"answer": "'
            yield "你好"
            cancel.set()
            yield "世界"
            yield '"}'
            raise AssertionError("取消后不应继续读取")

    chat = StockChatSession({}, llm=CancelLLM(), tools=FakeTools())
    events, done = _run(chat, cancel=cancel)
    assert done["type"] == "done" and done["turn"]["error"] == "已取消"
    assert "世界" not in _deltas(events) and done["turn"]["answer"].startswith("你好")
    assert chat.turns[-1] is not None and chat.turns[-1].error == "已取消" and chat.turns[-1].answer.startswith("你好")


def test_stream_cancel_before_start():
    cancel = threading.Event()
    cancel.set()
    llm = StreamLLM([{"answer": "不该出现"}])
    tools = FakeTools()
    chat = StockChatSession({}, llm=llm, tools=tools)
    events, done = _run(chat, cancel=cancel)
    assert done["turn"]["error"] == "已取消" and not tools.calls and "不该出现" not in _deltas(events)
    assert len(chat.turns) == 1


def test_stream_cancel_stops_further_tool_calls():
    cancel = threading.Event()
    tools = FakeTools(on_call=lambda name, args: cancel.set())
    llm = StreamLLM([{"tool_calls": [{"name": "quote", "args": {}}, {"name": "technical", "args": {}}]}, {"answer": "x"}])
    chat = StockChatSession({}, llm=llm, tools=tools)
    events, done = _run(chat, cancel=cancel)
    assert len(tools.calls) == 1 and done["turn"]["error"] == "已取消"
    assert len(llm.prompts) == 1 and _deltas(events) == ""


def test_stream_llm_error_emits_error_and_done():
    llm = StreamLLM([RuntimeError("boom")])
    chat = StockChatSession({}, llm=llm, tools=FakeTools())
    events, done = _run(chat)
    types = [e["type"] for e in events]
    assert "error" in types and types[-1] == "done" and types.index("error") < len(types) - 1
    assert next(e for e in events if e["type"] == "error")["message"]
    assert done["turn"]["error"] and not done["turn"]["answer"]
    assert chat.turns[-1].error


def test_stream_llm_error_midstream():
    def gen():
        yield '{"answer": "半截'
        raise RuntimeError("断流")

    class BadLLM:
        def chat_stream(self, *a, **k):
            return gen()

    events, done = _run(StockChatSession({}, llm=BadLLM(), tools=FakeTools()))
    assert any(e["type"] == "error" for e in events) and events[-1]["type"] == "done" and done["turn"]["error"]


# ---------- ChatSessionStore ----------

@pytest.fixture
def store(tmp_path):
    path = str(tmp_path / "store.db")
    _reset_db_engine()
    init_db(path)
    llm_holder = {}
    s = ChatSessionStore({"database": {"sqlite_path": path}},
                         session_factory=lambda cfg: StockChatSession(cfg, llm=llm_holder["llm"], tools=FakeTools()))
    s.llm_holder = llm_holder
    yield s
    _reset_db_engine()


def test_store_ask_stream_persists(store):
    store.llm_holder["llm"] = StreamLLM([{"answer": "观望。"}])
    sid = store.create("龙回头")["id"]
    events = list(store.ask_stream(sid, "茅台能买吗"))
    assert events[-1]["type"] == "done" and _deltas(events) == "观望。"
    record = store.get(sid)
    assert len(record["turns"]) == 1 and record["turns"][0]["answer"] == "观望。" and record["turns"][0]["question"] == "茅台能买吗"
    assert record["title"] == "茅台能买吗"
    assert store.cancel(sid) is False                      # 已结束


def test_store_ask_stream_unknown_session(store):
    store.llm_holder["llm"] = StreamLLM([])
    with pytest.raises(KeyError):
        list(store.ask_stream("nope", "x"))
    assert store.cancel("nope") is False


def test_store_cancel_running_stream(store):
    started, release = threading.Event(), threading.Event()

    class SlowLLM:
        def chat_stream(self, user_message, system_message="", **kwargs):
            yield '{"answer": "'
            yield "开头"
            started.set()
            release.wait(5)
            yield "后续内容"
            yield '"}'

    store.llm_holder["llm"] = SlowLLM()
    sid = store.create()["id"]
    out = []
    t = threading.Thread(target=lambda: out.extend(store.ask_stream(sid, "问")))
    t.start()
    assert started.wait(5)
    assert store.cancel(sid) is True
    release.set()
    t.join(5)
    assert not t.is_alive()
    assert out[-1]["type"] == "done" and out[-1]["turn"]["error"] == "已取消" and "后续内容" not in _deltas(out)
    turns = store.get(sid)["turns"]
    assert len(turns) == 1 and turns[0]["error"] == "已取消" and turns[0]["answer"].startswith("开头")
    assert store.cancel(sid) is False


# ---------- API ----------

def _events(text: str) -> list[dict]:
    blocks = [b for b in text.split("\n\n") if b.strip()]
    assert all(b.startswith("data: ") for b in blocks), text
    return [json.loads(b[len("data: "):]) for b in blocks]


def test_api_ask_stream_sse(env):  # noqa: F811
    client, app, _ = env
    app.state.chat_store._factory = lambda cfg: StockChatSession(
        cfg, llm=StreamLLM([{"answer": "观望：量能不足。\n仅供学习研究"}]), tools=FakeTools())
    session = client.post("/api/v1/chat/sessions", json={"perspective": "龙回头"}).json()
    with client.stream("POST", f"/api/v1/chat/sessions/{session['id']}/ask/stream", json={"question": "茅台能买吗"}) as res:
        assert res.status_code == 200 and res.headers["content-type"].startswith("text/event-stream")
        raw = "".join(res.iter_text())
    assert "\\u" not in raw and "观望" in raw                      # 中文不转义
    events = _events(raw)
    assert events[-1]["type"] == "done" and sum(e["type"] == "done" for e in events) == 1
    assert _deltas(events) == events[-1]["turn"]["answer"] == "观望：量能不足。\n仅供学习研究"
    record = client.get(f"/api/v1/chat/sessions/{session['id']}").json()
    assert record["turns"][0]["answer"].startswith("观望") and record["turns"][0]["perspective"] == "龙回头"


def test_api_ask_stream_with_tools_and_fallback_llm(env):  # noqa: F811
    client, app, _ = env
    llm = JsonOnlyLLM([{"tool_calls": [{"name": "quote", "args": {"code": "600519"}}]}, {"answer": "整段回答"}])
    app.state.chat_store._factory = lambda cfg: StockChatSession(cfg, llm=llm, tools=FakeTools())
    sid = client.post("/api/v1/chat/sessions", json={}).json()["id"]
    res = client.post(f"/api/v1/chat/sessions/{sid}/ask/stream", json={"question": "茅台"})
    events = _events(res.text)
    assert {"tool", "tool_result", "delta", "done"} <= {e["type"] for e in events}
    assert events[-1]["turn"]["answer"] == "整段回答"


def test_api_ask_stream_unknown_session_404(env):  # noqa: F811
    client, _, _ = env
    assert client.post("/api/v1/chat/sessions/none/ask/stream", json={"question": "x"}).status_code == 404


def test_api_cancel_without_stream(env):  # noqa: F811
    client, _, _ = env
    sid = client.post("/api/v1/chat/sessions", json={}).json()["id"]
    assert client.post(f"/api/v1/chat/sessions/{sid}/cancel").json() == {"ok": False}
    assert client.post("/api/v1/chat/sessions/none/cancel").json() == {"ok": False}
