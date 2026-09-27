"""
技术面评分因子
基于均线形态、量价关系等技术指标评分
"""


from loguru import logger

from src.database.db import get_db_session
from src.database.models import StockDaily

RAW_CODE_LEN = 6
EXCHANGE_CODE_LEN = 8
EXCHANGE_PREFIX_LEN = 2

MIN_RECORDS_REQUIRED = 5
MIN_MA_RECORDS = 20
VOLUME_WINDOW = 5
PREV_VOLUME_WINDOW = 10

VOL_RATIO_STRONG = 1.5
VOL_RATIO_BASE = 1.0
VOL_RATIO_SHRINK = 0.7

PRICE_CHANGE_STRONG = 15
PRICE_CHANGE_GOOD = 8
PRICE_CHANGE_MEDIUM = 3
PRICE_CHANGE_WEAK = -5


def _code_candidates(stock_code: str) -> list[str]:
    raw = (stock_code or "").strip().lower()
    if not raw:
        return []
    cands = [raw]
    if len(raw) == RAW_CODE_LEN and raw.isdigit():
        cands.extend([f"sh{raw}", f"sz{raw}", f"bj{raw}"])
    elif len(raw) == EXCHANGE_CODE_LEN and raw[:EXCHANGE_PREFIX_LEN] in {"sh", "sz", "bj"} and raw[EXCHANGE_PREFIX_LEN:].isdigit():
        bare = raw[EXCHANGE_PREFIX_LEN:]
        cands.extend([bare, f"sh{bare}", f"sz{bare}", f"bj{bare}"])
    return list(dict.fromkeys(cands))


def calculate_tech_score(stock_code: str, db_path: str = "data/quant.db") -> float:
    """
    计算技术面评分 (0-100)

    评分因子：
    - 量价配合（放量上涨=好）
    - 均线多头排列
    - 近期走势（相对强弱）

    Args:
        stock_code: 股票代码
        db_path: 数据库路径

    Returns:
        0-100 的评分
    """
    try:
        with get_db_session(db_path) as session:
            # 获取最近30个交易日数据
            cands = _code_candidates(stock_code)
            records = (
                session.query(StockDaily)
                .filter(StockDaily.code.in_(cands))
                .order_by(StockDaily.trade_date.desc())
                .limit(30)
                .all()
            )

            if not records or len(records) < MIN_RECORDS_REQUIRED:
                return 50  # 数据不足

            # 按日期正序排列
            records = list(reversed(records))

            closes = [r.close for r in records if r.close]
            volumes = [r.volume for r in records if r.volume]
            changes = [r.change_pct for r in records if r.change_pct is not None]

            if len(closes) < MIN_RECORDS_REQUIRED:
                return 50

            # 1. 量价配合评分 (40%)
            vp_score = _volume_price_score(closes, volumes)

            # 2. 均线评分 (30%)
            ma_score = _moving_average_score(closes)

            # 3. 近期强度评分 (30%)
            strength_score = _relative_strength_score(changes)

            total = vp_score * 0.40 + ma_score * 0.30 + strength_score * 0.30
            return max(0, min(100, total))

    except Exception as e:
        logger.error(f"技术面评分失败 [{stock_code}]: {e}")
        return 50


def _volume_price_score(closes: list[float], volumes: list[float]) -> float:
    """量价配合评分"""
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


def _moving_average_score(closes: list[float]) -> float:
    """均线形态评分"""
    if len(closes) < MIN_MA_RECORDS:
        return 50

    ma5 = sum(closes[-5:]) / 5
    ma10 = sum(closes[-10:]) / 10
    ma20 = sum(closes[-20:]) / 20

    current = closes[-1]

    # 多头排列: 价格 > MA5 > MA10 > MA20
    if current > ma5 > ma10 > ma20:
        return 90
    if current > ma5 > ma10:
        return 75
    if current > ma5:
        return 60
    if current > ma20:
        return 45
    if current < ma5 < ma10 < ma20:
        return 15  # 空头排列
    return 40


def _relative_strength_score(changes: list[float]) -> float:
    """近期相对强度评分"""
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
