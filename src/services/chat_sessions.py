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

    def ask(self, session_id: str, question: str, perspective: str | None = None,
            progress: Callable[[str], None] | None = None) -> dict[str, Any]:
        """在会话里提问（同一会话串行），返回这一轮问答。"""
        with self._lock(session_id):
            record = self.get(session_id)
            if record is None:
                raise KeyError(session_id)
            chat = self._restore(record)
            turn = chat.ask(question, perspective or record["perspective"] or "综合", progress=progress)
            with get_db_session(self.db_path) as session:
                r = session.get(ChatSessionRecord, session_id)
                r.turns_json = json.dumps([asdict(t) for t in chat.turns], ensure_ascii=False)
                if not record["turns"]:
                    r.title = question.strip()[:TITLE_CHARS] or "新会话"
                if perspective:
                    r.perspective = perspective
            return asdict(turn)

    def ask_stream(self, session_id: str, question: str, perspective: str | None = None,
                   cancel: threading.Event | None = None) -> Iterator[dict[str, Any]]:
        """流式提问：整个流期间持有会话锁，结束（含中途关闭）后持久化；未知会话抛 KeyError。"""
        record = self.get(session_id)
        if record is None:
            raise KeyError(session_id)
        return self._ask_stream(session_id, question, perspective, cancel or threading.Event())

    def _ask_stream(self, session_id: str, question: str, perspective: str | None,
                    cancel: threading.Event) -> Iterator[dict[str, Any]]:
        with self._lock(session_id):
            record = self.get(session_id)
            if record is None:
                raise KeyError(session_id)
            chat = self._restore(record)
            before = len(chat.turns)
            with self._locks_guard:
                self._cancels[session_id] = cancel
            try:
                yield from chat.ask_stream(question, perspective or record["perspective"] or "综合", cancel=cancel)
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
