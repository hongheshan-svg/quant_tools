"""
常用技术指标（纯 Python，输入按日期升序的序列），供技术面评分和技术指标提醒共用。
"""

from __future__ import annotations


def sma(values: list[float], period: int) -> float | None:
    return sum(values[-period:]) / period if len(values) >= period else None


def ema(values: list[float], span: int) -> list[float]:
    k = 2 / (span + 1)
    out, prev = [], values[0]
    for v in values:
        prev = v * k + prev * (1 - k)
        out.append(prev)
    return out


def macd(closes: list[float], fast: int = 12, slow: int = 26, signal: int = 9) -> tuple[list[float], list[float]]:
    """(DIF, DEA) 序列。"""
    dif = [a - b for a, b in zip(ema(closes, fast), ema(closes, slow))]
    return dif, ema(dif, signal)


def rsi(closes: list[float], period: int) -> float | None:
    """Wilder 平滑 RSI。"""
    if len(closes) <= period:
        return None
    diffs = [b - a for a, b in zip(closes[:-1], closes[1:])]
    avg_gain = sum(max(d, 0) for d in diffs[:period]) / period
    avg_loss = sum(max(-d, 0) for d in diffs[:period]) / period
    for d in diffs[period:]:
        avg_gain = (avg_gain * (period - 1) + max(d, 0)) / period
        avg_loss = (avg_loss * (period - 1) + max(-d, 0)) / period
    if avg_loss == 0:
        return 100.0
    return 100 - 100 / (1 + avg_gain / avg_loss)


def kdj(highs: list[float], lows: list[float], closes: list[float], n: int = 9) -> tuple[list[float], list[float], list[float]]:
    """(K, D, J) 序列，K、D 初值 50，按 1/3 平滑（国内常用算法）。"""
    k_values, d_values, j_values = [], [], []
    k = d = 50.0
    for i in range(len(closes)):
        window_high = max(highs[max(0, i - n + 1): i + 1])
        window_low = min(lows[max(0, i - n + 1): i + 1])
        rsv = (closes[i] - window_low) / (window_high - window_low) * 100 if window_high > window_low else 50.0
        k = 2 / 3 * k + rsv / 3
        d = 2 / 3 * d + k / 3
        k_values.append(k)
        d_values.append(d)
        j_values.append(3 * k - 2 * d)
    return k_values, d_values, j_values


def crossed(prev_a: float, prev_b: float, a: float, b: float, direction: str) -> bool:
    """a 是否在最新一根上穿（up）或下穿（down）b。"""
    if direction == "up":
        return prev_a <= prev_b and a > b
    return prev_a >= prev_b and a < b
