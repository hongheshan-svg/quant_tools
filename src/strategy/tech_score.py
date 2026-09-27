"""
技术面评分因子
基于均线趋势、量价关系、近期强度、MACD、RSI 评分，并给出理由和风险提示。

参考 daily_stock_analysis 的趋势分析器，但按打板/连板策略调整：
涨停股天然远离均线，乖离率只作为高位风险扣分，不作为禁买条件；RSI 高位视为偏热风险而非买点。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from loguru import logger

from src.database.db import get_db_session
from src.database.models import StockDaily
from src.utils.stock_code import code_candidates

HISTORY_LIMIT = 60
MIN_RECORDS_REQUIRED = 5
MIN_MA_RECORDS = 20
MIN_MACD_RECORDS = 35
VOLUME_WINDOW = 5
PREV_VOLUME_WINDOW = 10

VOL_RATIO_STRONG = 1.5
VOL_RATIO_BASE = 1.0
VOL_RATIO_SHRINK = 0.7

PRICE_CHANGE_STRONG = 15
PRICE_CHANGE_GOOD = 8
PRICE_CHANGE_MEDIUM = 3
PRICE_CHANGE_WEAK = -5

# 各部分满分：趋势 25 + 量价 25 + 近期强度 20 + MACD 15 + RSI 15 = 100
TREND_POINTS = 25
VOLUME_PRICE_POINTS = 25
STRENGTH_POINTS = 20

RSI_PERIOD = 6
RSI_STRONG_LOW = 50
RSI_HOT = 80
RSI_EXTREME = 90
RSI_WEAK = 40

BIAS_WARN = 8.0     # 偏离 5 日线超过 8% 扣 5 分
BIAS_DANGER = 15.0  # 超过 15% 扣 10 分


@dataclass
class TechnicalAnalysis:
    score: float = 50.0
    trend: str = ""
    bias_ma5: float | None = None       # (收盘 - MA5) / MA5 %
    volume_ratio: float | None = None   # 最新成交量 / 前 5 日均量
    macd: str = ""
    rsi6: float | None = None
    reasons: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)

    def brief(self) -> str:
        """一行摘要，供 LLM 提示词和界面使用。"""
        parts = [p for p in (self.trend, self.macd) if p]
        if self.rsi6 is not None:
            parts.append(f"RSI6={self.rsi6:.0f}")
        if self.bias_ma5 is not None:
            parts.append(f"偏离5日线{self.bias_ma5:+.1f}%")
        if self.volume_ratio is not None:
            parts.append(f"量比{self.volume_ratio:.1f}")
        if self.risks:
            parts.append("风险:" + "；".join(self.risks))
        return " ".join(parts)


def calculate_tech_score(stock_code: str, db_path: str = "data/quant.db") -> float:
    """计算技术面评分 (0-100)，数据不足或出错时返回 50。"""
    return analyze_technical(stock_code, db_path).score


def analyze_technical(stock_code: str, db_path: str = "data/quant.db") -> TechnicalAnalysis:
    """读取最近 60 个交易日行情做技术分析。"""
    try:
        with get_db_session(db_path) as session:
            records = (
                session.query(StockDaily)
                .filter(StockDaily.code.in_(code_candidates(stock_code)))
                .order_by(StockDaily.trade_date.desc())
                .limit(HISTORY_LIMIT)
                .all()
            )
            records = list(reversed(records))  # 按日期正序
            closes = [r.close for r in records if r.close]
            volumes = [r.volume for r in records if r.volume]
            changes = [r.change_pct for r in records if r.change_pct is not None]
        return analyze_series(closes, volumes, changes)
    except Exception as e:
        logger.error(f"技术面评分失败 [{stock_code}]: {e}")
        return TechnicalAnalysis()


def analyze_series(closes: list[float], volumes: list[float], changes: list[float]) -> TechnicalAnalysis:
    """按日期正序的收盘价、成交量、涨跌幅序列做技术分析。"""
    result = TechnicalAnalysis()
    if len(closes) < MIN_RECORDS_REQUIRED:
        return result  # 数据不足

    trend_points = _analyze_trend(closes, result)
    vp_points = _volume_price_score(closes, volumes) / 100 * VOLUME_PRICE_POINTS
    strength_points = _relative_strength_score(changes) / 100 * STRENGTH_POINTS
    macd_points = _analyze_macd(closes, result)
    rsi_points = _analyze_rsi(closes, result)
    _analyze_volume(volumes, closes, result)
    penalty = _bias_penalty(closes, result)

    total = trend_points + vp_points + strength_points + macd_points + rsi_points - penalty
    result.score = round(max(0.0, min(100.0, total)), 2)
    return result


def _ma(values: list[float], period: int) -> float:
    return sum(values[-period:]) / period


def _analyze_trend(closes: list[float], result: TechnicalAnalysis) -> float:
    """均线趋势，满分 25。"""
    current = closes[-1]
    ma5 = _ma(closes, 5)
    if len(closes) < MIN_MA_RECORDS:
        result.trend = "站上5日线" if current > ma5 else "跌破5日线"
        return 15.0 if current > ma5 else 8.0

    ma10, ma20 = _ma(closes, 10), _ma(closes, 20)
    if current > ma5 > ma10 > ma20:
        result.trend = "多头排列"
        result.reasons.append("均线多头排列")
        return 25.0
    if ma5 > ma10 > ma20:
        result.trend = "多头回踩"
        return 21.0
    if current > ma5 > ma10:
        result.trend = "短线走强"
        return 18.0
    if current < ma5 < ma10 < ma20:
        result.trend = "空头排列"
        result.risks.append("均线空头排列")
        return 3.0
    if current > ma20:
        result.trend = "站上20日线"
        return 12.0
    result.trend = "均线纠缠"
    return 10.0


def _ema(values: list[float], span: int) -> list[float]:
    k = 2 / (span + 1)
    out, prev = [], values[0]
    for v in values:
        prev = v * k + prev * (1 - k)
        out.append(prev)
    return out


def _analyze_macd(closes: list[float], result: TechnicalAnalysis) -> float:
    """MACD(12,26,9)，满分 15；数据不足时给中性 7 分。"""
    if len(closes) < MIN_MACD_RECORDS:
        return 7.0
    ema12, ema26 = _ema(closes, 12), _ema(closes, 26)
    dif = [a - b for a, b in zip(ema12, ema26)]
    dea = _ema(dif, 9)
    crossed_up = dif[-1] > dea[-1] and dif[-2] <= dea[-2]
    crossed_down = dif[-1] < dea[-1] and dif[-2] >= dea[-2]

    if crossed_up:
        result.macd = "MACD零轴上金叉" if dif[-1] > 0 else "MACD金叉"
        result.reasons.append(result.macd)
        return 15.0 if dif[-1] > 0 else 12.0
    if crossed_down:
        result.macd = "MACD死叉"
        result.risks.append(result.macd)
        return 0.0
    if dif[-2] <= 0 < dif[-1]:
        result.macd = "MACD上穿零轴"
        result.reasons.append(result.macd)
        return 11.0
    if dif[-1] > dea[-1] > 0:
        result.macd = "MACD多头"
        return 10.0
    if dif[-1] > dea[-1]:
        result.macd = "MACD零轴下反弹"
        return 6.0
    result.macd = "MACD空头"
    return 3.0


def _rsi(closes: list[float], period: int) -> float | None:
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


def _analyze_rsi(closes: list[float], result: TechnicalAnalysis) -> float:
    """RSI6，满分 15。打板策略下 50~80 为强势区；80 以上偏热，90 以上极度超买。"""
    rsi = _rsi(closes, RSI_PERIOD)
    if rsi is None:
        return 7.0
    result.rsi6 = round(rsi, 1)
    if rsi >= RSI_EXTREME:
        result.risks.append(f"RSI6={rsi:.0f}极度超买")
        return 4.0
    if rsi >= RSI_HOT:
        result.risks.append(f"RSI6={rsi:.0f}偏热")
        return 9.0
    if rsi >= RSI_STRONG_LOW:
        return 15.0
    if rsi >= RSI_WEAK:
        return 8.0
    return 4.0


def _analyze_volume(volumes: list[float], closes: list[float], result: TechnicalAnalysis) -> None:
    """最新量比（相对前 5 日均量），只做描述不计分（量价评分已单独计算）。"""
    if len(volumes) < VOLUME_WINDOW + 1:
        return
    base = sum(volumes[-VOLUME_WINDOW - 1 : -1]) / VOLUME_WINDOW
    if base <= 0:
        return
    result.volume_ratio = round(volumes[-1] / base, 2)
    if result.volume_ratio >= VOL_RATIO_STRONG and len(closes) >= 2 and closes[-1] < closes[-2]:
        result.risks.append("放量下跌")


def _bias_penalty(closes: list[float], result: TechnicalAnalysis) -> float:
    """偏离 5 日线过远视为高位风险：超过 8% 扣 5 分，超过 15% 扣 10 分。"""
    ma5 = _ma(closes, 5)
    if ma5 <= 0:
        return 0.0
    bias = (closes[-1] - ma5) / ma5 * 100
    result.bias_ma5 = round(bias, 2)
    if bias > BIAS_DANGER:
        result.risks.append(f"偏离5日线{bias:+.1f}%，高位追涨风险大")
        return 10.0
    if bias > BIAS_WARN:
        result.risks.append(f"偏离5日线{bias:+.1f}%")
        return 5.0
    return 0.0


def _volume_price_score(closes: list[float], volumes: list[float]) -> float:
    """量价配合评分（0-100）"""
    if len(closes) < MIN_RECORDS_REQUIRED or len(volumes) < MIN_RECORDS_REQUIRED:
        return 50

    # 最近5日平均成交量 vs 前5日平均成交量
    recent_vol = sum(volumes[-VOLUME_WINDOW:]) / VOLUME_WINDOW
    prev_vol = sum(volumes[-PREV_VOLUME_WINDOW:-VOLUME_WINDOW]) / VOLUME_WINDOW if len(volumes) >= PREV_VOLUME_WINDOW else recent_vol

    # 最近价格趋势
    recent_price_up = closes[-1] > closes[-VOLUME_WINDOW]

    vol_ratio = recent_vol / prev_vol if prev_vol > 0 else 1

    if recent_price_up and vol_ratio > VOL_RATIO_STRONG:
        return 90  # 放量上涨
    if recent_price_up and vol_ratio > VOL_RATIO_BASE:
        return 75  # 温和放量上涨
    if recent_price_up and vol_ratio < VOL_RATIO_BASE:
        return 60  # 缩量上涨
    if not recent_price_up and vol_ratio > VOL_RATIO_STRONG:
        return 25  # 放量下跌
    if not recent_price_up and vol_ratio < VOL_RATIO_SHRINK:
        return 45  # 缩量下跌（可能止跌）
    return 50


def _relative_strength_score(changes: list[float]) -> float:
    """近期相对强度评分（0-100）"""
    if len(changes) < MIN_RECORDS_REQUIRED:
        return 50

    # 最近5日累计涨跌幅
    recent_5 = sum(changes[-VOLUME_WINDOW:])

    if recent_5 > PRICE_CHANGE_STRONG:
        return 90  # 近5日涨幅超15%
    if recent_5 > PRICE_CHANGE_GOOD:
        return 75
    if recent_5 > PRICE_CHANGE_MEDIUM:
        return 65
    if recent_5 > 0:
        return 55
    if recent_5 > PRICE_CHANGE_WEAK:
        return 40
    return 25
