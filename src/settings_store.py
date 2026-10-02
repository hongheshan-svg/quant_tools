"""
settings.yaml 的分段写回（Web 设置接口使用）。
写回会丢掉文件里的注释；文件不存在时新建，只含写入的段，其余配置仍由 settings.yaml.example 提供默认值。
来自环境变量（QUANT__ 开头）的值不会写进文件。
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import yaml

from src.config_loader import strip_env_overrides

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
        full = strip_env_overrides({**full, section: value}, full)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            yaml.dump(full, f, allow_unicode=True, default_flow_style=False, sort_keys=False)


# ---------- 导出与导入（配置备份恢复） ----------

MASK = "******"
SECRET_KEYS = {"api_key", "api_keys", "api_token", "token", "bot_token", "secret", "app_secret", "client_secret",
               "password", "sendkey", "device_key", "user_key", "webhook_url", "tushare_token", "tickflow_api_key"}
SECRET_PATHS = {("notifier", "webhook", "url")}


def _is_secret(path: tuple) -> bool:
    return bool(path) and (path[-1] in SECRET_KEYS or tuple(path) in SECRET_PATHS)


def _mask_tree(value: Any, path: tuple = ()) -> Any:
    if isinstance(value, dict):
        return {k: _mask_tree(v, path + (k,)) for k, v in value.items()}
    if _is_secret(path):
        if isinstance(value, list):
            return [MASK if item not in (None, "") else item for item in value]
        if value not in (None, ""):
            return MASK
    if isinstance(value, list):
        return [_mask_tree(v, path) for v in value]
    return value


def export_settings(include_secrets: bool = False, path: Path | None = None) -> str:
    """导出 settings.yaml 原文件内容（不与示例合并）；默认把密钥替换成 ******。"""
    path = path or SETTINGS_PATH
    data = read_settings(path)
    header = ""
    if not path.exists():
        header = "# 当前没有 settings.yaml，所有配置使用默认值\n"
    if not include_secrets:
        data = _mask_tree(data)
    return header + yaml.dump(data, allow_unicode=True, default_flow_style=False, sort_keys=False)


def _restore_masked(new: Any, old: Any, path: tuple, stats: dict) -> Any:
    """把 ****** 从当前配置同一路径还原；没有对应值的键删除并记警告。返回 _DROP 表示丢弃。"""
    if isinstance(new, dict):
        result = {}
        old_d = old if isinstance(old, dict) else {}
        for k, v in new.items():
            r = _restore_masked(v, old_d.get(k), path + (k,), stats)
            if r is not _DROP:
                result[k] = r
        return result
    if isinstance(new, list):
        if MASK not in new:
            return new
        if isinstance(old, list) and len(old) == len(new):
            stats["restored"] += sum(1 for n in new if n == MASK)
            return [old[i] if n == MASK else n for i, n in enumerate(new)]
        stats["warnings"].append(f"{'.'.join(map(str, path))} 的掩码项无法还原，已丢弃")
        return [n for n in new if n != MASK]
    if new == MASK:
        if old not in (None, "", MASK):
            stats["restored"] += 1
            return old
        stats["warnings"].append(f"{'.'.join(map(str, path))} 是掩码且当前没有对应值，已删除")
        return _DROP
    return new


_DROP = object()


def import_settings(text: str, path: Path | None = None) -> dict[str, Any]:
    """导入 YAML 配置整体覆盖 settings.yaml；掩码值从当前文件还原。返回 sections、restored、warnings。"""
    path = path or SETTINGS_PATH
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise ValueError(f"YAML 格式错误：{e}") from e
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ValueError("配置的顶层必须是字典（键值对）")
    stats: dict[str, Any] = {"restored": 0, "warnings": []}
    with _lock:
        current = read_settings(path)
        restored = _restore_masked(data, current, (), stats)
        example_path = Path(f"{path}.example")
        if not example_path.exists():
            example_path = Path(__file__).resolve().parent.parent / "config" / "settings.yaml.example"
        known = set(read_settings(example_path))
        if known:
            for key in restored:
                if key not in known:
                    stats["warnings"].append(f"未知的顶层配置段：{key}（已保留）")
        restored = strip_env_overrides(restored, current)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            yaml.dump(restored, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
        tmp.replace(path)
    return {"sections": list(restored), "restored": stats["restored"], "warnings": stats["warnings"]}
