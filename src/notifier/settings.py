"""
推送设置的读写辅助（不依赖 Qt，桌面端推送设置对话框使用）。
"""

from __future__ import annotations

from pathlib import Path

import yaml

SETTINGS_PATH = Path("config/settings.yaml")


def parse_quiet_hours(text: str) -> list[str]:
    """「22:00-08:00」→ ["22:00", "08:00"]；为空或格式不对返回 []。"""
    parts = [p.strip() for p in text.replace("～", "-").replace("~", "-").split("-") if p.strip()]
    if len(parts) == 2 and all(len(p) == 5 and p[2] == ":" for p in parts):
        return parts
    return []


def save_notifier_settings(notifier: dict, path: Path = SETTINGS_PATH) -> None:
    """把 notifier 段合并写回 settings.yaml，其余配置保持不变。"""
    full = {}
    if path.exists():
        with open(path, encoding="utf-8") as f:
            full = yaml.safe_load(f) or {}
    full["notifier"] = {**(full.get("notifier") or {}), **notifier}
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(full, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
