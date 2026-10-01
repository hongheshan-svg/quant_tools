"""
推送设置的读写辅助（Web 推送设置接口使用）。
"""

from __future__ import annotations

from pathlib import Path


def parse_quiet_hours(text: str) -> list[str]:
    """「22:00-08:00」→ ["22:00", "08:00"]；为空或格式不对返回 []。"""
    parts = [p.strip() for p in text.replace("～", "-").replace("~", "-").split("-") if p.strip()]
    if len(parts) == 2 and all(len(p) == 5 and p[2] == ":" for p in parts):
        return parts
    return []


def save_notifier_settings(notifier: dict, path: Path | None = None) -> None:
    """把 notifier 段合并写回 settings.yaml，其余配置保持不变。"""
    from src.settings_store import save_section

    save_section("notifier", notifier, path=path, merge=True)
