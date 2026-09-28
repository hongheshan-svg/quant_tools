"""
AI 问股（参考 daily_stock_analysis 的 Agent 策略问股）

多轮对话。每个问题最多 3 轮：AI 先决定调用哪些工具（行情、日线、技术面、资金流、筹码、业绩、新闻公告、涨停记录、
主线、大盘、策略选股、持仓、历史诊断），拿到结果后再回答；3 轮用完还没回答时要求直接根据已有数据回答。
工具协议用 JSON（{"tool_calls": [...]} 或 {"answer": "..."}），不依赖各家模型的 function calling，所有兼容 OpenAI
接口的模型都能用。可以选择策略视角（打板接力、龙回头、主线补涨等），回答会按该视角的判断标准展开。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

from loguru import logger

from src.config_loader import load_config
from src.services.chat_tools import TOOL_LABELS, ChatTools, tools_prompt

MAX_TOOL_ROUNDS = 3
MAX_CALLS_PER_ROUND = 6
HISTORY_TURNS = 6          # 带给 AI 的最近几轮对话
HISTORY_CHARS = 800        # 每条历史消息最多带多少字

PERSPECTIVES: dict[str, str] = {
    "综合": "综合技术面、资金、题材和大盘给出判断。",
    "打板接力": "按打板接力的标准看：封板时间与炸板次数、连板高度与晋级率、所属主线的阶段和龙头地位、次日溢价预期；高位分歧和亏钱效应扩散时回避。",
    "龙回头": "按龙回头的标准看：前期是否为多次涨停的龙头、回调幅度（8%~25% 较理想）、是否缩量回踩 MA10/MA20、主线是否仍在；放量破位则放弃。",
    "主线补涨": "按主线补涨的标准看：所属主线是否仍在启动/加速/发酵阶段、该股在主线中的辨识度、龙头是否仍强、补涨空间和位置。",
    "放量突破": "按放量突破的标准看：是否突破 20 日平台或前高、量能是否放大到 2 倍以上、收盘是否强势、突破后乖离是否过大。",
    "缩量回踩": "按趋势回踩的标准看：均线是否多头排列、回踩 MA10/MA20 时是否缩量、中期涨幅是否过大、支撑位和止损位。",
    "超跌反弹": "按超跌反弹的标准看：前期跌幅、是否放量企稳、有无利空未出尽（公告、业绩）、反弹目标和止损。",
    "事件驱动": "按事件驱动的标准看：新闻和公告中的催化事件、事件的持续性和市场认可度、是否已经被充分反映在股价中。",
}

SYSTEM_PROMPT = """你是 A 股短线投研助手，通过调用工具获取数据来回答用户关于个股、板块和大盘的问题。

规则：
- 涉及具体股票、价格、涨跌、公告等事实时必须先用工具查，不要凭记忆编造；工具没查到的就说没有数据。
- 用户给的股票名称、简称或拼音不确定时，先用 resolve_stock 查到代码。
- 每轮可以同时调用多个工具（最多 6 个），不要重复调用已经有结果的工具；数据足够时直接回答。
- 回答用中文 markdown：先给结论（看多 / 观望 / 看空，一句话理由），再列关键数据和风险，最后给出操作要点（买点、止损、仓位）。
- 结论要和大盘环境一致：冰点不建议开新仓，防守时控制仓位；公告有立案、退市风险等严重风险时不建议买入。
- 回答末尾注明「仅供学习研究，不构成投资建议」。

可用工具：
{tools}

只返回 JSON，二选一：
{{"thought": "为什么调用这些工具（一句话）", "tool_calls": [{{"name": "quote", "args": {{"code": "600519"}}}}]}}
{{"answer": "给用户的回答（markdown）"}}"""


@dataclass
class ChatTurn:
    question: str
    answer: str = ""
    perspective: str = "综合"
    tools: list[dict[str, Any]] = field(default_factory=list)   # [{"name", "label", "args", "result"}]
    error: str = ""
    asked_at: str = field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M"))


class StockChatSession:
    def __init__(self, config: dict | None = None, llm=None, tools: ChatTools | None = None):
        self.config = config or load_config()
        self._llm = llm
        self.tools = tools or ChatTools(self.config)
        self.turns: list[ChatTurn] = []

    @property
    def llm(self):
        if self._llm is None:
            from src.analyzers.llm_client import LLMClient

            self._llm = LLMClient(self.config.get("llm", {}))
        return self._llm

    def clear(self) -> None:
        self.turns.clear()

    def ask(self, question: str, perspective: str = "综合", progress: Callable[[str], None] | None = None) -> ChatTurn:
        """回答一个问题（会带上之前的对话）；progress(提示文字) 用于界面显示正在调用的工具。"""
        turn = ChatTurn(question=question.strip(), perspective=perspective if perspective in PERSPECTIVES else "综合")
        system = SYSTEM_PROMPT.format(tools=tools_prompt())
        for round_no in range(1, MAX_TOOL_ROUNDS + 2):
            final = round_no > MAX_TOOL_ROUNDS
            try:
                reply = self.llm.chat_json(user_message=self._user_message(turn, final), system_message=system)
            except Exception as e:
                logger.error(f"AI 问股调用失败: {e}")
                turn.error = "AI 调用失败，请检查 AI 设置或稍后重试"
                break
            answer = str((reply or {}).get("answer") or "").strip()
            calls = [c for c in (reply or {}).get("tool_calls") or [] if isinstance(c, dict) and c.get("name")]
            if answer or final or not calls:
                turn.answer = answer or "没能根据现有数据得出回答，请换个问法或指定股票代码。"
                break
            if progress:
                progress("正在查询：" + "、".join(TOOL_LABELS.get(c["name"], c["name"]) for c in calls[:MAX_CALLS_PER_ROUND]))
            for call in calls[:MAX_CALLS_PER_ROUND]:
                args = call.get("args") if isinstance(call.get("args"), dict) else {}
                turn.tools.append({"name": call["name"], "label": TOOL_LABELS.get(call["name"], call["name"]),
                                   "args": args, "result": self.tools.call(call["name"], args)})
        self.turns.append(turn)
        return turn

    def _user_message(self, turn: ChatTurn, final: bool) -> str:
        parts = [f"当前时间：{datetime.now():%Y-%m-%d %H:%M}", f"【分析视角】{turn.perspective}：{PERSPECTIVES[turn.perspective]}"]
        history = [t for t in self.turns if t.answer][-HISTORY_TURNS:]
        if history:
            parts.append("【之前的对话】\n" + "\n".join(
                f"用户：{t.question[:HISTORY_CHARS]}\n助手：{t.answer[:HISTORY_CHARS]}" for t in history
            ))
        parts.append(f"【本轮问题】{turn.question}")
        if turn.tools:
            parts.append("【已查询的数据】\n" + "\n".join(
                f"- {t['name']}({', '.join(f'{k}={v}' for k, v in t['args'].items())})：{t['result']}" for t in turn.tools
            ))
        if final:
            parts.append("工具调用次数已用完，请直接根据已有数据回答，返回 {\"answer\": ...}。")
        return "\n\n".join(parts)

    def to_markdown(self) -> str:
        """整段对话导出为 markdown。"""
        lines = [f"# AI 问股记录（{datetime.now():%Y-%m-%d %H:%M}）"]
        for t in self.turns:
            lines.append(f"## 问：{t.question}\n\n*{t.asked_at}｜视角：{t.perspective}*")
            if t.tools:
                lines.append("查询：" + "、".join(dict.fromkeys(x["label"] for x in t.tools)))
            lines.append(t.answer or f"（{t.error}）")
        return "\n\n".join(lines)
