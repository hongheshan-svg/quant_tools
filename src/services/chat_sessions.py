"""
AI 问股会话的持久化（Web 和聊天机器人共用）：每个会话一行（chat_session 表），问答记录存成 JSON，
追问时按记录恢复 StockChatSession，回答后写回。

会话状态由服务端保存（对齐 daily_stock_analysis 的问股会话状态）：
- active_stock_context：主讨论对象（代码、名称、类型、确认来源和时间），由 web_intent 的对象规则确认；
- session_state_version：每接受一个问题加 1。客户端带上已知版本而版本已变化时返回冲突，不自动重发；
- session_state.selected_skill_ids：补充策略选择，None 为未选择，[] 为显式清空，列表为已保存的选择。

提问先在一个事务里写入这一轮问题（status=running）、主对象和策略选择，成功后发出 accepted 事件再执行分析；
之后的失败、取消或断开不撤销已接受的状态，最终回答按 message_id 写回同一轮。会话被删除后迟到的回答直接丢弃。
"""

from __future__ import annotations

import json
import threading
import uuid
from copy import deepcopy
from dataclasses import asdict, fields, is_dataclass
from datetime import datetime, timedelta
from typing import Any, Callable, Iterator

from sqlalchemy import func

from src.config_loader import load_config
from src.database.db import get_db_session
from src.database.models import ChatSessionRecord
from src.services.stock_chat import ChatTurn, StockChatSession

TITLE_CHARS = 40
CONFLICT_CODE = "session_state_conflict"
CONFLICT_TEXT = "会话已在其他窗口或机器人中更新，请刷新后重试"
INTERRUPTED_TEXT = "回答被中断（服务重启或连接断开），请重新提问"
_TURN_FIELDS = {f.name for f in fields(ChatTurn)}


class ChatStateConflict(RuntimeError):
    """会话状态版本与客户端已知版本不一致，或准备期间已有其他问题被接受。"""

    code = CONFLICT_CODE

    def __init__(self, message: str = CONFLICT_TEXT):
        super().__init__(message)


def _turn(data: dict) -> ChatTurn:
    return ChatTurn(**{key: value for key, value in data.items() if key in _TURN_FIELDS})


