"""
settings.yaml 的分段写回（桌面端设置对话框和 Web API 共用）。
写回会丢掉文件里的注释；文件不存在时新建，只含写入的段，其余配置仍由 settings.yaml.example 提供默认值。
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import yaml

SETTINGS_PATH = Path("config/settings.yaml")
_lock = threading.Lock()


def read_settings(path: Path | None = None) -> dict[str, Any]:
    path = path or SETTINGS_PATH
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def save_section(section: str, value: Any, path: Path | None = None, merge: bool = False) -> None:
    """写回一个顶层配置段；merge=True 时与已有的同名段（字典）浅合并。path 默认 SETTINGS_PATH（调用时读取，便于测试替换）。"""
    path = path or SETTINGS_PATH
    with _lock:
        full = read_settings(path)
        if merge and isinstance(full.get(section), dict) and isinstance(value, dict):
            value = {**full[section], **value}
        full[section] = value
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            yaml.dump(full, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
