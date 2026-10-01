"""完整配置校验：未知键、类型、格式与范围、语义（不联网、不抛异常）。

`check_config(config, raw, example)` 返回 {"ok", "errors", "warnings", "issues"}，
设置页、`main.py --check-config`、配置导入和服务启动日志共用。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable

import yaml
from loguru import logger

EXAMPLE_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "settings.yaml.example"

# 子键由程序或用户动态生成，不做未知键和类型检查
DYNAMIC_PATHS = {
    ("strategy", "adaptive_weights"), ("strategy", "source_confidence"), ("screening", "strategies"),
    ("llm", "pricing"), ("notifier", "routes"), ("alerts", "rules"),
}
# 允许写成字符串（逗号/换行分隔）的列表型键
STRING_LIST_KEYS = {"api_key", "api_keys", "base_urls", "to"}
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
ENUMS = {
    ("diagnosis", "mode"): ("single", "standard", "full"),
    ("llm", "backend"): ("litellm", "openai"),
    ("report", "language"): ("zh", "en"),
}
INT_RANGES = {("watchlist", "workers"): (1, 10), ("watchlist", "max_stocks"): (1, 500), ("web", "port"): (1, 65535)}


def _dot(path: tuple) -> str:
    return ".".join(str(p) for p in path)


def _get(data: Any, path: tuple, default: Any = None) -> Any:
    for key in path:
        if not isinstance(data, dict) or key not in data:
            return default
        data = data[key]
    return default if data is None else data


def _is_dynamic(path: tuple, example_value: Any) -> bool:
    if path in DYNAMIC_PATHS:
        return True
    # search 下各 provider 的子键（api_keys、base_urls、timeout…）
    return len(path) == 2 and path[0] == "search" and isinstance(example_value, dict)


def _kind(value: Any) -> str:
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "str"
    if isinstance(value, list):
        return "list"
    if isinstance(value, dict):
        return "dict"
    return "other"


_KIND_LABEL = {"bool": "布尔值（true/false）", "number": "数字", "str": "字符串", "list": "列表", "dict": "字典（键值对）"}


def _extra_keys(path: tuple) -> set[str]:
    """推送渠道的合法字段：example 没列出但 CHANNEL_FIELDS 里有的也不算未知。"""
    if len(path) == 2 and path[0] == "notifier":
        try:
            from src.notifier import CHANNEL_FIELDS

            return {f["key"] for f in CHANNEL_FIELDS.get(path[1], [])}
        except Exception:
            return set()
    return set()


def _walk_raw(raw: Any, example: Any, path: tuple, issues: list[dict]) -> None:
    """递归比较用户原文与 example：未知键 warning，类型不符 error。"""
    if not isinstance(raw, dict) or not isinstance(example, dict):
        return
    allowed_extra = _extra_keys(path)
    for key, value in raw.items():
        sub = (*path, key)
        if key not in example:
            if key not in allowed_extra:
                issues.append({"level": "warning", "path": _dot(sub), "message": "未知配置项，可能拼写错误"})
            continue
        default = example[key]
        if value is None or default is None or default == "":
            continue
        want, got = _kind(default), _kind(value)
        if want == "number" and got == "number" or want == got == "bool":
            continue
        if want == "str" and got == "number":  # 纯数字的 Key、ID 会被 YAML 解析成数字
            continue
        if key in STRING_LIST_KEYS and {want, got} <= {"list", "str"}:
            continue
        if want != got:
            issues.append({"level": "error", "path": _dot(sub),
                           "message": f"类型不符：应为{_KIND_LABEL.get(want, want)}，实际是{_KIND_LABEL.get(got, got)}"})
            continue
        if want == "dict" and not _is_dynamic(sub, default):
            _walk_raw(value, default, sub, issues)


def _err(issues: list[dict], path: str | tuple, message: str, level: str = "error") -> None:
    issues.append({"level": level, "path": path if isinstance(path, str) else _dot(path), "message": message})


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _check_formats(config: dict, raw: dict | None, example: dict, issues: list[dict]) -> None:
    for key, value in (config.get("scheduler") or {}).items():
        if str(key).endswith("_time") and not (isinstance(value, str) and TIME_RE.match(value.strip())):
            _err(issues, ("scheduler", key), f"时间格式应为 HH:MM（如 15:30，小时两位），当前是 {value!r}")
    quiet = _get(config, ("notifier", "quiet_hours"))
    if quiet:
        if not (isinstance(quiet, list) and len(quiet) == 2 and all(isinstance(v, str) and TIME_RE.match(v.strip()) for v in quiet)):
            _err(issues, "notifier.quiet_hours", "免打扰时段应为空或两个 HH:MM，如 [\"22:00\", \"08:00\"]")

    def walk(node: Any, path: tuple) -> None:
        if not isinstance(node, dict) or path in DYNAMIC_PATHS:
            return
        for key, value in node.items():
            sub = (*path, key)
            if isinstance(value, dict):
                walk(value, sub)
            elif (str(key).endswith("_minutes") or str(key).endswith("interval")) and _is_number(value) and value <= 0:
                default = _get(example, sub)
                if _is_number(default) and default == 0:  # 示例默认就是 0 的项（如 watchlist.timeout_minutes）0 表示不限
                    if value < 0:
                        _err(issues, sub, "不能小于 0（0 表示不限）")
                    continue
                _err(issues, sub, "必须大于 0")
    walk(config, ())

    for path, (low, high) in INT_RANGES.items():
        value = _get(config, path)
        if value is None:
            continue
        if not _is_number(value) or not (low <= value <= high):
            _err(issues, path, f"应在 {low}~{high} 之间，当前是 {value!r}")
    for path, choices in ENUMS.items():
        value = _get(config, path)
        if value is not None and str(value).strip().lower() not in choices:
            _err(issues, path, f"只能是 {' / '.join(choices)}，当前是 {value!r}")


def _check_llm(config: dict, raw: dict | None, example: dict, issues: list[dict]) -> None:
    from src.analyzers.llm_client import KEYLESS_PROVIDERS, parse_keys

    llm = config.get("llm") or {}
    primary = llm.get("primary") or {}
    provider = str(primary.get("provider") or "openai").lower()
    if provider not in KEYLESS_PROVIDERS and not parse_keys(primary.get("api_key")):
        _err(issues, "llm.primary.api_key", "主模型未配置 API Key，AI 功能都不可用")
    backup = llm.get("backup") or {}
    if backup.get("provider"):
        bprovider = str(backup["provider"]).lower()
        if bprovider not in KEYLESS_PROVIDERS and not parse_keys(backup.get("api_key")):
            _err(issues, "llm.backup.api_key", "备用模型已配置平台但没有可用 API Key，主模型失败时无法切换", "warning")


def _check_notifier(config: dict, raw: dict | None, example: dict, issues: list[dict]) -> None:
    from src.notifier import diagnose

    result = diagnose(config)
    for ch in result.get("channels", []):
        if ch.get("enabled"):
            for problem in ch.get("issues") or []:
                _err(issues, ("notifier", ch["channel"]), f"{ch.get('label', ch['channel'])}：{problem}")


def _check_search(config: dict, raw: dict | None, example: dict, issues: list[dict]) -> None:
    from src.collectors.news_search import configured_providers

    if (config.get("search") or {}).get("enabled") and not configured_providers(config):
        _err(issues, "search", "已启用联网搜索，但没有任何配置完整的搜索服务（缺 API Key 或地址）", "warning")


def _check_bot(config: dict, raw: dict | None, example: dict, issues: list[dict]) -> None:
    bot = config.get("bot") or {}
    platforms = {"dingtalk": ("client_id", "client_secret"), "feishu": ("app_id", "app_secret"), "discord": ("token",)}
    for name, keys in platforms.items():
        cfg = bot.get(name) or {}
        if not cfg.get("enabled"):
            continue
        missing = [k for k in keys if not cfg.get(k) or str(cfg.get(k)).startswith("your-")]
        if missing:
            _err(issues, ("bot", name), f"机器人已启用但缺少 {' / '.join(missing)}，不会启动", "warning")


def _check_web(config: dict, raw: dict | None, example: dict, issues: list[dict]) -> None:
    web = config.get("web") or {}
    host = str(web.get("host") or "127.0.0.1").strip().lower()
    if host not in LOCAL_HOSTS and not web.get("auth_enabled"):
        _err(issues, "web.host", f"服务监听 {host}（对外可访问），对外监听必须开启登录（web.auth_enabled）")


def _check_trading(config: dict, raw: dict | None, example: dict, issues: list[dict]) -> None:
    if (config.get("trading") or {}).get("auto_confirm"):
        _err(issues, "trading.auto_confirm", "自动确认只对模拟盘生效，订单生成后会直接下单", "warning")


def _check_types(config: dict, raw: dict | None, example: dict, issues: list[dict]) -> None:
    if raw:
        _walk_raw(raw, example, (), issues)


def load_example() -> dict:
    try:
        with open(EXAMPLE_PATH, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


def load_raw(path: str | Path = "config/settings.yaml") -> dict | None:
    """读取用户 settings.yaml 原文解析结果；文件不存在返回 None，格式错误抛出异常。"""
    p = Path(path)
    if not p.exists():
        return None
    with open(p, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


CHECKS: list[tuple[str, Callable]] = [
    ("类型与未知键", _check_types), ("格式与范围", _check_formats), ("主备模型", _check_llm),
    ("推送渠道", _check_notifier), ("联网搜索", _check_search), ("聊天机器人", _check_bot),
    ("Web 服务", _check_web), ("交易", _check_trading),
]


def check_config(config: dict, raw: dict | None = None, example: dict | None = None) -> dict[str, Any]:
    """校验配置。config 为合并后的配置，raw 为 settings.yaml 原文解析结果，example 缺省时读示例配置。"""
    config = config if isinstance(config, dict) else {}
    if example is None:
        example = load_example()
    issues: list[dict] = []
    for name, fn in CHECKS:
        try:
            fn(config, raw if isinstance(raw, dict) else None, example, issues)
        except Exception as e:
            issues.append({"level": "warning", "path": name, "message": f"检查项执行失败（已跳过）：{e}"})
    issues.sort(key=lambda i: (i["level"] != "error", i["path"]))
    errors = sum(1 for i in issues if i["level"] == "error")
    return {"ok": errors == 0, "errors": errors, "warnings": len(issues) - errors, "issues": issues}


def check_current(config: dict, path: str | Path = "config/settings.yaml") -> dict[str, Any]:
    """校验当前配置和磁盘上的 settings.yaml 原文（原文无法解析时按没有原文处理并记一条错误）。"""
    try:
        raw = load_raw(path)
        parse_error = None
    except Exception as e:
        raw, parse_error = None, str(e)
    result = check_config(config, raw)
    if parse_error:
        result["issues"].insert(0, {"level": "error", "path": str(path), "message": f"配置文件无法解析：{parse_error}"})
        result.update(ok=False, errors=result["errors"] + 1)
    return result


def log_startup_issues(config: dict, limit: int = 10) -> dict[str, Any] | None:
    """启动时调用一次：有 error 时逐条 logger.warning（最多 limit 条），不影响启动。"""
    try:
        result = check_current(config)
    except Exception as e:
        logger.debug(f"配置校验失败（忽略）: {e}")
        return None
    errors = [i for i in result["issues"] if i["level"] == "error"]
    if errors:
        logger.warning(f"配置校验发现 {len(errors)} 个错误（可用 python main.py --check-config 查看全部）")
        for i in errors[:limit]:
            logger.warning(f"配置错误 {i['path']}：{i['message']}")
        if len(errors) > limit:
            logger.warning(f"还有 {len(errors) - limit} 条配置错误未显示")
    return result


def format_check(result: dict) -> str:
    """整理成命令行可读文字。"""
    lines = ["配置检查"]
    if not result["issues"]:
        lines.append("  配置检查通过")
    for i in result["issues"]:
        lines.append(f"  [{'错误' if i['level'] == 'error' else '警告'}] {i['path']}：{i['message']}")
    lines.append(f"共 {result['errors']} 个错误，{result['warnings']} 个警告")
    return "\n".join(lines)
