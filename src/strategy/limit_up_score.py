"""
涨停连板评分因子
基于涨停板分析结果，评估连板概率
"""

from datetime import date

from loguru import logger

from src.database.db import get_db_session
from src.database.models import LimitUpStock

SEAL_RATIO_STRONG = 0.10
SEAL_RATIO_GOOD = 0.05
SEAL_RATIO_MEDIUM = 0.02
SEAL_RATIO_WEAK = 0.01

TIME_SLICE_LEN = 4
TIME_SUPER_EARLY = 935
TIME_EARLY = 1000
TIME_MORNING = 1030
TIME_NOON = 1130
TIME_AFTERNOON = 1400

DEFAULT_TIME_VALUE = 1200


def calculate_limit_up_score(stock_code: str, db_path: str = "data/quant.db") -> float:
    """
    计算涨停连板评分 (0-100)

    评分因子：
    - 连板天数（首板40分，二板65分，三板80分，过高递减）
    - 封板质量（封单比例、打开次数）
    - 封板时间（越早封板分越高）

    Args:
        stock_code: 股票代码
        db_path: 数据库路径

    Returns:
        0-100 的评分
    """
    try:
        today = date.today().strftime("%Y-%m-%d")

        with get_db_session(db_path) as session:
            record = (
                session.query(LimitUpStock)
                .filter(
                    LimitUpStock.code == stock_code,
                    LimitUpStock.trade_date == today,
                )
                .first()
            )

            if not record:
                return 0  # 今日未涨停

            # 1. 连板天数评分 (40%)
            days = record.continuous_days or 1
            day_scores = {1: 40, 2: 65, 3: 80, 4: 70, 5: 55}
            days_score = day_scores.get(days, max(35, 80 - (days - 3) * 15))

            # 2. 封单强度评分 (35%)
            seal_amount = record.seal_amount or 0
            circ_mv = record.circ_mv or 1
            seal_ratio = seal_amount / circ_mv if circ_mv > 0 else 0

            if seal_ratio > SEAL_RATIO_STRONG:
                seal_score = 95
            elif seal_ratio > SEAL_RATIO_GOOD:
                seal_score = 80
            elif seal_ratio > SEAL_RATIO_MEDIUM:
                seal_score = 65
            elif seal_ratio > SEAL_RATIO_WEAK:
                seal_score = 50
            else:
                seal_score = 30

            # 打开次数惩罚
            open_count = record.open_count or 0
            seal_score = max(10, seal_score - open_count * 15)

            # 3. 封板时间评分 (25%)
            timing_score = 50
            first_time = record.first_limit_time or ""
            if first_time:
                try:
                    t = first_time.replace(":", "")
                    t_val = int(t[:TIME_SLICE_LEN]) if len(t) >= TIME_SLICE_LEN else DEFAULT_TIME_VALUE
                    if t_val <= TIME_SUPER_EARLY:
                        timing_score = 95
                    elif t_val <= TIME_EARLY:
                        timing_score = 85
                    elif t_val <= TIME_MORNING:
                        timing_score = 70
                    elif t_val <= TIME_NOON:
                        timing_score = 55
                    elif t_val <= TIME_AFTERNOON:
                        timing_score = 40
                    else:
                        timing_score = 25
                except (ValueError, IndexError):
                    pass

            # 加权合计
            total = days_score * 0.40 + seal_score * 0.35 + timing_score * 0.25
            return max(0, min(100, total))

    except Exception as e:
        logger.error(f"涨停连板评分失败 [{stock_code}]: {e}")
        return 0
