"""
股票搜索：按代码、名称、拼音首字母查找任意 A 股（参考 daily_stock_analysis 的代码/名称/拼音补全）

股票列表取 stock_info（交易所官方列表）和最新一天的全市场行情，进程内缓存 12 小时。
多音字按所有读音生成首字母组合（如「朝阳」同时匹配 zy 和 cy）。
排序：代码完全匹配 > 代码/名称/首字母前缀 > 名称包含 > 首字母包含。
"""

from __future__ import annotations

import itertools
import re
import threading
from datetime import datetime, timedelta
from typing import Any

from loguru import logger
from sqlalchemy import func

from src.database.db import get_db_session
from src.database.models import StockDaily, StockInfo
from src.utils.stock_code import bare_code

INDEX_TTL_HOURS = 12
MAX_INITIAL_VARIANTS = 8
_NON_ALNUM = re.compile(r"[^0-9a-z]")


def name_initials(name: str) -> tuple[str, ...]:
    """名称的拼音首字母（小写，只保留字母数字）；第一个是按词组判断的常用读音，其后是多音字的其他组合，最多 8 种。"""
    from pypinyin import Style, lazy_pinyin, pinyin

    default = _NON_ALNUM.sub("", "".join(lazy_pinyin(name, style=Style.FIRST_LETTER)).lower())
    letters = pinyin(name, style=Style.FIRST_LETTER, heteronym=True, errors=lambda chars: list(chars))
    options = [sorted({_NON_ALNUM.sub("", c.lower()) for c in choices}) or [""] for choices in letters]
    variants = [default] if default else []
    for combo in itertools.islice(itertools.product(*options), MAX_INITIAL_VARIANTS - len(variants)):
        text = "".join(combo)
        if text and text not in variants:
            variants.append(text)
    return tuple(variants)


class StockSearch:
    _entries: list[tuple[str, str, tuple[str, ...]]] = []
    _built_at: datetime | None = None
    _db_path = ""
    _lock = threading.Lock()

    def __init__(self, db_path: str):
        self.db_path = db_path

    def _ensure_index(self) -> list[tuple[str, str, tuple[str, ...]]]:
        cls = StockSearch
        with cls._lock:
            fresh = cls._built_at and datetime.now() - cls._built_at < timedelta(hours=INDEX_TTL_HOURS)
            if cls._entries and fresh and cls._db_path == self.db_path:
                return cls._entries
        entries = self._build()
        with cls._lock:
            cls._entries, cls._built_at, cls._db_path = entries, datetime.now(), self.db_path
        return entries

    def _build(self) -> list[tuple[str, str, tuple[str, ...]]]:
        names: dict[str, str] = {}
        try:
            with get_db_session(self.db_path) as session:
                latest = session.query(func.max(StockDaily.trade_date)).scalar()
                if latest:
                    for code, name in session.query(StockDaily.code, StockDaily.name).filter(StockDaily.trade_date == latest).all():
                        if name:
                            names[bare_code(code)] = name.strip()
                for code, name in session.query(StockInfo.code, StockInfo.name).all():  # 交易所官方简称优先
                    if name:
                        names[bare_code(code)] = name.strip()
        except Exception as e:
            logger.warning(f"股票搜索索引构建失败: {e}")
        entries = [(code, name, name_initials(name)) for code, name in sorted(names.items())
                   if len(code) == 6 and code.isdigit()]
        logger.debug(f"股票搜索索引：{len(entries)} 只")
        return entries

    def search(self, text: str, limit: int = 20) -> list[dict[str, Any]]:
        query = (text or "").strip()
        if not query:
            return []
        lowered = _NON_ALNUM.sub("", query.lower())
        code_query = bare_code(lowered) if lowered.isdigit() or (lowered[:2] in ("sh", "sz", "bj") and lowered[2:].isdigit()) else ""
        scored = []
        for code, name, initials in self._ensure_index():
            rank = self._rank(query, lowered, code_query, code, name, initials)
            if rank is not None:
                scored.append((rank, code, name))
        scored.sort()
        return [{"code": code, "name": name} for _, code, name in scored[:limit]]

    @staticmethod
    def _rank(query: str, lowered: str, code_query: str, code: str, name: str, initials: tuple[str, ...]) -> int | None:
        if code_query:
            if code == code_query:
                return 0
            if code.startswith(code_query):
                return 1
            return 3 if code_query in code else None
        if name == query:
            return 0
        if name.startswith(query):
            return 1
        if lowered and any(i.startswith(lowered) for i in initials):
            return 2
        if query in name:
            return 3
        if lowered and any(lowered in i for i in initials):
            return 4
        return None

    @classmethod
    def reset(cls) -> None:
        with cls._lock:
            cls._entries, cls._built_at, cls._db_path = [], None, ""