class ChatSessionStore:
    _locks: dict[str, threading.Lock] = {}
    _locks_guard = threading.Lock()
    _cancels: dict[str, threading.Event] = {}

    def __init__(self, config: dict | None = None, session_factory: Callable[[dict], StockChatSession] | None = None):
        self.config = config or load_config()
        self.db_path = self.config.get("database", {}).get("sqlite_path", "data/quant.db")
        self._factory = session_factory or (lambda cfg: StockChatSession(cfg))

    @classmethod
    def _lock(cls, session_id: str) -> threading.Lock:
        with cls._locks_guard:
            return cls._locks.setdefault(session_id, threading.Lock())

    # ---------- 会话 ----------

    def create(self, perspective: str = "综合", *, source: str | None = None, source_key: str | None = None) -> dict[str, Any]:
        session_id = uuid.uuid4().hex
        with get_db_session(self.db_path) as session:
            session.add(ChatSessionRecord(id=session_id, title="新会话", perspective=perspective, turns_json="[]",
                                          state_version=0, source=source, source_key=source_key))
        return self.get(session_id)

    def list(self, limit: int = 50) -> list[dict[str, Any]]:
        with get_db_session(self.db_path) as session:
            rows = session.query(ChatSessionRecord).order_by(ChatSessionRecord.updated_at.desc()).limit(limit).all()
            return [{"id": r.id, "title": r.title, "perspective": r.perspective, "turns": len(json.loads(r.turns_json or "[]")),
                     "updated_at": r.updated_at.strftime("%Y-%m-%d %H:%M") if r.updated_at else "", "source": r.source or "web"}
                    for r in rows]

    def _load(self, session_id: str) -> dict[str, Any] | None:
        """数据库里的原始记录（不做展示转换）。"""
        with get_db_session(self.db_path) as session:
            r = session.get(ChatSessionRecord, session_id)
            if r is None:
                return None
            return {"id": r.id, "title": r.title, "perspective": r.perspective, "turns": json.loads(r.turns_json or "[]"),
                    "updated_at": r.updated_at.strftime("%Y-%m-%d %H:%M") if r.updated_at else "",
                    "source": r.source or "web",
                    "active": json.loads(r.active_stock_json) if r.active_stock_json else None,
                    "version": r.state_version or 0,
                    "skills": json.loads(r.skills_json) if r.skills_json is not None else None}

    def get(self, session_id: str) -> dict[str, Any] | None:
        """会话详情：问答记录和服务端会话状态。读取不修改业务状态。"""
        record = self._load(session_id)
        if record is None:
            return None
        turns = record["turns"]
        # 本进程没有在回答这个会话、最后一轮却还是 running：服务重启或连接断开，显示为中断（只改展示，不写回）。
        if turns and turns[-1].get("status") == "running" and not self._lock(session_id).locked():
            turns[-1] = {**turns[-1], "status": "interrupted", "error": turns[-1].get("error") or INTERRUPTED_TEXT}
        return {"id": record["id"], "title": record["title"], "perspective": record["perspective"], "turns": turns,
                "updated_at": record["updated_at"], "source": record["source"], **self._state(record)}

    @staticmethod
    def _state(record: dict[str, Any]) -> dict[str, Any]:
        return {"active_stock_context": record["active"], "session_state_version": record["version"],
                "session_state": {"selected_skill_ids": record["skills"]}}

    def delete(self, session_id: str) -> bool:
        with get_db_session(self.db_path) as session:
            return session.query(ChatSessionRecord).filter(ChatSessionRecord.id == session_id).delete() > 0

    # ---------- 机器人会话 ----------

    def session_for_source(self, source: str, source_key: str, idle_seconds: float, perspective: str = "综合") -> str:
        """机器人会话（平台:会话:用户）对应的问股会话：最近 idle_seconds 内有活动（含在 Web 里继续）就沿用，否则新建。"""
        cutoff = datetime.now() - timedelta(seconds=idle_seconds)
        with get_db_session(self.db_path) as session:
            rows = (session.query(ChatSessionRecord).filter(ChatSessionRecord.source_key == source_key)
                    .order_by(ChatSessionRecord.updated_at.desc()).all())
            if rows and rows[0].updated_at and rows[0].updated_at >= cutoff:
                return rows[0].id
            for row in rows:  # 过期的会话保留在列表里，只是不再接收机器人消息
                row.source_key = None
        return self.create(perspective, source=source, source_key=source_key)["id"]

    def end_source(self, source_key: str) -> None:
        """机器人「清空」：结束当前会话，下一条消息开始新会话。"""
        with get_db_session(self.db_path) as session:
            session.query(ChatSessionRecord).filter(ChatSessionRecord.source_key == source_key).update(
                {ChatSessionRecord.source_key: None}, synchronize_session=False)

    # ---------- 提问 ----------

    def _restore(self, record: dict[str, Any]) -> StockChatSession:
        """按已完成的问答恢复对话上下文；回答中或被中断的一轮不进入模型历史。"""
        chat = self._factory(self.config)
        chat.turns = [_turn(t) for t in record["turns"] if t.get("status", "done") == "done"]
        return chat

    def _active_identity(self, item: dict | None) -> dict | None:
        """补全主对象的名称和类型（限定证券只带代码）。"""
        if not item or not item.get("code"):
            return None
        identity = {key: item[key] for key in ("code", "name", "kind", "market") if item.get(key)}
        if "name" not in identity or "kind" not in identity:
            try:
                from src.services.stock_search import StockSearch
                found = [row for row in StockSearch(self.db_path).search(identity["code"], 5) if row["code"].lower() == identity["code"].lower()]
                if found:
                    identity = {"name": found[0]["name"], "kind": found[0].get("kind", "stock"), **identity}
            except Exception:
                pass
        identity.setdefault("kind", "stock")
        return identity

    def _accept(self, session_id: str, question: str, perspective: str | None, stock_context: dict | None,
                skills: list[str] | None, expected_version: int | None):
        """接受一个问题：校验版本，确定意图与主对象，在一个事务里写入这一轮、主对象和策略选择。"""
        record = self._load(session_id)
        if record is None:
            raise KeyError(session_id)
        version = record["version"]
        if expected_version is not None and expected_version != version:
            raise ChatStateConflict()
        chat = self._restore(record)
        perspective_name = perspective or record["perspective"] or "综合"
        effective_skills = list(skills) if skills is not None else list(record["skills"] or [])
        aggregate = ChatTurn(question=question, perspective=perspective_name, stock_context=stock_context,
                             skills=effective_skills, status="running")
        from src.services.chat_tools import ChatTools
        plan = None
        active = record["active"]
        if isinstance(getattr(chat, "tools", None), ChatTools):
            from src.services.web_intent import resolve
            state = deepcopy(next((t["intent_state"] for t in reversed(record["turns"]) if t.get("intent_state")), {}))
            state["active"] = active
            plan = resolve(question, self.db_path, state, stock_context)
            aggregate.intent_plan, aggregate.intent_state = plan["tasks"], plan["state"]
            if plan["requires_confirmation"]:
                aggregate.answer, aggregate.status = plan["message"], "done"
            candidate = plan["state"].get("active")
        else:
            candidate = stock_context or active
        identity = self._active_identity(candidate)
        if identity and active and identity["code"] == active.get("code"):
            new_active = {**active, **identity}   # 仍是同一对象：保留原确认来源和时间
        elif identity:
            new_active = {**identity, "source": "lock" if stock_context else "message",
                          "confirmed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
        else:
            new_active = None
        values: dict[Any, Any] = {
            ChatSessionRecord.turns_json: json.dumps([*record["turns"], asdict(aggregate)], ensure_ascii=False),
            ChatSessionRecord.state_version: version + 1,
            ChatSessionRecord.active_stock_json: json.dumps(new_active, ensure_ascii=False) if new_active else None,
            ChatSessionRecord.updated_at: datetime.now(),
        }
        if skills is not None:
            values[ChatSessionRecord.skills_json] = json.dumps(effective_skills, ensure_ascii=False)
        if not record["turns"]:
            values[ChatSessionRecord.title] = question.strip()[:TITLE_CHARS] or "新会话"
        if perspective:
            values[ChatSessionRecord.perspective] = perspective
        # 比较版本后写入：准备期间（另一个进程）已接受其他问题时拒绝，不追加旧问题。
        with get_db_session(self.db_path) as session:
            updated = (session.query(ChatSessionRecord)
                       .filter(ChatSessionRecord.id == session_id, func.coalesce(ChatSessionRecord.state_version, 0) == version)
                       .update(values, synchronize_session=False))
        if not updated:
            if self._load(session_id) is None:
                raise KeyError(session_id)
            raise ChatStateConflict()
        state = {"active_stock_context": new_active, "session_state_version": version + 1,
                 "session_state": {"selected_skill_ids": effective_skills if skills is not None else record["skills"]}}
        return chat, aggregate, plan, state

    def _finalize(self, session_id: str, turn: ChatTurn) -> None:
        """把最终回答写回接受时的那一轮（按 message_id）；会话或这一轮已删除时丢弃，不重建。"""
        turn.status = "done"
        with get_db_session(self.db_path) as session:
            r = session.get(ChatSessionRecord, session_id)
            if r is None:
                return
            turns = json.loads(r.turns_json or "[]")
            for index, item in enumerate(turns):
                if item.get("message_id") == turn.message_id:
                    turns[index] = asdict(turn)
                    r.turns_json = json.dumps(turns, ensure_ascii=False)
                    r.updated_at = datetime.now()
                    return

    def _execute(self, chat, aggregate: ChatTurn, plan: dict | None, cancel: threading.Event | None,
                 stock_context: dict | None, stream: bool, progress: Callable[[str], None] | None) -> Iterator[dict[str, Any]]:
        """执行已接受的问题，最后一个事件是 done；结束（含中途关闭）后 aggregate 是要写回的最终一轮。"""
        skills, perspective, question = aggregate.skills, aggregate.perspective, aggregate.question
        if plan is None:
            # 测试或自定义工具集没有意图层：整句交给会话对象回答。
            extra = {"stock_context": stock_context, "skills": skills} if stock_context or skills else {}
            before = len(chat.turns)
            returned = None
            try:
                if stream:
                    yield from chat.ask_stream(question, perspective, cancel=cancel, **extra)
                else:
                    returned = chat.ask(question, perspective, progress=progress, **extra)
            except Exception as e:
                aggregate.error = f"回答失败：{e}"
                raise
            finally:
                result = returned if returned is not None else chat.turns[-1] if len(chat.turns) > before else None
                if result is None:
                    aggregate.error = aggregate.error or "已取消"
                else:
                    data = asdict(result) if is_dataclass(result) else {"answer": getattr(result, "answer", ""), "error": getattr(result, "error", "")}
                    for key, value in data.items():
                        if key in _TURN_FIELDS and key not in ("message_id", "question", "asked_at", "status"):
                            setattr(aggregate, key, value)
            if not stream:
                yield {"type": "done", "turn": asdict(aggregate)}
            return
        if plan["requires_confirmation"]:
            yield {"type": "intent", "tasks": plan['tasks'], "requires_confirmation": True, "candidates": (plan['state'].get('pending') or {}).get('candidates', [])}
            yield {"type": "delta", "text": aggregate.answer}
            yield {"type": "done", "turn": asdict(aggregate)}
            return
        from src.services.execution_budget import ExecutionBudget
        from src.services.web_intent import within_scope
        budget = ExecutionBudget.from_config(chat.config)
        original_config = chat.config
        chat.config = {**chat.config, "_execution_deadline": budget.deadline}
        yield {"type": "intent", "tasks": plan['tasks'], "requires_confirmation": False}
        partial = None
        before = len(chat.turns)
        try:
            for task in plan['tasks']:
                if not within_scope([task], stock_context):
                    aggregate.error = "任务证券超出当前限定范围，请重新确认"
                    break
                targets = task['targets'] or [None]
                for target in targets:
                    if cancel and cancel.is_set():
                        aggregate.error = "已取消"
                        break
                    if not budget.remaining():
                        aggregate.stage_events.append({"type": "stage", "stage_id": aggregate.message_id + ':budget', "name": task['kind'], "status": "budget_skipped", "elapsed_ms": 0})
                        yield aggregate.stage_events[-1]
                        aggregate.error = "问股超时：已达到分析总时长上限"
                        break
                    scope = {"code": target['code']} if target else stock_context
                    title = target.get('name') or target['code'] if target else task['kind']
                    heading = f"\n\n### {title}\n\n" if len(plan['tasks']) > 1 or len(targets) > 1 else ''
                    aggregate.answer += heading
                    if heading:
                        yield {"type": "delta", "text": heading}
                    chat.config = {**chat.config, '_web_intent_kind': task['kind']}
                    partial = chat.ask_stream(task['question'], perspective, cancel=cancel, stock_context=scope, skills=skills)
                    for event in partial:
                        if event['type'] == 'done':
                            child = chat.turns[-1]
                            aggregate.tools.extend(child.tools)
                            aggregate.error = child.error or aggregate.error
                            aggregate.failure_detail = child.failure_detail or aggregate.failure_detail
                            aggregate.context_pack = child.context_pack or aggregate.context_pack
                            aggregate.run_log = child.run_log
                        else:
                            if event['type'] == 'delta':
                                aggregate.answer += event['text']
                            elif event['type'] == 'stage':
                                aggregate.stage_events.append(event)
                            elif event['type'] == 'status' and progress:
                                progress(event['text'])
                            yield event
                    partial = None
                    if aggregate.error:
                        break
                if aggregate.error:
                    break
        finally:
            if partial:
                partial.close()
                aggregate.error = aggregate.error or "已取消"
                if len(chat.turns) > before:
                    aggregate.stage_events = [*aggregate.stage_events, *[event for event in chat.turns[-1].stage_events if event not in aggregate.stage_events]]
            chat.turns[before:] = [aggregate]
            chat.config = original_config
        public = asdict(aggregate)
        public['tools'] = [{key: value for key, value in tool.items() if key != 'result'} for tool in aggregate.tools]
        yield {"type": "done", "turn": public}

    def _run(self, session_id: str, question: str, perspective: str | None, cancel: threading.Event | None,
             stock_context: dict | None, skills: list[str] | None, expected_version: int | None,
             stream: bool, progress: Callable[[str], None] | None) -> Iterator[dict[str, Any]]:
        """同一会话串行：持锁接受并执行，结束（含中途关闭）后写回最终回答。"""
        with self._lock(session_id):
            chat, aggregate, plan, state = self._accept(session_id, question, perspective, stock_context, skills, expected_version)
            if cancel is not None:
                with self._locks_guard:
                    self._cancels[session_id] = cancel
            finished = False
            try:
                yield {"type": "accepted", "session_id": session_id, "message_id": aggregate.message_id, **state}
                yield from self._execute(chat, aggregate, plan, cancel, stock_context, stream, progress)
                finished = True
            finally:
                if not finished and not aggregate.error:
                    aggregate.error = "已取消"   # 接受后连接关闭或出错：保留问题，标记未完成
                if cancel is not None:
                    with self._locks_guard:
                        if self._cancels.get(session_id) is cancel:
                            del self._cancels[session_id]
                self._finalize(session_id, aggregate)

    def ask(self, session_id: str, question: str, perspective: str | None = None,
            progress: Callable[[str], None] | None = None, *, stock_context: dict | None = None,
            skills: list[str] | None = None, expected_version: int | None = None) -> dict[str, Any]:
        """在会话里提问（后台任务和机器人用），返回这一轮问答和接受时的会话状态。

        skills 为 None 时沿用会话已保存的选择；会话不存在抛 KeyError，版本冲突抛 ChatStateConflict。
        """
        state: dict[str, Any] = {}
        turn: dict[str, Any] = {}
        for event in self._run(session_id, question, perspective, None, stock_context, skills, expected_version, False, progress):
            if event["type"] == "accepted":
                state = {key: event[key] for key in ("active_stock_context", "session_state_version", "session_state")}
            elif event["type"] == "done":
                turn = event["turn"]
        return {**turn, **state}

    def ask_stream(self, session_id: str, question: str, perspective: str | None = None,
                   cancel: threading.Event | None = None, *, stock_context: dict | None = None,
                   skills: list[str] | None = None, expected_version: int | None = None) -> Iterator[dict[str, Any]]:
        """流式提问：先发 accepted（已写入问题与会话状态），最后一个事件是 done。

        未知会话直接抛 KeyError；接受前的版本冲突只发一个 error 事件（error_code=session_state_conflict），
        不发 accepted 和 done，也不写入任何内容。
        """
        if self._load(session_id) is None:
            raise KeyError(session_id)
        return self._ask_stream(session_id, question, perspective, cancel or threading.Event(), stock_context, skills, expected_version)

    def _ask_stream(self, session_id, question, perspective, cancel, stock_context, skills, expected_version):
        try:
            yield from self._run(session_id, question, perspective, cancel, stock_context, skills, expected_version, True, None)
        except ChatStateConflict as e:
            yield {"type": "error", "message": str(e), "error_code": CONFLICT_CODE, "session_id": session_id}
        except KeyError:
            yield {"type": "error", "message": "会话不存在", "error_code": "session_not_found", "session_id": session_id}

    def cancel(self, session_id: str) -> bool:
        """取消该会话正在进行的流；没有进行中的流返回 False。"""
        with self._locks_guard:
            event = self._cancels.get(session_id)
        if event is None:
            return False
        event.set()
        return True

    def export_markdown(self, session_id: str) -> str | None:
        record = self._load(session_id)
        return self._restore(record).to_markdown() if record else None
