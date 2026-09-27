"""
推送降噪（参考 daily_stock_analysis 的 notification_noise）
- 冷却：同一个告警键在冷却期内只推送一次
- 免打扰：quiet_hours 时段内只推送 critical 级别
状态只保存在当前进程内（和参考实现一致），重启后重新计算。
"""

from __future__ import annotations

import threading
from datetime import datetime, time, timedelta

SEVERITY_RANK = {"info": 0, "warning": 1, "critical": 2}


def _parse_hhmm(text: str) -> time:
    hour, minute = str(text).split(":")
    return time(int(hour), int(minute))


def in_quiet_hours(now: datetime, quiet_hours: list[str] | None) -> bool:
    """quiet_hours 如 ["22:00", "08:00"]，支持跨午夜；为空表示不启用。"""
    if not quiet_hours or len(quiet_hours) != 2:
        return False
    start, end = _parse_hhmm(quiet_hours[0]), _parse_hhmm(quiet_hours[1])
    current = now.time()
    return start <= current < end if start <= end else current >= start or current < end


class NoiseFilter:
    """进程内的推送节流器。"""

    def __init__(self, cooldown_minutes: float = 30, quiet_hours: list[str] | None = None):
        self.cooldown = timedelta(minutes=cooldown_minutes)
        self.quiet_hours = quiet_hours or []
        self._last_sent: dict[str, datetime] = {}
        self._lock = threading.Lock()

    def check(self, key: str, severity: str = "info", now: datetime | None = None, cooldown: timedelta | None = None) -> str:
        """返回 "" 表示推送；"冷却中" 表示重复告警，应丢弃；"免打扰时段" 表示新告警但不推送。
        除「冷却中」外都会记录本次时间，冷却期从此刻开始计算。"""
        now = now or datetime.now()
        with self._lock:
            last = self._last_sent.get(key)
            if last is not None and now - last < (cooldown or self.cooldown):
                return "冷却中"
            self._last_sent[key] = now
        if SEVERITY_RANK.get(severity, 0) < SEVERITY_RANK["critical"] and in_quiet_hours(now, self.quiet_hours):
            return "免打扰时段"
        return ""

    def reset(self) -> None:
        with self._lock:
            self._last_sent.clear()
