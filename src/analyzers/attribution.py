"""诊断的信号归因：技术面/资讯/基本面/大盘四部分贡献度（合计 100）和最强看多/看空信号。"""

from __future__ import annotations

from typing import Any

FACTOR_KEYS = ("technical", "news", "fundamentals", "market")
SIGNAL_KEYS = ("strongest_bullish", "strongest_bearish")
MAX_SIGNAL_LEN = 60


def _to_number(value: Any) -> float | None:
    """数字或 "40%"/"40" 转数值；负数按 0，大于 100 按 100，无法解析为 None。"""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, str):
        value = value.strip().rstrip("%％").strip()
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    if num != num or num in (float("inf"), float("-inf")):
        return None
    return max(0.0, min(100.0, num))


def _scale_to_100(nums: list[float]) -> list[int]:
    """按比例缩放为合计恰好 100 的整数（最大余数法）。"""
    total = sum(nums)
    exact = [n * 100 / total for n in nums]
    floors = [int(x) for x in exact]
    order = sorted(range(len(nums)), key=lambda i: exact[i] - floors[i], reverse=True)
    for i in order[:100 - sum(floors)]:
        floors[i] += 1
    return floors


def normalize_attribution(raw: Any) -> dict:
    """规范化 LLM 返回的 signal_attribution，无有效内容时返回 {}。"""
    if not isinstance(raw, dict):
        return {}
    nums = [_to_number(raw.get(k)) for k in FACTOR_KEYS]
    signals = {k: str(raw.get(k) or "").strip()[:MAX_SIGNAL_LEN] for k in SIGNAL_KEYS}
    if all(n is None for n in nums) and not any(signals.values()):
        return {}
    if all(n is not None for n in nums) and sum(nums) > 0:
        values: list[int | None] = list(_scale_to_100(nums))
    else:
        values = [None if n is None else int(round(n)) for n in nums]
    return {**dict(zip(FACTOR_KEYS, values)), **signals}
