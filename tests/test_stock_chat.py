"""AI 问股：多轮工具调用、对话记录、工具实现。"""

from __future__ import annotations

import pytest

from src.collectors import daily_history as daily_history_mod
from src.collectors import stock_news as stock_news_mod
from src.database import db as db_module
from src.database.db import get_db_session, init_db
from src.database.models import LimitUpStock, StockDaily, StockInfo
from src.services import market_context as market_context_mod
from src.services.chat_tools import ChatTools
from src.services.stock_chat import MAX_TOOL_ROUNDS, StockChatSession
from src.services.stock_search import StockSearch


def _reset_db_engine():
    if db_module._engine:
        db_module._engine.dispose()
    db_module._engine = None
    db_module._SessionFactory = None


class ScriptedLLM:
    def __init__(self, replies):
        self.replies, self.prompts = list(replies), []

    def chat_json(self, user_message, system_message="", **kwargs):
        self.prompts.append(user_message)
        reply = self.replies.pop(0) if self.replies else {}
        if isinstance(reply, Exception):
            raise reply
        return reply


class FakeTools:
    def __init__(self):
        self.calls = []

    def call(self, name, args):
        self.calls.append((name, args))
        return {"resolve_stock": "贵州茅台(600519)", "quote": "贵州茅台(600519) 收盘 1500（+1.00%）"}.get(name, f"{name} 结果")


def test_ask_runs_tools_then_answers():
    llm = ScriptedLLM([
        {"thought": "先查代码", "tool_calls": [{"name": "resolve_stock", "args": {"query": "gzmt"}}]},
        {"tool_calls": [{"name": "quote", "args": {"code": "600519"}}, {"name": "technical", "args": {"code": "600519"}}]},
        {"answer": "**观望**：放量不足。仅供学习研究，不构成投资建议"},
    ])
    tools, progress = FakeTools(), []
    chat = StockChatSession({}, llm=llm, tools=tools)
    turn = chat.ask("茅台现在能买吗", perspective="龙回头", progress=progress.append)

    assert turn.answer.startswith("**观望**") and not turn.error
    assert [c[0] for c in tools.calls] == ["resolve_stock", "quote", "technical"]
    assert [t["label"] for t in turn.tools] == ["查找股票", "最新行情", "技术面"]
    assert progress == ["正在查询：查找股票", "正在查询：最新行情、技术面"]
    assert "【分析视角】龙回头：" in llm.prompts[0] and "【已查询的数据】" not in llm.prompts[0]
    assert "- resolve_stock(query=gzmt)：贵州茅台(600519)" in llm.prompts[1]
    assert "- quote(code=600519)：贵州茅台(600519) 收盘 1500（+1.00%）" in llm.prompts[2]

    # 追问会带上之前的对话
    llm.replies = [{"answer": "止损放在 1450。"}]
    chat.ask("止损放哪")
    assert "【之前的对话】\n用户：茅台现在能买吗\n助手：**观望**" in llm.prompts[-1]
    assert "止损放在 1450" in chat.to_markdown() and "查询：查找股票、最新行情、技术面" in chat.to_markdown()


def test_ask_forces_answer_after_max_rounds():
    llm = ScriptedLLM([{"tool_calls": [{"name": "quote", "args": {"code": "600519"}}]}] * MAX_TOOL_ROUNDS + [{"answer": "根据已有数据：观望"}])
    turn = StockChatSession({}, llm=llm, tools=FakeTools()).ask("茅台")
    assert len(llm.prompts) == MAX_TOOL_ROUNDS + 1 and "工具调用次数已用完" in llm.prompts[-1]
    assert turn.answer == "根据已有数据：观望" and len(turn.tools) == MAX_TOOL_ROUNDS

    turn = StockChatSession({}, llm=ScriptedLLM([{"tool_calls": [{"name": "quote"}]}] * 10), tools=FakeTools()).ask("茅台")
    assert "没能根据现有数据得出回答" in turn.answer


def test_ask_handles_llm_failure():
    chat = StockChatSession({}, llm=ScriptedLLM([RuntimeError("所有LLM模型均调用失败")]), tools=FakeTools())
    turn = chat.ask("茅台")
    assert turn.error and not turn.answer
    chat.llm.replies = [{"answer": "好的"}]
    chat.ask("再试一次")
    assert "【之前的对话】" not in chat.llm.prompts[-1]            # 失败的一轮不进入对话记录
    assert StockChatSession({}, llm=ScriptedLLM([{"answer": "x"}]), tools=FakeTools()).ask("q", perspective="不存在").perspective == "综合"


