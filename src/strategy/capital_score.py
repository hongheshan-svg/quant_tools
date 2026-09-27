"""
资金流向评分因子
基于龙虎榜、北向资金等数据评估资金面
"""

from datetime import date

from loguru import logger

from src.database.db import get_db_session
from src.database.models import DragonTigerBoard, NorthboundFlow

DRAGON_NET_SUPER = 100_000_000
DRAGON_NET_STRONG = 50_000_000
DRAGON_NET_MEDIUM = 10_000_000
DRAGON_NET_SLIGHT_NEGATIVE = -10_000_000

NORTHBOUND_SUPER = 100
NORTHBOUND_STRONG = 50
NORTHBOUND_SLIGHT_NEGATIVE = -50


def calculate_capital_score(stock_code: str, db_path: str = "data/quant.db") -> float:
    """
    计算资金流向评分 (0-100)

    评分因子：
    - 龙虎榜净买入（正=利好，负=利空）
    - 龙虎榜买入席位质量（知名游资/机构加分）
    - 北向资金整体流向（大幅流入=利好）

    Args:
        stock_code: 股票代码
        db_path: 数据库路径

    Returns:
        0-100 的评分
    """
    try:
        today = date.today().strftime("%Y-%m-%d")
        score_parts = []

        with get_db_session(db_path) as session:
            # 1. 龙虎榜评分 (60%)
            dragon = (
                session.query(DragonTigerBoard)
                .filter(
                    DragonTigerBoard.code == stock_code,
                    DragonTigerBoard.trade_date == today,
                )
                .first()
            )

            if dragon:
                net = dragon.net_amount or 0

                # 净买入评分
                if net > DRAGON_NET_SUPER:  # 1亿+
                    dragon_score = 95
                elif net > DRAGON_NET_STRONG:  # 5000万+
                    dragon_score = 85
                elif net > DRAGON_NET_MEDIUM:  # 1000万+
                    dragon_score = 70
                elif net > 0:
                    dragon_score = 60
                elif net > DRAGON_NET_SLIGHT_NEGATIVE:
                    dragon_score = 40
                else:
                    dragon_score = 20

                # 分析买入席位（简单版）
                buy_seat_text = dragon.buy_seat or ""
                famous_seats = ["华鑫", "东方财富拉萨", "国泰君安上海", "中信证券"]
                for seat in famous_seats:
                    if seat in buy_seat_text:
                        dragon_score = min(100, dragon_score + 5)

                score_parts.append(("dragon", dragon_score, 0.60))
            else:
                score_parts.append(("dragon", 50, 0.60))

            # 2. 北向资金评分 (40%)
            northbound = (
                session.query(NorthboundFlow)
                .filter(NorthboundFlow.trade_date == today)
                .first()
            )

            if northbound and northbound.total_net_inflow is not None:
                flow = northbound.total_net_inflow  # 亿元
                if flow > NORTHBOUND_SUPER:
                    north_score = 95
                elif flow > NORTHBOUND_STRONG:
                    north_score = 80
                elif flow > 0:
                    north_score = 65
                elif flow > NORTHBOUND_SLIGHT_NEGATIVE:
                    north_score = 40
                else:
                    north_score = 20
                score_parts.append(("northbound", north_score, 0.40))
            else:
                score_parts.append(("northbound", 50, 0.40))

        # 加权合计
        total = sum(score * weight for _, score, weight in score_parts)
        return max(0, min(100, total))

    except Exception as e:
        logger.error(f"资金流向评分失败 [{stock_code}]: {e}")
        return 50
