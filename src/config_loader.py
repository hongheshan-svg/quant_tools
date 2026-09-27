"""
配置加载器 - 读取 YAML 配置文件
"""

from pathlib import Path
from typing import Any

import yaml
from loguru import logger

_config_cache: dict = {}


def _deep_merge_dict(base: dict, override: dict) -> dict:
    """递归合并 dict（override 覆盖 base）。"""
    merged = dict(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(merged.get(k), dict):
            merged[k] = _deep_merge_dict(merged[k], v)
        else:
            merged[k] = v
    return merged


def load_config(config_path: str = "config/settings.yaml") -> dict:
    """加载配置文件（带缓存）"""
    global _config_cache
    if config_path in _config_cache:
        return _config_cache[config_path]

    path = Path(config_path)
    if not path.exists():
        # 回退策略：优先加载同名 .example 配置，提升首启可用性
        example_path = Path(f"{config_path}.example")
        if example_path.exists():
            logger.warning(f"配置文件不存在: {config_path}，已回退到示例配置 {example_path}")
            with open(example_path, encoding="utf-8") as f:
                config = yaml.safe_load(f) or {}
            _config_cache[config_path] = config
            return config

        logger.warning(f"配置文件不存在: {config_path}")
        return {}

    with open(path, encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}

    # 如果存在示例配置，按“示例默认值 + 本地覆盖”合并，防止本地只保存了局部配置
    example_path = Path(f"{config_path}.example")
    if example_path.exists():
        try:
            with open(example_path, encoding="utf-8") as f:
                defaults = yaml.safe_load(f) or {}
            config = _deep_merge_dict(defaults, config)
        except Exception as e:
            logger.warning(f"示例配置合并失败（忽略）: {e}")

    _config_cache[config_path] = config
    logger.info(f"配置已加载: {config_path}")
    return config


def get_config(key: str, default: Any = None, config_path: str = "config/settings.yaml") -> Any:
    """获取配置项（支持点号分隔的路径）

    示例: get_config("llm.primary.model") -> "deepseek-chat"
    """
    config = load_config(config_path)
    keys = key.split(".")
    value = config
    for k in keys:
        if isinstance(value, dict):
            value = value.get(k)
        else:
            return default
        if value is None:
            return default
    return value


def reload_config(config_path: str = "config/settings.yaml") -> dict:
    """强制重新加载配置"""
    global _config_cache
    _config_cache.pop(config_path, None)
    return load_config(config_path)


def save_config(config: dict, config_path: str = "config/settings.yaml") -> None:
    """将配置写回 YAML 文件并刷新缓存。"""
    global _config_cache
    path = Path(config_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(config, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
    _config_cache[config_path] = config
    logger.info(f"配置已保存: {config_path}")
