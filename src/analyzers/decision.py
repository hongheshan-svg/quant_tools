"""
操作建议归一化（参考 daily_stock_analysis 的八态 action taxonomy 与 canonical 评分口径）

把 AI 输出的自由文本建议（"强烈买入"、"观望，回踩再低吸"、"不建议买入"……）归一为
buy / add / hold / watch / reduce / sell / avoid / alert；无法识别时返回 ""，调用方应按保守处理。
"""

from __future__ import annotations

import re

ACTION_LABELS = {
    "buy": "买入", "add": "加仓", "hold": "持有", "watch": "观望",
    "reduce": "减仓", "sell": "卖出", "avoid": "回避", "alert": "风险预警",
}
BULLISH_ACTIONS = frozenset({"buy", "add"})

# 否定说法优先识别为回避，避免 "不建议买入" 里的 "买入" 被当成买入
_NEGATED_BUY = re.compile(r"(不建议|不宜|不要|不可|避免|暂不|不适合|切勿|禁止|别)\s*(买入|追高|追涨|介入|参与|建仓|打板|抄底)|do\s*not\s*buy|don't\s*buy", re.I)

# 同一段文字出现多个关键词时，取最先出现的那个（"观望，回踩再买入" → 观望）
_KEYWORDS: list[tuple[str, str]] = [
    ("强烈卖出", "sell"), ("strong_sell", "sell"), ("清仓", "sell"), ("卖出", "sell"), ("止损", "sell"), ("sell", "sell"),
    ("减仓", "reduce"), ("减持", "reduce"), ("reduce", "reduce"), ("trim", "reduce"),
    ("回避", "avoid"), ("规避", "avoid"), ("avoid", "avoid"),
    ("风险预警", "alert"), ("警惕", "alert"), ("alert", "alert"),
    ("加仓", "add"), ("增持", "add"), ("accumulate", "add"),
    ("强烈买入", "buy"), ("strong_buy", "buy"), ("买入", "buy"), ("建仓", "buy"), ("布局", "buy"), ("低吸", "buy"),
    ("打板", "buy"), ("推荐", "buy"), ("buy", "buy"),
    ("持有", "hold"), ("hold", "hold"),
    ("观望", "watch"), ("等待", "watch"), ("关注", "watch"), ("wait", "watch"), ("watch", "watch"),
]


def normalize_action(text: str | None) -> str:
    """把操作建议文本归一为八态 action，无法识别返回 ""。"""
    value = (text or "").strip()
    if not value:
        return ""
    if value.lower() in ACTION_LABELS:
        return value.lower()
    if _NEGATED_BUY.search(value):
        return "avoid"
    lower = value.lower()
    hits = [(lower.find(k.lower()), action) for k, action in _KEYWORDS if k.lower() in lower]
    return min(hits)[1] if hits else ""


def is_bullish(text: str | None) -> bool:
    return normalize_action(text) in BULLISH_ACTIONS


def score_to_action(score: float) -> str:
    """canonical 评分口径：80+ 强烈买入、60-79 买入、40-59 观望、20-39 减仓、<20 卖出。"""
    if score >= 60:
        return "buy"
    if score >= 40:
        return "watch"
    if score >= 20:
        return "reduce"
    return "sell"
