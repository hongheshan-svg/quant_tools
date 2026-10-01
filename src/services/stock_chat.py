"""
AI 问股（参考 daily_stock_analysis 的 Agent 策略问股）

多轮对话。每个问题最多 3 轮：AI 先决定调用哪些工具（行情、日线、技术面、资金流、筹码、业绩、新闻公告、涨停记录、
主线、大盘、策略选股、持仓、历史诊断），拿到结果后再回答；3 轮用完还没回答时要求直接根据已有数据回答。
工具协议用 JSON（{"tool_calls": [...]} 或 {"answer": "..."}），不依赖各家模型的 function calling，所有兼容 OpenAI
接口的模型都能用。可以选择策略视角（打板接力、龙回头、主线补涨等），回答会按该视角的判断标准展开。
"""

from __future__ import annotations

import json
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Callable, Iterator

from loguru import logger

from src.analyzers.llm_client import NO_MODEL_HINT
from src.config_loader import load_config
from src.services.chat_tools import TOOL_LABELS, ChatTools, tools_prompt
from src.services.report_language import report_language
from src.services.strategy_skills import DEFAULT_SKILL, get_skill, load_skills

MAX_TOOL_ROUNDS = 3
MAX_CALLS_PER_ROUND = 6
HISTORY_TURNS = 6          # 带给 AI 的最近几轮对话
HISTORY_CHARS = 800        # 每条历史消息最多带多少字


class AnswerStreamer:
    """增量解析模型输出的 JSON 文本：识别顶层 "answer" 字符串，边到达边解码（处理转义和跨块边界）。

    feed(chunk) 返回本块新解码出的回答文本；tool_calls 等其他字段不产出任何文本。
    """

    _SIMPLE = {"n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f", "/": "/", "\\": "\\", '"': '"'}

    def __init__(self) -> None:
        self.depth = 0
        self.in_str = False
        self.esc = False
        self.buf: list[str] = []          # 当前字符串（非 answer）内容
        self.last_key = ""                # 顶层最近一个刚结束的字符串
        self.colon_key = ""               # 冒号之前的键（值位置）
        self.answer_mode = False
        self.answer_done = False
        self.uni: str | None = None       # 正在收集的 \uXXXX 十六进制
        self.high: int | None = None      # 等待低位的高位代理
        self.emitted = False

    def feed(self, chunk: str) -> str:
        out: list[str] = []
        for ch in chunk:
            if self.answer_mode:
                self._answer_char(ch, out)
            elif self.in_str:
                self._plain_str_char(ch)
            else:
                self._outside_char(ch)
        text = "".join(out)
        if text:
            self.emitted = True
        return text

    def _outside_char(self, ch: str) -> None:
        if ch == '"':
            if self.depth == 1 and self.colon_key == "answer" and not self.answer_done:
                self.answer_mode = True
            else:
                self.in_str, self.esc, self.buf = True, False, []
        elif ch in "{[":
            self.depth += 1
        elif ch in "}]":
            self.depth -= 1
            self.colon_key = ""
        elif self.depth == 1:
            if ch == ":":
                self.colon_key = self.last_key
            elif ch == ",":
                self.colon_key = self.last_key = ""

    def _plain_str_char(self, ch: str) -> None:
        if self.esc:
            self.esc = False
            self.buf.append(ch)
        elif ch == "\\":
            self.esc = True
        elif ch == '"':
            self.in_str = False
            if self.depth == 1 and not self.colon_key:
                self.last_key = "".join(self.buf)
            elif self.depth == 1:
                self.colon_key = ""      # 值字符串结束
        else:
            self.buf.append(ch)

    def _answer_char(self, ch: str, out: list[str]) -> None:
        if self.uni is not None:
            self.uni += ch
            if len(self.uni) < 4:
                return
            try:
                code = int(self.uni, 16)
            except ValueError:
                code = 0xFFFD
            self.uni = None
            if 0xD800 <= code < 0xDC00:
                self.high = code
            elif 0xDC00 <= code < 0xE000 and self.high is not None:
                out.append(chr(0x10000 + ((self.high - 0xD800) << 10) + (code - 0xDC00)))
                self.high = None
            else:
                out.append(chr(code))
                self.high = None
            return
        if self.esc:
            self.esc = False
            if ch == "u":
                self.uni = ""
            else:
                out.append(self._SIMPLE.get(ch, ch))
            return
        if ch == "\\":
            self.esc = True
        elif ch == '"':
            self.answer_mode = False
            self.answer_done = True
            self.colon_key = ""
        else:
            out.append(ch)


