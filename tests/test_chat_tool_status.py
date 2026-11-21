"""问股工具调用状态：只按工具自己报告的失败判定，不按结果正文里的关键词猜。"""
import json

import pytest

from src.services.chat_tools import STOCK_ONLY_TEXT, tool_status
from src.services.stock_chat import StockChatSession
from tests.test_chat_stream import FakeTools, StreamLLM


@pytest.mark.parametrize("result,expected", [
    ("【新闻】某公司重组失败，董事会拒绝收购要约", "completed"),
    ("没有找到相关数据", "completed"),
    ("", "completed"),
    (json.dumps({"rows": [], "note": "部分数据缺失"}, ensure_ascii=False), "completed"),
    (json.dumps({"status": "ok", "data": [1]}), "completed"),
    ("工具执行失败：连接被重置", "failed"),
    ("工具被拒绝：超出当前股票范围", "failed"),
    ("没有名为 foo 的工具，可用：quote", "failed"),
    (STOCK_ONLY_TEXT, "failed"),
    (json.dumps({"status": "unavailable", "reason": "provider_unavailable"}), "failed"),
    (json.dumps({"status": "invalid", "reason": "invalid_arguments"}), "failed"),
    (json.dumps({"error": "rate limited"}), "failed"),
    (json.dumps({"success": False, "data": None}), "failed"),
    ("工具执行失败：数据阶段预算已耗尽", "timeout"),
    ("工具执行失败：请求超时", "timeout"),
    ("{截断的 JSON", "completed"),
])
def test_tool_status(result, expected):
    assert tool_status(result) == expected


class ResultTools(FakeTools):
    def __init__(self, result):
        super().__init__()
        self.result = result

    def call(self, name, args):
        super().call(name, args)
        return self.result


@pytest.mark.parametrize("result,expected", [
    ("贵州茅台：公告称收购失败，股东大会拒绝议案", "completed"),
    (json.dumps({"status": "unavailable", "reason": "provider_unavailable"}), "failed"),
])
def test_stream_stage_status_follows_tool_outcome(result, expected):
    llm = StreamLLM([{"tool_calls": [{"name": "quote", "args": {"code": "600519"}}]}, {"answer": "结论"}])
    chat = StockChatSession({}, llm=llm, tools=ResultTools(result))
    events = list(chat.ask_stream("茅台怎么样"))
    tool_result = next(e for e in events if e["type"] == "tool_result")
    assert tool_result["status"] == expected
    final = {e["stage_id"]: e for e in chat.turns[-1].stage_events}.values()
    assert [e["status"] for e in final if e.get("name") == tool_result["label"]] == [expected]
