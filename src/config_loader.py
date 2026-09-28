"""
配置加载器 - 读取 YAML 配置文件

优先级：环境变量（QUANT__ 开头）> config/settings.yaml > config/settings.yaml.example。
环境变量用于 Docker、GitHub Actions 等不方便放配置文件的场景，写回文件时会剔除，不会把 Secrets 写进 settings.yaml。
"""

import copy
import os
from pathlib import Path
from typing import Any

import yaml
from loguru import logger

_config_cache: dict = {}

ENV_PREFIX = "QUANT__"
_MISSING = object()


def _get_path(data: Any, path: list[str] | tuple[str, ...], default: Any = None) -> Any:
    for key in path:
        if not isinstance(data, dict) or key not in data:
            return default
        data = data[key]
    return data


def env_overrides(environ: dict | None = None, base: dict | None = None) -> dict:
    """从环境变量读取配置覆盖：QUANT__LLM__PRIMARY__API_KEY=sk-xxx → llm.primary.api_key。

    层级用双下划线分隔，键名转小写。原配置是字符串的项取原文（避免纯数字的 Key 被当成数字），
    其余按 YAML 解析（true、30、[600519, 000001]）。空值忽略（GitHub Actions 里没配置的 Secret 是空字符串）。
    """
    environ = os.environ if environ is None else environ
    result: dict = {}
    for key, raw in sorted(environ.items()):
        if not key.startswith(ENV_PREFIX) or raw == "":
            continue
        path = [part.lower() for part in key[len(ENV_PREFIX):].split("__") if part]
        if not path:
            continue
        if isinstance(_get_path(base or {}, path), str):
            value: Any = raw
        else:
            try:
                value = yaml.safe_load(raw)
            except yaml.YAMLError:
                value = raw
        node = result
        for part in path[:-1]:
            if not isinstance(node.get(part), dict):
                node[part] = {}
            node = node[part]
        node[path[-1]] = value
    return result


def _leaf_paths(data: dict, prefix: tuple[str, ...] = ()) -> list[tuple[str, ...]]:
    paths = []
    for key, value in data.items():
        if isinstance(value, dict) and value:
            paths.extend(_leaf_paths(value, (*prefix, key)))
        else:
            paths.append((*prefix, key))
    return paths


def strip_env_overrides(config: dict, file_config: dict, environ: dict | None = None) -> dict:
    """写回文件前去掉来自环境变量的值：文件里原来有的恢复原值，没有的删掉。"""
    result = copy.deepcopy(config)
    for path in _leaf_paths(env_overrides(environ)):
        parent = _get_path(result, path[:-1])
        if not isinstance(parent, dict):
            continue
        original = _get_path(file_config, path, _MISSING)
        if original is _MISSING:
            parent.pop(path[-1], None)
        else:
            parent[path[-1]] = copy.deepcopy(original)
    return result


def _read_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


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
            config = _apply_env(config)
            _config_cache[config_path] = config
            return config

        logger.warning(f"配置文件不存在: {config_path}")
        return _apply_env({})

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

    config = _apply_env(config)
    _config_cache[config_path] = config
    logger.info(f"配置已加载: {config_path}")
    return config


def _apply_env(config: dict) -> dict:
    overrides = env_overrides(base=config)
    if not overrides:
        return config
    logger.info(f"环境变量覆盖配置: {', '.join('.'.join(p) for p in _leaf_paths(overrides))}")
    return _deep_merge_dict(config, overrides)


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
    """将配置写回 YAML 文件并刷新缓存（来自环境变量的值不写入文件）。"""
    global _config_cache
    path = Path(config_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    to_write = strip_env_overrides(config, _read_yaml(path))
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(to_write, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
    _config_cache[config_path] = config
    logger.info(f"配置已保存: {config_path}")
