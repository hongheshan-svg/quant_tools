"""
大盘环境评估（参考 daily_stock_analysis 的三段式复盘：趋势结构 → 资金情绪 → 行动框架）

按打板短线的关注点，从数据库已采集的行情和涨停池计算：
- 趋势（35 分）：涨跌家数比、三大指数平均涨跌幅
- 短线情绪（45 分）：赚钱效应（昨日涨停今日表现）、涨停/跌停家数、最高连板高度、一次封板率
- 量能（20 分）：两市成交额较前一交易日的变化
总分映射为 进攻 / 均衡 / 防守 / 冰点，并给出新开仓的仓位系数；另外判断短线情绪周期。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from loguru import logger

from src.config_loader import load_config
from src.database.db import get_db_session
from src.database.models import LimitUpStock, StockDaily
from src.utils.stock_code import bare_code, daily_limit_pct

LIMIT_PCT_TOLERANCE = 0.5
DEFAULT_POSITION_FACTORS = {"进攻": 1.0, "均衡": 0.6, "防守": 0.3, "冰点": 0.0}
ATTACK_SCORE = 65
BALANCED_SCORE = 40
FREEZE_SCORE = 20
COOLING_LIMIT_DOWN = 15  # 跌停达到该数视为亏钱效应扩散
MIN_MARKET_SAMPLE = 300  # 当天有行情的股票少于该数时视为数据不足（全市场约 5000 只）


@dataclass
class DayStats:
    trade_date: str = ""
    up: int = 0
    down: int = 0
    limit_up: int = 0
    limit_down: int = 0
    max_height: int = 0          # 最高连板
    multi_board: int = 0         # 连板家数（≥2 板）
    first_seal_rate: float | None = None   # 一次封板率（全天未开板的涨停占比）
    amount_yi: float = 0.0


@dataclass
class MarketRegime:
    trade_date: str = ""
    regime: str = "未知"            # 进攻 / 均衡 / 防守 / 冰点 / 未知
    score: float = 0.0
    position_factor: float = 1.0
    emotion_cycle: str = "未知"     # 高潮 / 升温 / 震荡 / 退潮 / 冰点
    metrics: dict[str, Any] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)

    def summary(self) -> str:
        """一段话摘要，供 LLM 提示词、日报和界面使用。"""
        if self.regime == "未知":
            return "市场环境：数据不足，无法评估"
        m = self.metrics
        parts = [f"市场环境：{self.regime}（{self.score:.0f}分，新开仓仓位×{self.position_factor:.1f}）", f"情绪周期：{self.emotion_cycle}"]
        if m.get("profit_effect") is not None:
            parts.append(f"昨日涨停今日均涨{m['profit_effect']:+.2f}%")
        if m.get("promotion_rate") is not None:
            parts.append(f"晋级率{m['promotion_rate']:.0f}%")
        parts.append(f"涨停{m.get('limit_up', 0)}/跌停{m.get('limit_down', 0)}")
        parts.append(f"最高{m.get('max_height', 0)}板")
        if m.get("amount_change_pct") is not None:
            parts.append(f"成交额{m['amount_change_pct']:+.1f}%")
        return "，".join(parts)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class MarketRegimeAnalyzer:
    """从数据库计算大盘环境。"""

    def __init__(self, config: dict | None = None):
        self.config = config or load_config()
        self.db_path = self.config.get("database", {}).get("sqlite_path", "data/quant.db")
        factors = self.config.get("risk", {}).get("market_regime_position") or {}
        self.position_factors = {**DEFAULT_POSITION_FACTORS, **factors}

    def analyze(self, trade_date: str | None = None, overview: dict | None = None) -> MarketRegime:
        """trade_date 默认取数据库中最新的行情日；overview 为市场概况（含三大指数涨跌幅），默认取进程内缓存。"""
        try:
            with get_db_session(self.db_path) as session:
                dates = self._trade_dates(session, trade_date)
                if not dates:
                    return MarketRegime()
                today = self._day_stats(session, dates[0])
                prev = self._day_stats(session, dates[1]) if len(dates) > 1 else None
                profit_effect, promotion_rate = self._yesterday_limit_up_performance(session, dates)
        except Exception as e:
            logger.warning(f"大盘环境评估失败: {e}")
            return MarketRegime()
        if today.up + today.down < MIN_MARKET_SAMPLE:
            logger.debug(f"大盘环境评估：{today.trade_date} 行情样本 {today.up + today.down} 只，数据不足")
            return MarketRegime(trade_date=today.trade_date)
        if overview is None:
            overview = self._cached_overview()
        return self._evaluate(today, prev, profit_effect, promotion_rate, overview or {})

    # ---------- 数据 ----------

    @staticmethod
    def _trade_dates(session, trade_date: str | None) -> list[str]:
        query = session.query(StockDaily.trade_date).distinct()
        if trade_date:
            query = query.filter(StockDaily.trade_date <= trade_date)
        return [d for (d,) in query.order_by(StockDaily.trade_date.desc()).limit(2).all()]

    @staticmethod
    def _day_stats(session, trade_date: str) -> DayStats:
        stats = DayStats(trade_date=trade_date)
        rows = (
            session.query(StockDaily.code, StockDaily.name, StockDaily.change_pct, StockDaily.amount)
            .filter(StockDaily.trade_date == trade_date)
            .all()
        )
        for code, name, chg, amount in rows:
            if chg is None:
                continue
            stats.up += chg > 0
            stats.down += chg < 0
            if chg <= -(daily_limit_pct(code, name or "") * 100 - LIMIT_PCT_TOLERANCE):
                stats.limit_down += 1
            stats.amount_yi += (amount or 0) / 1e8
        limit_ups = session.query(LimitUpStock.continuous_days, LimitUpStock.open_count).filter(LimitUpStock.trade_date == trade_date).all()
        stats.limit_up = len(limit_ups)
        if limit_ups:
            heights = [int(d or 1) for d, _ in limit_ups]
            stats.max_height = max(heights)
            stats.multi_board = sum(1 for h in heights if h >= 2)
            stats.first_seal_rate = sum(1 for _, o in limit_ups if not o) / len(limit_ups)
        return stats

    @staticmethod
    def _yesterday_limit_up_performance(session, dates: list[str]) -> tuple[float | None, float | None]:
        """昨日涨停股今日的平均涨跌幅（赚钱效应）和再次涨停的比例（晋级率 %）。"""
        if len(dates) < 2:
            return None, None
        today, prev = dates
        prev_codes = {bare_code(c) for (c,) in session.query(LimitUpStock.code).filter(LimitUpStock.trade_date == prev).all()}
        if not prev_codes:
            return None, None
        changes = [
            chg
            for code, chg in session.query(StockDaily.code, StockDaily.change_pct).filter(StockDaily.trade_date == today).all()
            if bare_code(code) in prev_codes and chg is not None
        ]
        today_limit_ups = {bare_code(c) for (c,) in session.query(LimitUpStock.code).filter(LimitUpStock.trade_date == today).all()}
        promoted = len(prev_codes & today_limit_ups)
        profit = round(sum(changes) / len(changes), 2) if changes else None
        return profit, round(promoted / len(prev_codes) * 100, 1)

    @staticmethod
    def _cached_overview() -> dict:
        try:
            from src.collectors.stock_data import StockDataCollector

            return dict(StockDataCollector._market_overview_cache or {})
        except Exception:
            return {}

    # ---------- 评分 ----------

    def _evaluate(self, today: DayStats, prev: DayStats | None, profit_effect, promotion_rate, overview: dict) -> MarketRegime:
        reasons: list[str] = []

        # 1. 趋势：涨跌家数比 20 + 指数 15（无指数数据时按家数比折算满 35）
        total = today.up + today.down
        breadth = today.up / total if total else 0.5
        breadth_pts = 20 if breadth >= 0.7 else 15 if breadth >= 0.55 else 10 if breadth >= 0.45 else 5 if breadth >= 0.3 else 0
        index_changes = [float(overview[k]) for k in ("sh_change_pct", "sz_change_pct", "cy_change_pct") if isinstance(overview.get(k), (int, float)) and overview.get("sh_index")]
        if index_changes:
            avg_index = sum(index_changes) / len(index_changes)
            index_pts = 15 if avg_index >= 1 else 11 if avg_index >= 0.3 else 7 if avg_index >= -0.3 else 3 if avg_index >= -1 else 0
            trend = breadth_pts + index_pts
        else:
            avg_index = None
            trend = breadth_pts * 35 / 20
        reasons.append(f"涨跌家数 {today.up}:{today.down}" + (f"，三大指数平均 {avg_index:+.2f}%" if avg_index is not None else ""))

        # 2. 短线情绪：赚钱效应 15 + 涨跌停 12 + 连板高度 10 + 封板质量 8
        if profit_effect is None:
            profit_pts = 7
        else:
            profit_pts = 15 if profit_effect >= 3 else 11 if profit_effect >= 1 else 7 if profit_effect >= -1 else 3 if profit_effect >= -3 else 0
            reasons.append(f"昨日涨停今日平均 {profit_effect:+.2f}%" + (f"，晋级率 {promotion_rate:.0f}%" if promotion_rate is not None else ""))
        if today.limit_down >= 30:
            limit_pts = 0
            reasons.append(f"跌停 {today.limit_down} 家，亏钱效应明显")
        else:
            limit_pts = 12 if today.limit_up >= 80 and today.limit_down <= 10 else 9 if today.limit_up >= 50 else 6 if today.limit_up >= 30 else 3
        height_pts = {0: 0, 1: 0, 2: 3, 3: 6, 4: 8}.get(today.max_height, 10)
        seal_rate = today.first_seal_rate
        seal_pts = 4 if seal_rate is None else 8 if seal_rate >= 0.7 else 5 if seal_rate >= 0.55 else 2
        emotion = profit_pts + limit_pts + height_pts + seal_pts
        reasons.append(f"涨停 {today.limit_up} / 跌停 {today.limit_down}，最高 {today.max_height} 板，连板 {today.multi_board} 家")

        # 3. 量能：较前一交易日成交额变化
        amount_change = None
        if prev and prev.amount_yi > 0 and today.amount_yi > 0:
            amount_change = (today.amount_yi / prev.amount_yi - 1) * 100
            volume = 20 if amount_change >= 10 else 14 if amount_change >= -5 else 8 if amount_change >= -15 else 3
            reasons.append(f"成交额 {today.amount_yi:,.0f} 亿（{amount_change:+.1f}%）")
        else:
            volume = 10

        score = round(trend + emotion + volume, 1)
        if score >= ATTACK_SCORE:
            regime = "进攻"
        elif score >= BALANCED_SCORE:
            regime = "均衡"
        elif score >= FREEZE_SCORE:
            regime = "防守"
        else:
            regime = "冰点"

        return MarketRegime(
            trade_date=today.trade_date,
            regime=regime,
            score=score,
            position_factor=float(self.position_factors.get(regime, 1.0)),
            emotion_cycle=self._emotion_cycle(today, prev, profit_effect),
            metrics={
                "up": today.up, "down": today.down, "breadth": round(breadth, 3), "avg_index_change": avg_index,
                "limit_up": today.limit_up, "limit_down": today.limit_down, "max_height": today.max_height,
                "multi_board": today.multi_board, "first_seal_rate": None if seal_rate is None else round(seal_rate * 100, 1),
                "profit_effect": profit_effect, "promotion_rate": promotion_rate,
                "amount_yi": round(today.amount_yi, 1), "amount_change_pct": None if amount_change is None else round(amount_change, 1),
                "trend_score": round(trend, 1), "emotion_score": emotion, "volume_score": volume,
            },
            reasons=reasons,
        )

    @staticmethod
    def _emotion_cycle(today: DayStats, prev: DayStats | None, profit_effect: float | None) -> str:
        """短线情绪周期（规则判断，只作参考）。"""
        profit = profit_effect if profit_effect is not None else 0.0
        if today.limit_up < 30 or today.limit_down >= 30 or profit <= -4:
            return "冰点"
        if today.limit_up >= 100 and today.max_height >= 5 and profit >= 3:
            return "高潮"
        if profit < 0 and (today.limit_down >= COOLING_LIMIT_DOWN or (prev is not None and today.max_height < prev.max_height)):
            return "退潮"
        if (
            prev is not None
            and profit >= 1
            and today.limit_up >= prev.limit_up * 1.1
            and today.limit_down < COOLING_LIMIT_DOWN
        ):
            return "升温"
        return "震荡"
