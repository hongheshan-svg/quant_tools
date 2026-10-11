"""问股不把术语缩写当美股代码（A股、K线、MACD、PE、CPI…），显式 US: 前缀仍可用；工具参数同样拒绝。全部离线。"""

import json

import pytest

from src.services.stock_chat import StockChatSession
from src.services.web_intent import resolve
from src.utils.stock_code import is_ticker_jargon, overseas_identity
from tests.test_chat_stream import FakeTools, StreamLLM
from tests.test_web_intent_stages import names  # noqa: F401


@pytest.mark.parametrize("token,following,expected", [
    ("MACD", "", True), ("PE", "", True), ("CPI", "", True), ("TTM", "", True), ("A", "股", True), ("K", "线", True),
    ("AH", "股", True), ("T", "+0", True), ("ma", "", True), ("AAPL", "", False), ("TSLA", "", False), ("BRK.B", "", False),
    ("US:MA", "", False), ("600519", "", False),
])
def test_ticker_jargon(token, following, expected):
    assert is_ticker_jargon(token, following) is expected


def test_explicit_us_prefix_still_resolves():
    assert overseas_identity("US:MA").code == "MA" and overseas_identity("MA.US").code == "MA"


@pytest.mark.parametrize("question,expected", [
    ("茅台的PE和MACD怎么样", [["600519"]]),
    ("分析600519的TTM营收和KDJ", [["600519"]]),
    ("A股今天怎么样", [[]]),
    ("最近CPI和LPR对股市影响", [[]]),
])
def test_abbreviations_are_not_targets(names, question, expected):
    assert [[t["code"] for t in task["targets"]] for task in resolve(question, "unused")["tasks"]] == expected


def test_macd_screening_only_screens(names):
    assert [task["kind"] for task in resolve("用MACD选股", "unused")["tasks"]] == ["strategy_screening"]


def _tool_turn(tools, call, scope=None):
    chat = StockChatSession({}, llm=StreamLLM([{"tool_calls": [call]}, {"answer": "完"}]), tools=tools)
    return list(chat.ask_stream("问题", stock_context=scope))[-1]["turn"]


def test_jargon_tool_arguments_are_rejected():
    tools = FakeTools()
    turn = _tool_turn(tools, {"name": "quote", "args": {"code": "MACD"}})
    assert tools.calls == [] and "工具被拒绝" in json.dumps(turn, ensure_ascii=False)
