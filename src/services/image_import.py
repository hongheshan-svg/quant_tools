"""图片识别导入自选股：截图 → 视觉模型 → 股票代码/名称 → 校验后的候选列表。

只做识别与校验，不写库；用户在界面确认后再走自选股的添加接口。
"""

from __future__ import annotations

import json
import re
from typing import Any

from json_repair import repair_json
from loguru import logger

ALLOWED_TYPES = {"image/png", "image/jpeg", "image/webp", "image/gif"}
MAX_BYTES = 5 * 1024 * 1024

SYSTEM_PROMPT = "你是股票截图识别助手，只输出 JSON，不要任何解释。"
PROMPT = (
    "请识别这张图片（可能是券商自选股/持仓截图、行情列表、聊天记录或研报）中出现的全部 A 股股票，"
    "列出每只股票的 6 位代码和/或名称。看不清代码时只给名称，看不清名称时只给代码，不要编造图中没有的股票，"
    "也不要收录指数、基金、港股和美股。\n"
    '只返回 JSON，格式：{"stocks": [{"code": "600519", "name": "贵州茅台"}]}；图中没有股票时返回 {"stocks": []}。'
)


def _parse_json(raw: str) -> dict:
    """容忍 markdown 代码块和前后多余文字。"""
    text = (raw or "").strip()
    candidates = [text]
    for block in re.findall(r"```(?:json)?\s*(.*?)```", text, flags=re.S | re.I):
        candidates.insert(0, block.strip())
    for cand in candidates:
        try:
            data = json.loads(cand)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(data, (dict, list)):
            return data if isinstance(data, dict) else {"stocks": data}
    for cand in candidates:
        try:
            data = repair_json(cand, return_objects=True)
        except Exception:
            continue
        if isinstance(data, dict) and data:
            return data
        if isinstance(data, list) and data:
            return {"stocks": data}
    return {}


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def extract_stocks(image: bytes, mime: str, config: dict, llm=None) -> dict:
    """识别图片里的股票，返回 {"candidates": [{code, name, raw}], "unresolved": [raw]}。"""
    mime = (mime or "").lower().split(";")[0].strip()
    if mime == "image/jpg":
        mime = "image/jpeg"
    if mime not in ALLOWED_TYPES:
        raise ValueError("只支持 PNG、JPEG、WebP、GIF 图片")
    if not image:
        raise ValueError("图片内容为空")
    if len(image) > MAX_BYTES:
        raise ValueError("图片不能超过 5MB")

    config = config or {}
    if llm is None:
        from src.analyzers.llm_client import LLMClient

        llm = LLMClient(config.get("llm", config) if isinstance(config.get("llm", config), dict) else {})
    raw_reply = llm.chat_vision(PROMPT, [(image, mime)], system_message=SYSTEM_PROMPT)
    data = _parse_json(raw_reply)
    items = data.get("stocks") if isinstance(data, dict) else None
    if not isinstance(items, list):
        items = []

    from src.services.watchlist import WatchlistService

    service = WatchlistService(config)
    candidates: list[dict[str, str]] = []
    unresolved: list[str] = []
    seen: set[str] = set()
    for item in items:
        if isinstance(item, dict):
            code, name = _text(item.get("code")), _text(item.get("name"))
        else:
            code, name = "", _text(item)
        raw = " ".join(p for p in (code, name) if p)
        if not raw:
            continue
        resolved = None
        for query in (code, name):
            if query:
                try:
                    resolved = service.resolve(query, include_funds=False)
                except Exception as e:
                    logger.warning(f"图片识别解析股票失败 {query}: {e}")
                    resolved = None
                if resolved:
                    break
        if not resolved:
            if raw not in unresolved:
                unresolved.append(raw)
            continue
        if resolved[0] in seen:
            continue
        seen.add(resolved[0])
        candidates.append({"code": resolved[0], "name": resolved[1], "raw": raw})
    logger.info(f"图片识别股票：候选 {len(candidates)} 只，未识别 {len(unresolved)} 条")
    return {"candidates": candidates, "unresolved": unresolved}
