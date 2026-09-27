"""
涨停板分析器
分析涨停股票的连板概率，结合多维因子评分
"""

from datetime import date

from loguru import logger

from src.config_loader import load_config
from src.database.db import get_db_session
from src.database.models import LimitUpStock, SentimentAnalysis

MAX_SAFE_CONTINUOUS_DAYS = 6

SEAL_RATIO_STRONG = 0.10
SEAL_RATIO_GOOD = 0.05
SEAL_RATIO_MEDIUM = 0.02
SEAL_RATIO_WEAK = 0.01

SECTOR_COUNT_STRONG = 5
SECTOR_COUNT_GOOD = 3
SECTOR_COUNT_MEDIUM = 2

TIME_SLICE_LEN = 4
TIME_SUPER_EARLY = 935
TIME_EARLY = 1000
TIME_MORNING = 1030
TIME_NOON = 1130
TIME_AFTERNOON = 1400


class LimitUpAnalyzer:
    """涨停板分析器"""

    def __init__(self, config: dict = None):
        self.config = config or load_config()
        self.db_path = self.config.get("database", {}).get("sqlite_path", "data/quant.db")

    def analyze_today(self) -> list[dict]:
        """分析今日涨停板，返回涨停股详情列表"""
        today = date.today().strftime("%Y-%m-%d")
        logger.info(f"开始涨停板分析: {today}")

        stocks = self._get_limit_up_stocks(today)
        if not stocks:
            logger.info("今日无涨停股票")
            return []

        results = []
        for stock in stocks:
            analysis = self._analyze_single(stock, today)
            results.append(analysis)

        # 按综合得分排序
        results.sort(key=lambda x: x.get("total_score", 0), reverse=True)
        logger.info(f"涨停板分析完成: {len(results)} 只股票")
        return results

    def _get_limit_up_stocks(self, trade_date: str) -> list[dict]:
        """获取今日涨停股票列表"""
        stocks = []
        try:
            with get_db_session(self.db_path) as session:
                records = (
                    session.query(LimitUpStock)
                    .filter(LimitUpStock.trade_date == trade_date)
                    .all()
                )
                stocks.extend({
                        "code": r.code,
                        "name": r.name,
                        "close": r.close,
                        "change_pct": r.change_pct,
                        "continuous_days": r.continuous_days or 1,
                        "seal_amount": r.seal_amount,
                        "first_limit_time": r.first_limit_time,
                        "last_limit_time": r.last_limit_time,
                        "open_count": r.open_count or 0,
                        "sector": r.sector,
                        "reason": r.reason,
                        "circ_mv": r.circ_mv,
                    } for r in records)
        except Exception as e:
            logger.error(f"获取涨停股票失败: {e}")
        return stocks

    def _analyze_single(self, stock: dict, trade_date: str) -> dict:
        """分析单只涨停股票"""
        code = stock["code"]

        # 1. 连板高度评分 (0-100)
        continuous_score = self._score_continuous_days(stock.get("continuous_days", 1))

        # 2. 封单强度评分 (0-100)
        seal_score = self._score_seal_strength(stock)

        # 3. 板块效应评分 (0-100)
        sector_score = self._score_sector_effect(stock.get("sector", ""), trade_date)

        # 4. 封板时间评分 (0-100)
        timing_score = self._score_limit_timing(stock)

        # 5. 题材热度评分 (0-100) — 根据舆情分析结果
        theme_score = self._score_theme_heat(code, stock.get("reason", ""), trade_date)

        # 综合评分
        total_score = (
            continuous_score * 0.20 +
            seal_score * 0.25 +
            sector_score * 0.20 +
            timing_score * 0.15 +
            theme_score * 0.20
        )

        return {
            "code": code,
            "name": stock.get("name", ""),
            "continuous_days": stock.get("continuous_days", 1),
            "close": stock.get("close"),
            "sector": stock.get("sector"),
            "reason": stock.get("reason"),
            "continuous_score": round(continuous_score, 1),
            "seal_score": round(seal_score, 1),
            "sector_score": round(sector_score, 1),
            "timing_score": round(timing_score, 1),
            "theme_score": round(theme_score, 1),
            "total_score": round(total_score, 1),
        }

    @staticmethod
    def _score_continuous_days(days: int) -> float:
        """连板高度评分

        首板: 40, 二板: 65, 三板: 80, 四板+: 55(过高风险增加)
        """
        score_map = {1: 40, 2: 65, 3: 80, 4: 70, 5: 55}
        if days >= MAX_SAFE_CONTINUOUS_DAYS:
            return 40  # 高位连板，风险大
        return score_map.get(days, 40)

    @staticmethod
    def _score_seal_strength(stock: dict) -> float:
        """封单强度评分"""
        seal_amount = stock.get("seal_amount") or 0
        circ_mv = stock.get("circ_mv") or 1

        if circ_mv <= 0:
            return 50

        # 封单金额 / 流通市值
        ratio = seal_amount / circ_mv
        if ratio > SEAL_RATIO_STRONG:
            return 95
        if ratio > SEAL_RATIO_GOOD:
            return 80
        if ratio > SEAL_RATIO_MEDIUM:
            return 65
        if ratio > SEAL_RATIO_WEAK:
            return 50
        return 30

    def _score_sector_effect(self, sector: str, trade_date: str) -> float:
        """板块效应评分 - 同板块涨停家数越多分越高"""
        if not sector:
            return 50

        try:
            with get_db_session(self.db_path) as session:
                count = (
                    session.query(LimitUpStock)
                    .filter(
                        LimitUpStock.trade_date == trade_date,
                        LimitUpStock.sector.contains(sector.split(",")[0] if "," in sector else sector),
                    )
                    .count()
                )
                if count >= SECTOR_COUNT_STRONG:
                    return 95
                if count >= SECTOR_COUNT_GOOD:
                    return 80
                if count >= SECTOR_COUNT_MEDIUM:
                    return 65
                return 45
        except Exception:
            return 50

    @staticmethod
    def _score_limit_timing(stock: dict) -> float:
        """封板时间评分 - 越早封板分越高"""
        first_time = stock.get("first_limit_time", "")
        open_count = stock.get("open_count", 0)

        score = 50  # 基准
        if first_time:
            try:
                parts = first_time.replace(":", "")
                time_val = int(parts[:TIME_SLICE_LEN]) if len(parts) >= TIME_SLICE_LEN else 1200
                if time_val <= TIME_SUPER_EARLY:    # 9:35 前封板（一字板或秒板）
                    score = 95
                elif time_val <= TIME_EARLY:  # 10:00 前
                    score = 85
                elif time_val <= TIME_MORNING:  # 10:30 前
                    score = 75
                elif time_val <= TIME_NOON:  # 上午
                    score = 60
                elif time_val <= TIME_AFTERNOON:  # 下午2点前
                    score = 45
                else:                   # 尾盘封板
                    score = 30
            except (ValueError, IndexError):
                pass

        # 打开次数惩罚
        score -= open_count * 10
        return max(score, 10)

    def _score_theme_heat(self, code: str, reason: str, trade_date: str) -> float:
        """题材热度评分 - 基于舆情分析结果"""
        try:
            with get_db_session(self.db_path) as session:
                # 查找该股票的舆情分析
                analyses = (
                    session.query(SentimentAnalysis)
                    .filter(
                        SentimentAnalysis.related_stock_code == code,
                        SentimentAnalysis.sentiment == "bullish",
                    )
                    .order_by(SentimentAnalysis.analyzed_at.desc())
                    .limit(10)
                    .all()
                )

                if not analyses and reason:
                    # 尝试通过板块/原因匹配
                    analyses = (
                        session.query(SentimentAnalysis)
                        .filter(
                            SentimentAnalysis.related_sector.contains(reason[:10]),
                            SentimentAnalysis.sentiment == "bullish",
                        )
                        .limit(10)
                        .all()
                    )

                if not analyses:
                    return 40

                # 根据匹配数量和影响力评分
                avg_impact = sum(a.impact_score or 5 for a in analyses) / len(analyses)
                count_score = min(len(analyses) * 10, 50)
                impact_score = avg_impact * 5

                return min(count_score + impact_score, 100)

        except Exception as e:
            logger.debug(f"题材热度评分失败 [{code}]: {e}")
            return 40