# ---------- 工具 ----------

@pytest.fixture
def tools(tmp_path, monkeypatch):
    path = str(tmp_path / "chat.db")
    _reset_db_engine()
    init_db(path)
    StockSearch.reset()
    monkeypatch.setattr(daily_history_mod, "ensure_daily_history", lambda *a, **k: 0)
    with get_db_session(path) as session:
        session.add(StockInfo(code="600519", name="贵州茅台", exchange="sh"))
        for i, day in enumerate(["2026-09-22", "2026-09-23", "2026-09-24"]):
            session.add(StockDaily(code="sh600519", name="贵州茅台", trade_date=day, close=1500 + i * 10, change_pct=0.5 + i,
                                   amount=5e9, turnover=0.4, circ_mv=1.9e12))
        session.add(LimitUpStock(code="600519", name="贵州茅台", trade_date="2026-09-24", continuous_days=1,
                                 first_limit_time="09:45", open_count=0, reason="白酒"))
    yield ChatTools({"database": {"sqlite_path": path}})
    StockSearch.reset()
    _reset_db_engine()


def test_chat_tools(tools, monkeypatch):
    assert tools.call("resolve_stock", {"query": "gzmt"}) == "贵州茅台(600519)"
    assert tools.call("quote", {"code": "茅台"}) == "贵州茅台(600519) 主板 2026-09-24 收盘 1520.0（+2.50%），成交 50.00 亿，换手 0.40%，流通市值 19000 亿"
    assert tools.call("daily_bars", {"code": "600519", "days": 2}) == "贵州茅台(600519) 近 2 日：09-23 1510.0(+1.5%)；09-24 1520.0(+2.5%)"
    assert "2026-09-24 1板 首封09:45 炸板0次 原因:白酒" in tools.call("limit_up_history", {"code": "600519"})
    assert tools.call("quote", {"code": "不存在"}) == "工具执行失败：找不到股票「不存在」"
    assert tools.call("quote", {}) == "工具执行失败：缺少股票代码"
    assert tools.call("buy", {"code": "600519"}).startswith("没有名为 buy 的工具")   # 不存在下单一类的工具

    monkeypatch.setattr(stock_news_mod, "get_stock_news", lambda code, refresh=False, now=None: {
        "news": [{"date": "2026-09-24 10:00", "title": "茅台提价"}],
        "notices": [{"date": "2026-09-20", "title": "关于立案的公告", "risk": "立案"}],
    })
    assert tools.call("news", {"code": "600519"}) == "贵州茅台(600519) 公告：2026-09-20 关于立案的公告【风险：立案】\n新闻：2026-09-24 茅台提价"
    monkeypatch.setattr(market_context_mod, "build_market_facts", lambda config, overview=None, news=True: type("F", (), {"text": lambda self: "大盘数据"})())
    assert tools.call("market", {}) == "大盘数据"
    assert tools.call("screening", {"code": "600519"}) == "还没有策略选股结果"
    assert tools.call("diagnosis", {"code": "600519"}) == "贵州茅台(600519) 还没有 AI 诊断记录"


def test_long_tool_result_is_truncated(tools, monkeypatch):
    monkeypatch.setattr(ChatTools, "_tool_market", lambda self, args: "数" * 3000)
    fresh = ChatTools(tools.config)
    assert fresh.call("market", {}).endswith("…（已截断）")


def test_render_chat_markdown():
    from src.desktop.markdown_render import render_chat_markdown
    from src.services.stock_chat import ChatTurn

    assert "可以这样问" in render_chat_markdown([])
    turn = ChatTurn(question="茅台能买吗", answer="观望", tools=[{"label": "最新行情"}, {"label": "最新行情"}, {"label": "技术面"}])
    text = render_chat_markdown([turn], pending="止损放哪", status="正在查询：日线走势…")
    assert "> 查询：最新行情、技术面" in text and text.endswith("**🧑 止损放哪**\n\n*正在查询：日线走势…*")
    assert "*AI 调用失败*" in render_chat_markdown([ChatTurn(question="q", error="AI 调用失败")])
