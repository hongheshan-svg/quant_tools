"""
机器人命令分发（与平台无关）

命令可以带或不带 /，中文命令后面可以不加空格（「诊断茅台」）；不是命令的消息交给 AI 问股，
同一会话（群里按人区分）30 分钟内保持上下文。所有命令只读，不能下单。
"""

from __future__ import annotations

import re
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from loguru import logger

from src.bot.models import BotMessage

SESSION_IDLE_SECONDS = 30 * 60
MAX_SESSIONS = 200
MAX_LIST_ROWS = 20
_FILLER = re.compile(r"^(一下|下|看看|看下|帮我|请)+|[\s，,。.！!？?]+$")

HELP_TEXT = """**A股量化助手**

- **诊断 茅台**：个股 AI 诊断（也可用 分析、/analyze；代码、名称、拼音首字母都行）
- **大盘**：最近一次大盘复盘和次日姿态
- **预测**：今日 AI 涨停预测
- **自选**：自选股和最近诊断；**自选 加 茅台** / **自选 删 茅台**
- **持仓**：模拟盘和实盘记账的持仓
- **状态**：数据源健康状况
- **清空**：结束当前 AI 问股对话
- 其他内容：AI 问股，例如「宁德时代最近资金流怎么样」

只读查询，不会下单。"""


@dataclass
class Command:
    names: tuple[str, ...]
    handler: Callable[[BotMessage, str, Callable[[str], None]], str]
    takes_args: bool = False


class _Session:
    def __init__(self, chat) -> None:
        self.chat = chat
        self.lock = threading.Lock()
        self.last_used = time.monotonic()


