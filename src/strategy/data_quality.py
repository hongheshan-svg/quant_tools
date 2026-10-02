"""策略数据边界：有限数值、A 股身份、重复行与价格口径连续性。"""

import math
from datetime import datetime

from src.utils.stock_code import StockCodeError, resolve_identity


def finite_number(value) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def iso_date(value) -> bool:
    if not isinstance(value, str) or len(value) != 10:
        return False
    try:
        return datetime.strptime(value, "%Y-%m-%d").strftime("%Y-%m-%d") == value
    except ValueError:
        return False


def equity_code(value: str) -> str | None:
    try:
        identity = resolve_identity(value)
    except StockCodeError:
        return None
    return identity.code if identity.kind == "stock" and identity.code.startswith(("0", "3", "6", "4", "8", "92")) else None


def row_priority(row) -> tuple:
    """规范裸代码优先；同格式按更新时间、主键确定顺序。"""
    return (row.code == equity_code(row.code), getattr(row, "updated_at", None) or datetime.min,
            getattr(row, "id", 0) or 0)


def prices_comparable(left, right) -> bool:
    """复权口径切换只在价格与公布涨幅一致时衔接，无法证明一致就切断窗口。

    老数据没有口径标签时保持可读；已知前复权与实时原价可在未发生除权的边界衔接。
    不对价格作猜测性换算。
    """
    a, b = getattr(left, "price_adjustment", None), getattr(right, "price_adjustment", None)
    if not a or not b:
        return True
    if a != b and {a, b} != {"none", "forward"}:
        return False
    previous, current = finite_number(left.close), finite_number(right.close)
    change = finite_number(getattr(right, "change_pct", None))
    if previous is None or current is None or min(previous, current) <= 0:
        return False
    # 同口径也可能混入不同抓取批次的复权价；用涨幅核对能发现除权后留下的旧价格。
    if change is None:
        return a == b
    tolerance = max(0.5, 2 / previous)  # 两分钱舍入误差（换算为百分点）
    return abs((current / previous - 1) * 100 - change) <= tolerance
