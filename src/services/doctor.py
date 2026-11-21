"""离线环境诊断（`main.py --doctor`、`GET /api/v1/system/doctor`），对齐 DSA dc99c26e。

汇总运行环境、配置校验、配置向导各项、存储可写性、大模型和推送是否就绪，生成可以直接
贴到 issue 里的报告：只读、不联网、不抛异常；配置里的密钥原文、Webhook、本机用户目录
都会被替换掉，大模型只报告平台、模型名和有没有 Key。
"""

from __future__ import annotations

import os
import platform
import sys
from importlib import metadata
from pathlib import Path
from typing import Any

from src.utils.redaction import MASK, redact_text

PACKAGES = ("fastapi", "uvicorn", "sqlalchemy", "pandas", "numpy", "litellm", "openai", "akshare", "playwright",
            "httpx", "apscheduler", "yfinance")
SECRET_WORDS = ("key", "token", "secret", "password", "passwd", "webhook", "authorization", "cookie")
STATUS_LABELS = {"ok": "正常", "warn": "有警告", "error": "有错误"}


def _secret_literals(value: Any, parent: str = "", out: set[str] | None = None) -> set[str]:
    """收集配置里密钥类字段的原文（含列表形式的多个 Key），用于在报告里逐字替换。"""
    out = set() if out is None else out
    if isinstance(value, dict):
        for key, item in value.items():
            _secret_literals(item, str(key).lower(), out)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _secret_literals(item, parent, out)
    elif isinstance(value, str) and any(word in parent for word in SECRET_WORDS):
        for part in value.replace("\n", ",").split(","):
            part = part.strip()
            if len(part) >= 6 and "your-" not in part and part != "******":
                out.add(part)
    return out


def _sanitize(value: Any, secrets: set[str]) -> Any:
    if isinstance(value, dict):
        return {k: _sanitize(v, secrets) for k, v in value.items()}
    if isinstance(value, list):
        return [_sanitize(v, secrets) for v in value]
    if isinstance(value, str):
        for secret in sorted(secrets, key=len, reverse=True):
            value = value.replace(secret, MASK)
        return redact_text(value)
    return value


def _writable(path: Path) -> bool:
    """目录可写，或者还不存在但最近的已存在上级目录可写（启动时会自动创建）。"""
    try:
        for node in (path, *path.parents):
            if node.exists():
                return node.is_dir() and os.access(node, os.W_OK | os.X_OK)
    except OSError:
        return False
    return False


def _version(name: str) -> str:
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return "未安装"
    except Exception:
        return "未知"


def _environment() -> dict[str, Any]:
    from src import __version__

    return {"app_version": __version__, "python": platform.python_version(),
            "platform": f"{platform.system()} {platform.release()} {platform.machine()}",
            "packaged": bool(getattr(sys, "frozen", False)),
            "packages": {name: _version(name) for name in PACKAGES}}


def _llm(config: dict) -> dict[str, Any]:
    from src.analyzers.llm_client import KEYLESS_PROVIDERS

    roles = {}
    llm = config.get("llm") or {}
    for role in ("primary", "backup", "vision"):
        cfg = llm.get(role) or {}
        provider = str(cfg.get("provider") or "").strip()
        if not provider:
            continue
        keys = cfg.get("api_key")
        keys = keys if isinstance(keys, list) else str(keys or "").replace("\n", ",").split(",")
        usable = [k for k in keys if str(k).strip() and "your-" not in str(k)]
        roles[role] = {"provider": provider, "model": str(cfg.get("model") or ""), "keys": len(usable),
                       "ready": bool(usable) or provider in KEYLESS_PROVIDERS}
    ready = (roles.get("primary") or {}).get("ready") or (roles.get("backup") or {}).get("ready") or False
    return {"status": "ok" if ready else "error", "backend": llm.get("backend", "litellm"), "roles": roles, "ready": bool(ready)}


def _storage(config: dict) -> dict[str, Any]:
    db_path = Path(str((config.get("database") or {}).get("sqlite_path") or "data/quant.db"))
    log_dir = Path(str((config.get("logging") or {}).get("log_dir") or "logs"))
    cache_path = Path(str((config.get("llm") or {}).get("cache_path") or "data/llm_cache.sqlite3"))
    result = {"database_dir_writable": _writable(db_path.parent), "log_dir_writable": _writable(log_dir),
              "llm_cache_dir_writable": _writable(cache_path.parent), "database_exists": db_path.exists()}
    try:
        result["database_mb"] = round(db_path.stat().st_size / 1024 / 1024, 1) if db_path.exists() else 0
    except OSError:
        result["database_mb"] = None
    ok = result["database_dir_writable"] and result["log_dir_writable"]
    return {"status": "ok" if ok else "error", **result}


def _section(name: str, fn, *args) -> dict[str, Any]:
    """单项检查出错时记为 error，不影响其他项。"""
    try:
        return fn(*args)
    except Exception as e:  # noqa: BLE001 - 诊断本身不能因为某一项失败而中断
        return {"status": "error", "error": f"{name} 检查失败：{type(e).__name__}: {e}"}


