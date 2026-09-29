"""系统错误推送：定时任务等后台流程出错时，按来源限频推送到 system_error 类消息的渠道。

配置 notifier.system_error：enabled（默认 true）、cooldown_minutes（同一来源的冷却，默认 60 分钟）。
冷却状态只保存在当前进程内。
"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta

from loguru import logger

DEFAULT_COOLDOWN_MINUTES = 60
MAX_ERROR_CHARS = 500
_last_sent: dict[str, datetime] = {}
_lock = threading.Lock()


def report_error(config: dict, source: str, error, cooldown_minutes: int | None = None) -> bool:
    """记录错误日志，并在启用且不在冷却期时推送；返回是否有渠道推送成功。推送本身的异常不会外抛。"""
    logger.error(f"系统错误 [{source}]: {error}")
    cfg = (config.get("notifier", {}) or {}).get("system_error") or {}
    if cfg.get("enabled", True) is False:
        return False
    if cooldown_minutes is None:
        try:
            cooldown_minutes = int(cfg.get("cooldown_minutes", DEFAULT_COOLDOWN_MINUTES))
        except (TypeError, ValueError):
            cooldown_minutes = DEFAULT_COOLDOWN_MINUTES
    now = datetime.now()
    with _lock:
        last = _last_sent.get(source)
        if last is not None and now - last < timedelta(minutes=cooldown_minutes):
            return False
        _last_sent[source] = now
    try:
        from src.notifier import broadcast

        text = str(error).strip()
        if len(text) > MAX_ERROR_CHARS:
            text = text[:MAX_ERROR_CHARS] + "…"
        content = (f"- 时间：{now:%Y-%m-%d %H:%M:%S}\n- 来源：{source}\n- 错误：{text or type(error).__name__}\n\n"
                   "查看 logs/ 下的日志了解详情。")
        results = broadcast(config, f"系统错误：{source}", content, kind="system_error")
        return any(results.values())
    except Exception as e:
        logger.error(f"系统错误推送异常: {e}")
        return False


def reset_state() -> None:
    """清空冷却记录（测试用）。"""
    with _lock:
        _last_sent.clear()