def parse_reply(text: str) -> dict[str, Any] | None:
    """把模型的完整输出解析成 JSON 对象；解析不了返回 None。"""
    t = (text or "").strip()
    start, end = t.find("{"), t.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(t[start:end + 1])
    except ValueError:
        try:
            import json_repair

            data = json_repair.loads(t[start:end + 1])
        except Exception:
            return None
    return data if isinstance(data, dict) else None


def perspectives() -> dict[str, str]:
    """display_name → 一句话说明（内置 + 自定义策略，按优先级排序）。"""
    return {s.display_name: s.description for s in load_skills()}



def normalize_perspective(value: str | None) -> str:
    """策略名、中文名或别名 → 中文名；空值返回「综合」，未知值回退到「综合」并记 warning。"""
    if not value or not str(value).strip():
        return DEFAULT_SKILL
    skill = get_skill(value)
    if skill is None:
        logger.warning(f"未知的问股策略「{value}」，已改用「{DEFAULT_SKILL}」")
        return DEFAULT_SKILL
    return skill.display_name


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



def _failure_text(error: Exception) -> str:
    """模型调用失败时给用户看的说明：没配置模型时直接说去哪里填，其余情况笼统提示"""
    return NO_MODEL_HINT if NO_MODEL_HINT in str(error) else "AI 调用失败，请检查 AI 设置或稍后重试"

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

    def _language_note(self) -> str:
        """英文模式下追加的回答语言指令（JSON 协议的键和工具名不变）。"""
        if report_language(self.config) != "en":
            return ""
        return ("\n\nAnswer in English: write the \"answer\" markdown (conclusion, key data, risks, action points and the "
                "disclaimer \"For study and research only; not investment advice\") in English. Keep the JSON keys "
                "(\"thought\", \"tool_calls\", \"answer\", \"name\", \"args\") and tool names unchanged.")

    def clear(self) -> None:
        self.turns.clear()

    def ask(self, question: str, perspective: str = "综合", progress: Callable[[str], None] | None = None) -> ChatTurn:
        """回答一个问题（会带上之前的对话）；progress(提示文字) 用于界面显示正在调用的工具。"""
        turn = ChatTurn(question=question.strip(), perspective=normalize_perspective(perspective))
        system = SYSTEM_PROMPT.format(tools=tools_prompt()) + self._language_note()
        for round_no in range(1, MAX_TOOL_ROUNDS + 2):
            final = round_no > MAX_TOOL_ROUNDS
            try:
                reply = self.llm.chat_json(user_message=self._user_message(turn, final), system_message=system)
            except Exception as e:
                logger.error(f"AI 问股调用失败: {e}")
                turn.error = _failure_text(e)
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

    def _stream_llm(self, user_message: str, system: str, cancel: threading.Event | None,
                    streamer: AnswerStreamer) -> Iterator[Any]:
        """流式读取一轮模型输出：产出 delta 文本；最后产出 (True, 完整文本)（被取消时 (False, 已收到文本)）。"""
        stream = getattr(self.llm, "chat_stream", None)
        if stream is None:
            reply = self.llm.chat_json(user_message=user_message, system_message=system)
            yield (True, json.dumps(reply or {}, ensure_ascii=False))
            return
        parts: list[str] = []
        it = stream(user_message, system, response_format="json")
        try:
            for chunk in it:
                if cancel is not None and cancel.is_set():
                    yield (False, "".join(parts))
                    return
                if not chunk:
                    continue
                parts.append(chunk)
                delta = streamer.feed(chunk)
                if delta:
                    yield delta
        finally:
            close = getattr(it, "close", None)
            if close:
                close()
        if cancel is not None and cancel.is_set():
            yield (False, "".join(parts))
            return
        yield (True, "".join(parts))

    def ask_stream(self, question: str, perspective: str = "综合",
                   cancel: threading.Event | None = None) -> Iterator[dict[str, Any]]:
        """流式回答：产出 status / tool / tool_result / delta / error 事件，最后一定是 done（含完整 turn）。"""
        turn = ChatTurn(question=question.strip(), perspective=normalize_perspective(perspective))
        system = SYSTEM_PROMPT.format(tools=tools_prompt()) + self._language_note()
        streamed: list[str] = []          # 已经作为 delta 产出的回答文本
        appended = False
        cancelled = lambda: cancel is not None and cancel.is_set()  # noqa: E731

        def finish() -> dict[str, Any]:
            nonlocal appended
            if not appended:
                self.turns.append(turn)
                appended = True
            data = asdict(turn)
            data["tools"] = [{k: v for k, v in t.items() if k != "result"} for t in turn.tools]
            return {"type": "done", "turn": data}

        try:
            for round_no in range(1, MAX_TOOL_ROUNDS + 2):
                final = round_no > MAX_TOOL_ROUNDS
                if cancelled():
                    turn.error = "已取消"
                    break
                yield {"type": "status", "text": "思考中"}
                streamer = AnswerStreamer()
                full: str | None = None
                ok = True
                try:
                    for item in self._stream_llm(self._user_message(turn, final), system, cancel, streamer):
                        if isinstance(item, tuple):
                            ok, full = item
                        else:
                            streamed.append(item)
                            yield {"type": "delta", "text": item}
                except Exception as e:
                    logger.error(f"AI 问股调用失败: {e}")
                    turn.error = _failure_text(e)
                    yield {"type": "error", "message": turn.error}
                    break
                if not ok:
                    turn.error = "已取消"
                    turn.answer = "".join(streamed).strip()
                    break
                reply = parse_reply(full or "")
                if reply is None and full and "{" not in full:
                    reply = {"answer": full}        # 模型没按 JSON 协议，直接当回答
                answer = str((reply or {}).get("answer") or "").strip()
                calls = [c for c in (reply or {}).get("tool_calls") or [] if isinstance(c, dict) and c.get("name")]
                if answer or final or not calls:
                    turn.answer = answer or "".join(streamed).strip() or "没能根据现有数据得出回答，请换个问法或指定股票代码。"
                    if not streamed:
                        yield {"type": "delta", "text": turn.answer}
                    break
                calls = calls[:MAX_CALLS_PER_ROUND]
                yield {"type": "status", "text": "正在查询：" + "、".join(TOOL_LABELS.get(c["name"], c["name"]) for c in calls)}
                for call in calls:
                    if cancelled():
                        break
                    args = call.get("args") if isinstance(call.get("args"), dict) else {}
                    label = TOOL_LABELS.get(call["name"], call["name"])
                    yield {"type": "tool", "name": call["name"], "label": label, "args": args}
                    result = self.tools.call(call["name"], args)
                    turn.tools.append({"name": call["name"], "label": label, "args": args, "result": result})
                    yield {"type": "tool_result", "name": call["name"], "label": label, "summary": str(result)[:200]}
                if cancelled():
                    turn.error = "已取消"
                    turn.answer = "".join(streamed).strip()
                    break
        except GeneratorExit:
            turn.error = turn.error or "已取消"
            turn.answer = turn.answer or "".join(streamed).strip()
            if not appended:
                self.turns.append(turn)
            raise
        yield finish()

    @staticmethod
    def _perspective_text(perspective: str) -> str:
        skill = get_skill(perspective)
        if skill is None:
            return f"【分析视角】{perspective}"
        text = f"【分析视角】{skill.display_name}：{skill.description}\n{skill.instructions}"
        if skill.market_regimes:
            text += (f"\n该策略适配的大盘环境：{'、'.join(skill.market_regimes)}。"
                     "若当前大盘环境（用 market 工具确认）不在其中，回答时要明确提示环境不匹配的风险。")
        return text

    def _user_message(self, turn: ChatTurn, final: bool) -> str:
        parts = [f"当前时间：{datetime.now():%Y-%m-%d %H:%M}", self._perspective_text(turn.perspective)]
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
