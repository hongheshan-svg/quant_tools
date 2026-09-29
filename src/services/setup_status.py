"""首次配置向导：检查各项必需/可选配置是否就绪（不联网、不抛异常）。"""

from __future__ import annotations

import glob
import os
import sys
from datetime import datetime, timedelta
from typing import Any


def _safe(fn) -> bool:
    try:
        return bool(fn())
    except Exception:
        return False


def _check_llm(config: dict) -> bool:
    from src.analyzers.llm_client import build_route
    return build_route((config.get("llm") or {}).get("primary") or {}) is not None


def _db_path(config: dict) -> str:
    return (config.get("database") or {}).get("sqlite_path", "data/quant.db")


def _check_data(config: dict) -> bool:
    from src.database.db import get_db_session
    from src.database.models import StockDaily, StockInfo

    since = (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d")
    with get_db_session(_db_path(config)) as session:
        if session.query(StockInfo.id).limit(1000).count() >= 1000:
            return True
        return session.query(StockDaily.id).filter(StockDaily.trade_date >= since).first() is not None


def _check_calendar(config: dict) -> bool:
    from src.database.db import get_db_session
    from src.database.models import TradeCalendar

    today = datetime.now().strftime("%Y-%m-%d")
    with get_db_session(_db_path(config)) as session:
        return session.query(TradeCalendar.id).filter(TradeCalendar.trade_date >= today).first() is not None


def _check_notifier(config: dict) -> bool:
    from src.notifier import enabled_channels
    return bool(enabled_channels(config))


def _check_watchlist(config: dict) -> bool:
    from src.services.watchlist import WatchlistService
    return bool(WatchlistService(config).list())


def _browser_dirs() -> list[str]:
    env = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if env and env != "0":
        return [env]
    home = os.path.expanduser("~")
    if sys.platform == "win32":
        return [os.path.join(os.environ.get("LOCALAPPDATA") or os.path.join(home, "AppData", "Local"), "ms-playwright")]
    if sys.platform == "darwin":
        return [os.path.join(home, "Library", "Caches", "ms-playwright")]
    return [os.path.join(home, ".cache", "ms-playwright")]


def _check_browser(config: dict) -> bool:
    return any(glob.glob(os.path.join(d, "chromium*")) for d in _browser_dirs())


def setup_status(config: dict) -> dict[str, Any]:
    """返回配置完成情况：{items, done, total, required_missing}。"""
    config = config or {}
    specs: list[tuple[str, str, bool, Any, str, str]] = [
        ("llm", "AI 模型", True, _check_llm, "填写主模型的平台、模型名和 API Key（Ollama 无需 Key）。", "/settings?tab=llm"),
        ("data", "行情数据", True, _check_data, "首次运行需要先执行一次数据采集。", "/"),
        ("calendar", "交易日历", False, _check_calendar, "交易日历会在采集或定时任务运行时自动联网更新；未更新前按周一至周五判断。", "/settings?tab=scheduler"),
        ("notifier", "消息推送", False, _check_notifier, "配置企业微信、钉钉、飞书或邮件渠道后，可推送日报和提醒。", "/settings?tab=notifier"),
        ("watchlist", "自选股", False, _check_watchlist, "添加自选股后可逐只 AI 诊断并生成决策仪表盘。", "/watchlist"),
        ("browser", "浏览器组件", False, _check_browser, "运行 playwright install chromium（东方财富、同花顺、社交平台采集依赖它）。", "/setup"),
    ]
    web = config.get("web") or {}
    host = str(web.get("host") or "127.0.0.1").strip().lower()
    items: list[dict[str, Any]] = []
    for key, label, required, fn, hint, link in specs:
        done = _safe(lambda fn=fn: fn(config))
        items.append({"key": key, "label": label, "done": done, "required": required,
                      "hint": "已完成" if done else hint, "link": link})
    if host not in ("127.0.0.1", "localhost"):
        done = bool(web.get("auth_enabled"))
        items.append({"key": "web_auth", "label": "登录安全", "done": done, "required": True,
                      "hint": "已完成" if done else f"服务监听 {host}，对外可访问，请开启 Web 登录并设置密码。",
                      "link": "/settings?tab=security"})
    done_n = sum(1 for i in items if i["done"])
    return {"items": items, "done": done_n, "total": len(items),
            "required_missing": sum(1 for i in items if i["required"] and not i["done"])}
