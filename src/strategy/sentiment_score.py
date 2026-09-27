"""
舆情评分因子
基于 AI 舆情分析结果，对个股的舆情热度和方向进行评分
"""

from datetime import datetime, timedelta

from loguru import logger

from src.database.db import get_db_session
from src.database.models import SentimentAnalysis


def calculate_sentiment_score(stock_code: str, db_path: str = "data/quant.db") -> float:
    """
    计算个股的舆情评分 (0-100)

    评分逻辑：
    - 先按股票代码精确匹配
    - 再按板块/行业模糊匹配（权重减半）
    - 利好情绪越强、影响力越大、出现频率越高，分数越高
    - 利空情绪会降低分数
    - 考虑信息衰减效应（越近期的信息权重越大）
    """
    try:
        with get_db_session(db_path) as session:
            cutoff = datetime.now() - timedelta(hours=48)

            # 1. 精确匹配股票代码
            direct = (
                session.query(SentimentAnalysis)
                .filter(
                    SentimentAnalysis.related_stock_code == stock_code,
                    SentimentAnalysis.analyzed_at >= cutoff,
                )
                .all()
            )

            # 2. 如果精确匹配不到，尝试板块匹配
            sector_matches = []
            if not direct:
                # 获取该股票的板块信息
                from src.database.models import LimitUpStock
                stock = session.query(LimitUpStock).filter(
                    LimitUpStock.code == stock_code
                ).order_by(LimitUpStock.trade_date.desc()).first()

                if stock and stock.sector:
                    sector = stock.sector
                    sector_matches = (
                        session.query(SentimentAnalysis)
                        .filter(
                            SentimentAnalysis.analyzed_at >= cutoff,
                            SentimentAnalysis.related_sector.isnot(None),
                            SentimentAnalysis.related_sector.contains(sector),
                        )
                        .all()
                    )

            # 合并分析记录
            analyses = [(a, 1.0) for a in direct]  # 精确匹配权重 1.0
            analyses.extend((a, 0.4) for a in sector_matches)  # 板块匹配权重 0.4

            if not analyses:
                return 50  # 无数据返回中性分

            # 计算加权情绪分
            total_weight = 0
            weighted_score = 0

            now = datetime.now()
            for a, match_weight in analyses:
                age_hours = (now - (a.analyzed_at or now)).total_seconds() / 3600
                time_weight = max(0.1, 1.0 - age_hours / 48)
                impact = a.impact_score or 5

                weight = time_weight * impact * match_weight
                total_weight += weight

                if a.sentiment == "bullish":
                    sentiment_val = 75 + impact * 2.5  # 75-100
                elif a.sentiment == "bearish":
                    sentiment_val = 25 - impact * 2.5  # 0-25
                else:
                    sentiment_val = 50

                weighted_score += sentiment_val * weight

            final = weighted_score / total_weight if total_weight > 0 else 50

            return max(0, min(100, round(final, 1)))

    except Exception as e:
        logger.error(f"舆情评分计算失败 [{stock_code}]: {e}")
        return 50
