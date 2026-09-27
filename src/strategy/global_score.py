"""
国际因子评分
基于隔夜美股表现、美股财报影响、国际重大事件等评分
"""

import json

from loguru import logger

from src.database.db import get_db_session
from src.database.models import (
    GlobalImpactAnalysis,
    LimitUpStock,
    USMarketDaily,
)

AVG_CHG_SUPER = 2
AVG_CHG_STRONG = 1
AVG_CHG_WEAK = -1
AVG_CHG_BAD = -2

VIX_HIGH = 30
VIX_WARN = 25
VIX_LOW = 15

CHINA_CONCEPT_STRONG = 3
CHINA_CONCEPT_WEAK = -3


def calculate_global_score(stock_code: str, db_path: str = "data/quant.db") -> float:
    """
    计算国际因子评分 (0-100)

    评分因子：
    - 隔夜美股表现（三大指数涨跌）
    - 国际因子分析结果中是否提及该股所属板块/个股
    - 美股对应公司的财报影响

    Args:
        stock_code: 股票代码
        db_path: 数据库路径

    Returns:
        0-100 的评分
    """
    try:
        score_parts = []

        with get_db_session(db_path) as session:
            # 1. 隔夜美股整体表现 (40%)
            us_market = (
                session.query(USMarketDaily)
                .order_by(USMarketDaily.trade_date.desc())
                .first()
            )

            if us_market:
                nasdaq_chg = us_market.nasdaq_change_pct or 0
                sp500_chg = us_market.sp500_change_pct or 0
                avg_chg = (nasdaq_chg + sp500_chg) / 2

                if avg_chg > AVG_CHG_SUPER:
                    us_score = 90
                elif avg_chg > AVG_CHG_STRONG:
                    us_score = 75
                elif avg_chg > 0:
                    us_score = 60
                elif avg_chg > AVG_CHG_WEAK:
                    us_score = 45
                elif avg_chg > AVG_CHG_BAD:
                    us_score = 30
                else:
                    us_score = 15

                # VIX 恐慌指数修正
                vix = us_market.vix
                if vix is not None:
                    if vix > VIX_HIGH:
                        us_score = max(us_score - 15, 5)
                    elif vix > VIX_WARN:
                        us_score = max(us_score - 8, 10)
                    elif vix < VIX_LOW:
                        us_score = min(us_score + 5, 95)

                # 中概股修正（中概暴涨/跌直接影响互联网板块）
                china_concept_chg = us_market.china_concept_change_pct
                if china_concept_chg is not None:
                    if china_concept_chg > CHINA_CONCEPT_STRONG:
                        us_score = min(us_score + 8, 95)
                    elif china_concept_chg < CHINA_CONCEPT_WEAK:
                        us_score = max(us_score - 8, 5)

                score_parts.append(("us_market", us_score, 0.40))
            else:
                score_parts.append(("us_market", 50, 0.40))

            # 2. 国际因子分析是否利好该股 (60%)
            impact = (
                session.query(GlobalImpactAnalysis)
                .order_by(GlobalImpactAnalysis.analysis_date.desc())
                .first()
            )

            if impact and impact.analysis_detail:
                impact_score = _score_from_impact(stock_code, impact, session)
                score_parts.append(("impact", impact_score, 0.60))
            else:
                score_parts.append(("impact", 50, 0.60))

        total = sum(score * weight for _, score, weight in score_parts)
        return max(0, min(100, total))

    except Exception as e:
        logger.error(f"国际因子评分失败 [{stock_code}]: {e}")
        return 50


def _score_from_impact(stock_code: str, impact: GlobalImpactAnalysis, session) -> float:
    """根据国际因子分析结果评分"""
    score = 50

    try:
        # 获取该股的板块信息
        stock_record = (
            session.query(LimitUpStock)
            .filter(LimitUpStock.code == stock_code)
            .order_by(LimitUpStock.trade_date.desc())
            .first()
        )
        stock_sector = stock_record.sector if stock_record else ""

        # 解析分析结果
        detail = json.loads(impact.analysis_detail) if impact.analysis_detail else {}

        # 总体方向
        direction = detail.get("overall_direction", "neutral")
        if direction == "bullish":
            score += 10
        elif direction == "bearish":
            score -= 10

        # 检查是否在受益/受损名单中
        for event in detail.get("key_events", []):
            benefited = event.get("benefited_stocks", [])
            hurt = event.get("hurt_stocks", [])
            affected_sectors = event.get("affected_sectors", [])
            event_score = event.get("impact_score", 5)

            # 检查个股是否被提及
            for stock_str in benefited:
                if stock_code in str(stock_str):
                    score += event_score * 3
                    break

            for stock_str in hurt:
                if stock_code in str(stock_str):
                    score -= event_score * 3
                    break

            # 检查板块是否被影响
            if stock_sector:
                for sector in affected_sectors:
                    if any(s in stock_sector for s in str(sector).split(",")):
                        if event.get("impact_direction") == "bullish":
                            score += event_score * 2
                        elif event.get("impact_direction") == "bearish":
                            score -= event_score * 2
                        break

    except Exception as e:
        logger.debug(f"解析国际因子影响失败: {e}")

    return max(0, min(100, score))