class CommandRouter:
    def __init__(self, config: dict, pipeline=None, chat_factory: Callable[[], Any] | None = None,
                 idle_seconds: float = SESSION_IDLE_SECONDS) -> None:
        self.config = config
        self._pipeline = pipeline
        self._chat_factory = chat_factory
        self.idle_seconds = idle_seconds
        self.allowed_users = {str(u) for u in (config.get("bot") or {}).get("allowed_users") or [] if str(u).strip()}
        self._sessions: OrderedDict[str, _Session] = OrderedDict()
        self._sessions_lock = threading.Lock()
        self.commands = [
            Command(("帮助", "help", "?", "？", "菜单"), self._help),
            Command(("诊断", "分析", "analyze", "diagnose"), self._diagnose, takes_args=True),
            Command(("大盘", "复盘", "market"), self._market),
            Command(("预测", "涨停预测", "predict"), self._predict),
            Command(("自选股", "自选", "watchlist"), self._watchlist, takes_args=True),
            Command(("持仓", "positions"), self._positions),
            Command(("状态", "status"), self._status),
            Command(("清空", "新对话", "reset"), self._reset),
        ]

    @property
    def pipeline(self):
        if self._pipeline is None:
            from src.services.pipeline_service import PipelineService

            self._pipeline = PipelineService(self.config)
        return self._pipeline

    # ---------- 入口 ----------

    def handle(self, message: BotMessage, progress: Callable[[str], None] | None = None) -> str:
        """处理一条消息，返回 Markdown 回复；progress(文字) 用于先回一句「正在处理」。"""
        progress = progress or (lambda _text: None)
        if self.allowed_users and message.user_id not in self.allowed_users:
            return f"没有使用权限。请管理员把你的用户 ID `{message.user_id}` 加到配置 bot.allowed_users。"
        text = message.text.strip()
        if not text:
            return HELP_TEXT
        command, args = self.match(text)
        try:
            if command:
                return command.handler(message, args, progress)
            return self._chat(message, text, progress)
        except Exception as e:
            logger.exception(f"机器人处理消息失败: {e}")
            return f"处理失败：{e}"

    def match(self, text: str) -> tuple[Command | None, str]:
        """识别命令：不带参数的命令要整句匹配（「大盘怎么样」交给问股），带参数的按前缀匹配"""
        body = text.strip().lstrip("/").strip()
        lowered = body.lower()
        for command in self.commands:
            for name in sorted(command.names, key=len, reverse=True):
                if lowered == name:
                    return command, ""
                if command.takes_args and lowered.startswith(name):
                    rest = body[len(name):]
                    if name.isascii() and rest[:1] not in (" ", ""):
                        continue  # /analyzer 这类不是命令
                    return command, rest.strip()
        return None, ""

    # ---------- 命令 ----------

    def _help(self, message: BotMessage, args: str, progress) -> str:
        return HELP_TEXT

    def _resolve(self, text: str) -> tuple[str, str] | None:
        from src.services.watchlist import WatchlistService

        query = _FILLER.sub("", text.strip())
        return WatchlistService(self.config).resolve(query) if query else None

    def _diagnose(self, message: BotMessage, args: str, progress) -> str:
        if not args:
            return "请带上股票，例如：诊断 茅台"
        resolved = self._resolve(args)
        if not resolved:
            # 「分析一下最近的行情」这类不是个股诊断，交给问股
            return self._chat(message, message.text.strip(), progress)
        code, name = resolved
        progress(f"正在诊断 {name}({code})，大约需要半分钟…")
        from src.services.stock_diagnosis import render_markdown

        result = self.pipeline.diagnose_stock(code)
        if result.get("error"):
            return f"诊断 {name}({code}) 失败：{result['error']}"
        return render_markdown(result)

    def _market(self, message: BotMessage, args: str, progress) -> str:
        from src.services.market_review import render_markdown

        review = self.pipeline.latest_market_review()
        if review and not review.get("error"):
            return f"### 大盘复盘 {review.get('trade_date', '')}\n\n{render_markdown(review)}"
        regime = self.pipeline.market_regime() or {}
        summary = regime.get("summary") or f"大盘环境：{regime.get('regime', '未知')}"
        return f"### 大盘\n\n{summary}\n\n今天还没有生成大盘复盘（收盘后自动生成）。"

    def _predict(self, message: BotMessage, args: str, progress) -> str:
        from src.services.data_query_service import DataQueryService

        rows = DataQueryService(self.pipeline.db_path).get_premarket_predictions()[:10]
        if not rows:
            return "今天还没有 AI 涨停预测。"
        lines = ["### AI 涨停预测", ""]
        for r in rows:
            verdict = f" ｜ {r['ai_verdict']}" if r.get("ai_verdict") else ""
            lines.append(f"{r.get('rank', '')}. **{r['name']}**({r['code']}) 信心 {_num(r.get('confidence'), 0)}{verdict}")
            if r.get("reason"):
                lines.append(f"   {str(r['reason'])[:80]}")
        return "\n".join(lines)

    def _watchlist(self, message: BotMessage, args: str, progress) -> str:
        for prefix, action in (("添加", "add"), ("加入", "add"), ("加", "add"), ("add", "add"),
                               ("删除", "remove"), ("移除", "remove"), ("删", "remove"), ("remove", "remove")):
            if args.lower().startswith(prefix):
                target = args[len(prefix):].strip()
                return self._watch_add(target) if action == "add" else self._watch_remove(target)
        rows = self.pipeline.watchlist_overview()
        if not rows:
            return "还没有自选股。发「自选 加 茅台」添加。"
        lines = [f"### 自选股（{len(rows)}）", ""]
        for r in rows[:MAX_LIST_ROWS]:
            quote = f"{_num(r.get('close'))} {_pct(r.get('change_pct'))}" if r.get("close") else "暂无行情"
            diag = r.get("diagnosis")
            verdict = f" ｜ {diag['action_label']} {diag.get('score', '')}分" if diag else ""
            lines.append(f"- **{r['name']}**({r['code']}) {quote}{verdict}")
        if len(rows) > MAX_LIST_ROWS:
            lines.append(f"- …共 {len(rows)} 只")
        return "\n".join(lines)

    def _watch_add(self, target: str) -> str:
        if not target:
            return "请带上股票，例如：自选 加 茅台"
        result = self.pipeline.watchlist_add(_FILLER.sub("", target))
        return f"已加入自选：{result['name']}({result['code']})" if result.get("ok") else str(result.get("error") or "添加失败")

    def _watch_remove(self, target: str) -> str:
        resolved = self._resolve(target) if target else None
        if not resolved:
            return f"找不到股票「{target}」" if target else "请带上股票，例如：自选 删 茅台"
        code, name = resolved
        return f"已移出自选：{name}({code})" if self.pipeline.watchlist_remove(code) else f"{name}({code}) 不在自选股中"

    def _positions(self, message: BotMessage, args: str, progress) -> str:
        sections = []
        paper = (self.pipeline.trading_snapshot() or {}).get("positions") or []
        from src.services.real_portfolio import RealPortfolioService

        real = (RealPortfolioService(self.config).snapshot() or {}).get("positions") or []
        for title, rows in (("模拟盘", paper), ("实盘记账", real)):
            if not rows:
                continue
            lines = [f"### {title}持仓（{len(rows)}）", ""]
            for p in rows[:MAX_LIST_ROWS]:
                pnl = p.get("unrealized_pnl") or 0
                cost = p.get("avg_cost") or 0
                pnl_pct = pnl / (cost * p["quantity"]) * 100 if cost and p.get("quantity") else None
                lines.append(f"- **{p['name']}**({p['code']}) {p['quantity']} 股，成本 {_num(cost, 3)}，现价 {_num(p.get('market_price'))}，"
                             f"浮盈 {pnl:+,.0f}（{_pct(pnl_pct)}）")
            sections.append("\n".join(lines))
        return "\n\n".join(sections) or "模拟盘和实盘记账都没有持仓。"

    def _status(self, message: BotMessage, args: str, progress) -> str:
        rows = self.pipeline.data_source_status()
        if not rows:
            return "本次启动后还没有请求过数据源。"
        failing = [r for r in rows if r.get("consecutive_failures")]
        lines = ["### 数据源状态", "", f"共 {len(rows)} 个数据源，{len(rows) - len(failing)} 个正常。"]
        for r in failing[:MAX_LIST_ROWS]:
            lines.append(f"- ⚠️ {r['dataset']} / {r['source']}：连续失败 {r['consecutive_failures']} 次 {str(r.get('last_error') or '')[:60]}")
        return "\n".join(lines)

    def _reset(self, message: BotMessage, args: str, progress) -> str:
        with self._sessions_lock:
            self._sessions.pop(message.session_key, None)
        return "已结束当前对话，下一条消息开始新的问股对话。"

    # ---------- AI 问股 ----------

    def _session(self, key: str) -> _Session:
        now = time.monotonic()
        with self._sessions_lock:
            session = self._sessions.get(key)
            if session is None or now - session.last_used > self.idle_seconds:
                session = _Session(self._new_chat())
                self._sessions[key] = session
            session.last_used = now
            self._sessions.move_to_end(key)
            while len(self._sessions) > MAX_SESSIONS:
                self._sessions.popitem(last=False)
            return session

    def _new_chat(self):
        if self._chat_factory:
            return self._chat_factory()
        from src.services.stock_chat import StockChatSession

        return StockChatSession(self.config)

    def _chat(self, message: BotMessage, question: str, progress) -> str:
        session = self._session(message.session_key)
        notified: list[str] = []

        def notify_once(text: str) -> None:  # 聊天软件里只提示一次「正在查询」，避免刷屏
            if not notified:
                notified.append(text)
                progress(text + "…")

        with session.lock:  # 同一会话的问题排队回答，保证上下文顺序
            turn = session.chat.ask(question, progress=notify_once)
        if turn.error:
            return turn.error
        return turn.answer


def _num(value: Any, digits: int = 2) -> str:
    return f"{value:.{digits}f}" if isinstance(value, (int, float)) else "--"


def _pct(value: Any) -> str:
    return f"{value:+.2f}%" if isinstance(value, (int, float)) else "--"
