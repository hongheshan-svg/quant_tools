"""
市场上下文（参考 daily_stock_analysis 的 market_context / daily_market_context）

把大盘概况、量化的大盘环境、题材与行业主线、降温板块、重要快讯汇总成一份结构化事实，
供 AI 研判、LLM 大盘复盘等提示词统一使用，避免各处各写一套。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from loguru import logger

from src.database.db import get_db_session
from src.database.models import FinanceNews

NEWS_HOURS = 24
NEWS_LIMIT = 10
IMPORTANT_NEWS_CATEGORIES = ("red", "important")


@dataclass
class MarketFacts:
    overview: dict[str, Any] = field(default_factory=dict)
    regime: Any = None                       # MarketRegime
    concept_lines: list[str] = field(default_factory=list)
    industry_lines: list[str] = field(default_factory=list)
    cooling: list[str] = field(default_factory=list)
    news: list[str] = field(default_factory=list)

    def text(self) -> str:
        """交给 LLM 的事实块。"""
        ov = self.overview
        lines = []
        if ov.get("sh_index"):
            lines.append("指数：" + "，".join(
                f"{label} {ov[key]}（{ov.get(pct, 0):+.2f}%）"
                for label, key, pct in (("上证", "sh_index", "sh_change_pct"), ("深证", "sz_index", "sz_change_pct"), ("创业板", "cy_index", "cy_change_pct"))
                if ov.get(key)
            ))
        if ov.get("up_count"):
            lines.append(f"涨跌家数：上涨 {ov['up_count']} / 下跌 {ov.get('down_count', 0)}；涨停 {ov.get('limit_up_count', 0)} / 跌停 {ov.get('limit_down_count', 0)}")
        if ov.get("total_amount_yi"):
            lines.append(f"两市成交额：{ov['total_amount_yi']:,.0f} 亿" + (f"；北向资金 {ov['northbound_net_yi']:+.1f} 亿" if ov.get("northbound_net_yi") else ""))
        sectors = [s.get("name", "") for s in ov.get("top_sectors") or [] if isinstance(s, dict)]
        if any(sectors):
            lines.append("领涨板块：" + "、".join(s for s in sectors if s))
        if self.regime is not None:
            lines.append(self.regime.summary())
            if self.regime.reasons:
                lines.append("环境依据：" + "；".join(self.regime.reasons))
        if self.concept_lines:
            lines.append("题材主线：\n" + "\n".join(f"- {x}" for x in self.concept_lines))
        if self.industry_lines:
            lines.append("行业主线：\n" + "\n".join(f"- {x}" for x in self.industry_lines))
        if self.cooling:
            lines.append("降温/退潮：" + "、".join(self.cooling))
        if self.news:
            lines.append("重要快讯：\n" + "\n".join(f"- {x}" for x in self.news))
        return "\n".join(lines) or "暂无市场数据"


def _overview(config: dict, overview: dict | None) -> dict:
    if overview is not None:
        return overview
    try:
        from src.collectors.stock_data import StockDataCollector

        cached = dict(StockDataCollector._market_overview_cache or {})
        return cached if cached.get("up_count") or cached.get("sh_index") else StockDataCollector(config).collect_market_overview() or {}
    except Exception as e:
        logger.debug(f"获取市场概况失败: {e}")
        return {}


def build_market_facts(config: dict, overview: dict | None = None, news: bool = True) -> MarketFacts:
    """news=False 时不读快讯（调用方已有自己的资讯上下文）。"""
    from src.analyzers.market_regime import MarketRegimeAnalyzer
    from src.analyzers.theme_tracker import ThemeTracker

    facts = MarketFacts(overview=_overview(config, overview))
    try:
        facts.regime = MarketRegimeAnalyzer(config).analyze(overview=facts.overview)
        if facts.regime.regime == "未知":
            facts.regime = None
    except Exception as e:
        logger.debug(f"大盘环境评估失败: {e}")
    try:
        tracker = ThemeTracker(config)
        concepts = tracker.analyze(dimension="concept")
        industries = tracker.analyze(dimension="industry")
        facts.concept_lines = [t.brief() for t in tracker.main_lines(concepts, top=5)]
        facts.industry_lines = [t.brief() for t in tracker.main_lines(industries, top=4)]
        facts.cooling = [t.name for t in sorted(concepts + industries, key=lambda t: -t.heat) if t.phase in ("降温", "退潮")][:6]
    except Exception as e:
        logger.debug(f"主线追踪失败: {e}")
    if not news:
        return facts
    try:
        db_path = config.get("database", {}).get("sqlite_path", "data/quant.db")
        with get_db_session(db_path) as session:
            rows = (
                session.query(FinanceNews.title)
                .filter(FinanceNews.source == "cailianshe", FinanceNews.category.in_(IMPORTANT_NEWS_CATEGORIES),
                        FinanceNews.collected_at >= datetime.now() - timedelta(hours=NEWS_HOURS))
                .order_by(FinanceNews.collected_at.desc()).limit(NEWS_LIMIT).all()
            )
        facts.news = list(dict.fromkeys(t for (t,) in rows if t))
    except Exception as e:
        logger.debug(f"读取重要快讯失败: {e}")
    return facts
