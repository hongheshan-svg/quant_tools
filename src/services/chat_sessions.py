"""
AI 问股会话的持久化（Web 端使用）：每个会话一行（chat_session 表），问答记录存成 JSON，
追问时按记录恢复 StockChatSession，回答后写回。
"""

from __future__ import annotations

import json
import threading
import uuid
from dataclasses import asdict
from typing import Any, Callable, Iterator
from copy import deepcopy
import time

from src.config_loader import load_config
from src.database.db import get_db_session
from src.database.models import ChatSessionRecord
from src.services.stock_chat import ChatTurn, StockChatSession

TITLE_CHARS = 40


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

    def create(self, perspective: str = "综合") -> dict[str, Any]:
        session_id = uuid.uuid4().hex
        with get_db_session(self.db_path) as session:
            session.add(ChatSessionRecord(id=session_id, title="新会话", perspective=perspective, turns_json="[]"))
        return self.get(session_id)

    def list(self, limit: int = 50) -> list[dict[str, Any]]:
        with get_db_session(self.db_path) as session:
            rows = session.query(ChatSessionRecord).order_by(ChatSessionRecord.updated_at.desc()).limit(limit).all()
            return [{"id": r.id, "title": r.title, "perspective": r.perspective, "turns": len(json.loads(r.turns_json or "[]")),
                     "updated_at": r.updated_at.strftime("%Y-%m-%d %H:%M") if r.updated_at else ""} for r in rows]

    def get(self, session_id: str) -> dict[str, Any] | None:
        with get_db_session(self.db_path) as session:
            r = session.get(ChatSessionRecord, session_id)
            if r is None:
                return None
            return {"id": r.id, "title": r.title, "perspective": r.perspective, "turns": json.loads(r.turns_json or "[]"),
                    "updated_at": r.updated_at.strftime("%Y-%m-%d %H:%M") if r.updated_at else ""}

    def delete(self, session_id: str) -> bool:
        with get_db_session(self.db_path) as session:
            return session.query(ChatSessionRecord).filter(ChatSessionRecord.id == session_id).delete() > 0

    def _restore(self, record: dict[str, Any]) -> StockChatSession:
        chat = self._factory(self.config)
        chat.turns = [ChatTurn(**t) for t in record["turns"]]
        return chat

    def _intent_stream(self, chat, question, perspective, cancel, stock_context, skills):
        """一个用户问题保持一条记录，子任务按顺序各自锁定证券范围。"""
        from src.services.chat_tools import ChatTools
        if not isinstance(getattr(chat, 'tools', None), ChatTools):
            extra = {"stock_context": stock_context, "skills": skills} if stock_context or skills else {}
            yield from chat.ask_stream(question, perspective, cancel=cancel, **extra)
            return
        from src.services.web_intent import resolve, within_scope
        state = deepcopy(chat.turns[-1].intent_state) if chat.turns else {}
        plan = resolve(question, self.db_path, state, stock_context)
        aggregate = ChatTurn(question=question, perspective=perspective, stock_context=stock_context, skills=skills or [], intent_plan=plan['tasks'], intent_state=plan['state'])
        before = len(chat.turns)
        if plan['requires_confirmation']:
            aggregate.answer = plan['message']
            chat.turns.append(aggregate)
            yield {"type": "intent", "tasks": plan['tasks'], "requires_confirmation": True, "candidates": (plan['state'].get('pending') or {}).get('candidates', [])}
            yield {"type": "delta", "text": aggregate.answer}
            yield {"type": "done", "turn": asdict(aggregate)}
            return
        from src.services.execution_budget import ExecutionBudget
        budget = ExecutionBudget.from_config(chat.config)
        original_config = chat.config
        chat.config = {**chat.config, "_execution_deadline": budget.deadline}
        yield {"type": "intent", "tasks": plan['tasks'], "requires_confirmation": False}
        partial = None
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
                    if heading: yield {"type": "delta", "text": heading}
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
                            if event['type'] == 'delta': aggregate.answer += event['text']
                            elif event['type'] == 'stage': aggregate.stage_events.append(event)
                            yield event
                    partial = None
                    if aggregate.error: break
                if aggregate.error: break
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

    def ask(self, session_id: str, question: str, perspective: str | None = None,
            progress: Callable[[str], None] | None = None, *, stock_context: dict | None = None,
            skills: list[str] | None = None) -> dict[str, Any]:
        """在会话里提问（同一会话串行），返回这一轮问答。"""
        with self._lock(session_id):
            record = self.get(session_id)
            if record is None:
                raise KeyError(session_id)
            chat = self._restore(record)
            from src.services.chat_tools import ChatTools
            if isinstance(getattr(chat, 'tools', None), ChatTools):
                for event in self._intent_stream(chat, question, perspective or record['perspective'] or '综合', None, stock_context, skills):
                    if progress and event['type'] == 'status': progress(event['text'])
                turn = chat.turns[-1]
            else:
                extra = {"stock_context": stock_context, "skills": skills} if stock_context or skills else {}
                turn = chat.ask(question, perspective or record["perspective"] or "综合", progress=progress, **extra)
            with get_db_session(self.db_path) as session:
                r = session.get(ChatSessionRecord, session_id)
                r.turns_json = json.dumps([asdict(t) for t in chat.turns], ensure_ascii=False)
                if not record["turns"]:
                    r.title = question.strip()[:TITLE_CHARS] or "新会话"
                if perspective:
                    r.perspective = perspective
            return asdict(turn)

    def ask_stream(self, session_id: str, question: str, perspective: str | None = None,
                   cancel: threading.Event | None = None, *, stock_context: dict | None = None,
                   skills: list[str] | None = None) -> Iterator[dict[str, Any]]:
        """流式提问：整个流期间持有会话锁，结束（含中途关闭）后持久化；未知会话抛 KeyError。"""
        record = self.get(session_id)
        if record is None:
            raise KeyError(session_id)
        return self._ask_stream(session_id, question, perspective, cancel or threading.Event(), stock_context, skills)

    def _ask_stream(self, session_id: str, question: str, perspective: str | None,
                    cancel: threading.Event, stock_context: dict | None, skills: list[str] | None) -> Iterator[dict[str, Any]]:
        with self._lock(session_id):
            record = self.get(session_id)
            if record is None:
                raise KeyError(session_id)
            chat = self._restore(record)
            before = len(chat.turns)
            with self._locks_guard:
                self._cancels[session_id] = cancel
            try:
                yield from self._intent_stream(chat, question, perspective or record["perspective"] or "综合", cancel, stock_context, skills)
            finally:
                with self._locks_guard:
                    if self._cancels.get(session_id) is cancel:
                        del self._cancels[session_id]
                if len(chat.turns) > before:
                    with get_db_session(self.db_path) as session:
                        r = session.get(ChatSessionRecord, session_id)
                        if r is not None:
                            r.turns_json = json.dumps([asdict(t) for t in chat.turns], ensure_ascii=False)
                            if not record["turns"]:
                                r.title = question.strip()[:TITLE_CHARS] or "新会话"
                            if perspective:
                                r.perspective = perspective

    def cancel(self, session_id: str) -> bool:
        """取消该会话正在进行的流；没有进行中的流返回 False。"""
        with self._locks_guard:
            event = self._cancels.get(session_id)
        if event is None:
            return False
        event.set()
        return True

    def export_markdown(self, session_id: str) -> str | None:
        record = self.get(session_id)
        return self._restore(record).to_markdown() if record else None
