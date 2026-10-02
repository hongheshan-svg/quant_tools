"""公开错误、运行记录与工具诊断统一脱敏；先脱敏，再截断。"""

from __future__ import annotations

import re
from typing import Any

MASK = "[REDACTED]"
_SECRET_KEY = r"(?:tickflow_api_key|tushare_token|api[_-]?key|access[_-]?token|refresh[_-]?token|bot[_-]?token|token|password|passwd|secret|authorization|webhook(?:_url)?)"
_PAIR = re.compile(rf"(?i)([\"']?{_SECRET_KEY}[\"']?\s*[:=]\s*)(\"[^\"]*\"|'[^']*'|[^\s,;}}&]+)")
_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
_KEY = re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b")
_URL_AUTH = re.compile(r"(https?://)[^\s/@:]+:[^\s/@]+@", re.I)
_WEBHOOK = re.compile(r"https?://[^\s\"'<>]*(?:/hooks/|/hook/|/bot/|/services/)[^\s\"'<>]+", re.I)
_LOCAL_PATH = re.compile(r"(?:/Users/|/home/|[A-Za-z]:\\Users\\)[^\s\"'<>]+")
_SECRET_FIELD = re.compile(rf"^{_SECRET_KEY}$", re.I)


def redact_text(value: Any, limit: int | None = None) -> str:
    text = str(value or "")
    # 浏览器请求异常会携带整行 Cookie，必须连同分号后的字段一起隐藏。
    text = re.sub(r"(?im)(\b(?:set-cookie|cookie)\s*:\s*)[^\r\n]+", lambda m: m[1] + MASK, text)
    text = _BEARER.sub("Bearer " + MASK, text)
    text = _PAIR.sub(lambda m: m[1] + MASK, text)
    text = _KEY.sub(MASK, text)
    text = _URL_AUTH.sub(lambda m: m[1] + MASK + "@", text)
    text = _WEBHOOK.sub(MASK, text)
    text = _LOCAL_PATH.sub("[LOCAL_PATH]", text)
    return text if limit is None else text[:limit]


def redact(value: Any) -> Any:
    """保留 JSON 结构与数值，递归处理诊断中的敏感字段与文本。"""
    if isinstance(value, dict):
        return {k: MASK if _SECRET_FIELD.fullmatch(str(k)) else redact(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    return redact_text(value) if isinstance(value, str) else value