def _config_section(config: dict, config_path: str | Path) -> dict[str, Any]:
    from src.services.config_check import check_current

    result = check_current(config, config_path)
    issues = [{"level": i.get("level"), "path": i.get("path"), "message": i.get("message")} for i in result.get("issues", [])]
    status = "error" if result.get("errors") else "warn" if result.get("warnings") else "ok"
    return {"status": status, "errors": result.get("errors", 0), "warnings": result.get("warnings", 0), "issues": issues}


def _setup_section(config: dict) -> dict[str, Any]:
    from src.services.setup_status import setup_status

    result = setup_status(config)
    items = [{"key": i["key"], "label": i["label"], "done": i["done"], "required": i["required"]} for i in result["items"]]
    missing_required = [i["key"] for i in items if i["required"] and not i["done"]]
    missing_optional = [i["key"] for i in items if not i["required"] and not i["done"]]
    status = "error" if missing_required else "warn" if missing_optional else "ok"
    return {"status": status, "items": items, "missing_required": missing_required, "missing_optional": missing_optional}


def _notifier_section(config: dict) -> dict[str, Any]:
    from src.notifier import CHANNEL_LABELS, diagnose

    result = diagnose(config)
    channels = result.get("channels") or []
    enabled = [c for c in channels if c.get("enabled")]
    broken = [{"channel": c.get("label") or c.get("channel"), "issues": c.get("issues") or []} for c in enabled if not c.get("configured")]
    configured = [CHANNEL_LABELS.get(c.get("channel"), c.get("channel")) for c in enabled if c.get("configured")]
    status = "error" if broken else "ok" if configured else "warn"
    return {"status": status, "configured_channels": configured, "incomplete": broken,
            "route_issues": result.get("routes") or []}


def run_doctor(config: dict, config_path: str | Path = "config/settings.yaml") -> dict[str, Any]:
    """返回 {status, summary, environment, checks}；status 为 ok / warn / error。"""
    config = config or {}
    checks = {
        "config": _section("配置校验", _config_section, config, config_path),
        "setup": _section("配置向导", _setup_section, config),
        "llm": _section("大模型", _llm, config),
        "notifier": _section("消息推送", _notifier_section, config),
        "storage": _section("存储", _storage, config),
    }
    statuses = [c.get("status") for c in checks.values()]
    errors = sum(1 for s in statuses if s == "error")
    warnings = sum(1 for s in statuses if s == "warn")
    report = {"status": "error" if errors else "warn" if warnings else "ok",
              "summary": {"errors": errors, "warnings": warnings},
              "environment": _section("运行环境", _environment), "checks": checks}
    return _sanitize(report, _secret_literals(config))


def format_doctor(report: dict[str, Any]) -> str:
    """命令行与「复制诊断信息」用的纯文本报告。"""
    env, checks = report.get("environment") or {}, report.get("checks") or {}
    summary = report.get("summary") or {}
    status = report.get("status", "error")
    lines = [f"环境诊断：{STATUS_LABELS.get(status, status)}（{summary.get('errors', 0)} 项检查有错误，{summary.get('warnings', 0)} 项有警告）",
             "-" * 60,
             f"[环境] 版本 {env.get('app_version')}，Python {env.get('python')}，{env.get('platform')}"
             + ("，打包版" if env.get("packaged") else "")]
    packages = env.get("packages") or {}
    if packages:
        lines.append("  依赖：" + "，".join(f"{k} {v}" for k, v in packages.items()))
    labels = {"config": "配置", "setup": "配置向导", "llm": "大模型", "notifier": "推送", "storage": "存储"}
    for key, label in labels.items():
        check = checks.get(key) or {}
        state = STATUS_LABELS.get(check.get("status"), check.get("status"))
        if check.get("error"):
            lines.append(f"[{label}] {state}：{check['error']}")
            continue
        if key == "config":
            lines.append(f"[{label}] {state}（错误 {check.get('errors', 0)}，警告 {check.get('warnings', 0)}）")
            for issue in check.get("issues", [])[:20]:
                tag = "错误" if issue.get("level") == "error" else "警告"
                lines.append(f"  {tag} {issue.get('path')}：{issue.get('message')}")
        elif key == "setup":
            done = sum(1 for i in check.get("items", []) if i.get("done"))
            lines.append(f"[{label}] {state}（{done}/{len(check.get('items', []))} 项完成）")
            for item in check.get("items", []):
                if not item.get("done"):
                    lines.append(f"  {'必需' if item.get('required') else '可选'}未完成：{item.get('label')}")
        elif key == "llm":
            roles = check.get("roles") or {}
            text = "；".join(f"{role} {r.get('provider')}/{r.get('model') or '-'} Key {r.get('keys')} 个" for role, r in roles.items()) or "未配置"
            lines.append(f"[{label}] {state}（{check.get('backend')}）：{text}")
        elif key == "notifier":
            lines.append(f"[{label}] {state}：{'、'.join(check.get('configured_channels') or []) or '没有可用渠道'}")
            for item in check.get("incomplete", []):
                lines.append(f"  渠道 {item.get('channel')} 不完整：{'；'.join(item.get('issues') or [])}")
            for issue in check.get("route_issues", []):
                lines.append(f"  路由：{issue}")
        elif key == "storage":
            lines.append(f"[{label}] {state}：数据库目录可写 {check.get('database_dir_writable')}，"
                         f"日志目录可写 {check.get('log_dir_writable')}，数据库 {check.get('database_mb')} MB")
    return "\n".join(lines)
