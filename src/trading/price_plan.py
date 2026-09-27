"""
价格计划校验（参考 daily_stock_analysis 的决策信号价格计划）
AI 给出的买入价、止损价、目标价只有与最新价和涨跌停限制相符时才保留；
不合理的价格直接丢弃，由风控配置的默认止损/止盈比例兜底。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

MIN_STOP_RATIO = 0.8     # 止损价不低于买入价的 80%
MAX_TARGET_RATIO = 1.6   # 目标价不高于买入价的 160%
_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")


@dataclass
class PricePlan:
    entry_price: float | None = None
    stop_loss: float | None = None
    target_price: float | None = None


def daily_limit_pct(code: str, name: str = "") -> float:
    """A股涨跌幅限制：ST 5%，创业板/科创板 20%，北交所 30%，其余 10%。"""
    bare = (code or "").strip().lower()[-6:]
    if "ST" in (name or "").upper():
        return 0.05
    if bare.startswith(("30", "68")):
        return 0.20
    if bare.startswith(("8", "4", "92")):
        return 0.30
    return 0.10


def to_price(value: Any) -> float | None:
    """把 AI 输出的价格（数字、"12.5"、"12.5元"）转为正数，无法识别返回 None。"""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        price = float(value)
    else:
        match = _NUMBER.search(str(value))
        if not match:
            return None
        price = float(match.group())
    return round(price, 3) if price > 0 else None


def sanitize_price_plan(code: str, name: str, ref_price: float | None, entry: Any, stop: Any, target: Any) -> PricePlan:
    """以最新价 ref_price 为基准校验价格计划，只保留合理的价格。"""
    if not ref_price or ref_price <= 0:
        return PricePlan()
    limit = daily_limit_pct(code, name)
    plan = PricePlan()

    entry_price = to_price(entry)
    if entry_price and ref_price * (1 - limit) <= entry_price <= ref_price * (1 + limit):
        plan.entry_price = entry_price

    base = plan.entry_price or ref_price
    stop_price = to_price(stop)
    if stop_price and base * MIN_STOP_RATIO <= stop_price < base:
        plan.stop_loss = stop_price
    target_price = to_price(target)
    if target_price and base < target_price <= base * MAX_TARGET_RATIO:
        plan.target_price = target_price
    return plan
