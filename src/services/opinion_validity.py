"""模型观点的有效性边界；无效数据只能作为诊断信息，不能投票。"""

import math
from typing import Any

STANCES = ("看多", "中性", "看空")
CONFIDENCES = ("高", "中", "低")


def valid_score(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        score = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return score if math.isfinite(score) and 0 <= score <= 100 else None


def valid_opinion(raw: Any, direction: str = "stance") -> bool:
    return (isinstance(raw, dict) and not raw.get("error") and
            raw.get(direction) in STANCES and valid_score(raw.get("score")) is not None and
            raw.get("confidence", "中") in CONFIDENCES)


def valid_weight(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return 1.0
    return min(1.2, max(0.8, number)) if math.isfinite(number) and number > 0 else 1.0
