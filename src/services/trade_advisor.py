"""
AI 综合研判服务。
对 Top N 选股结果进行 DeepSeek 深度分析，给出买入/卖出/观望判断及操作建议。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from loguru import logger

from src.analyzers.llm_client import LLMClient
from src.config_loader import load_config
from src.database.db import get_db_session
from src.database.models import (
    FinanceNews,
    LimitUpStock,
    StockDaily,
    StockScore,
    TradeSignal,
)

ADVISOR_PROMPT = """你是A股实战短线交易专家，擅长涨停板战法和次日套利分析。
请对以下Top选股标的逐只进行综合研判，给出明确的买卖建议。

**重要规则**：
- 如果两市成交额低于2万亿，整体市场流动性不足，所有标的应降低信心，优先建议"观望"或"回避"
- 如果两市成交额低于1.5万亿，属于极度缩量，应建议全部"回避"，空仓等待放量
- 如果市场情绪为"恐慌"或跌停数>涨停数，应提高回避比例
- 如果市场情绪"亢奋"且板块联动强，可适度提高信心

分析维度：
1. 大盘环境（成交量、涨跌家数、指数趋势、市场情绪）
2. 涨停质量（封板时间、炸板次数、封单强度、连板高度）
3. 板块效应（是否有板块联动、龙头地位、板块涨幅排名）
4. 次日溢价预期（历史同类型票次日表现）
5. 资金面（北向资金方向、个股换手率、流通市值）
6. 风险提示（高位风险、追涨风险、流动性风险、缩量风险）
7. 多源热点综合（财联社红色新闻 + 同花顺热股排行 + 东方财富热门概念 + 韭研公社/雪球讨论焦点）
8. 隔夜美股/国际因子（美股三大指数、VIX恐慌指数、头部企业财报、中概股表现、大宗商品/汇率）对A股传导

返回 JSON 格式：
{
  "market_comment": "对当前大盘环境的一句话点评(30字以内)",
  "stocks": [
    {
      "code": "股票代码",
      "verdict": "买入/观望/回避",
      "confidence": 1到10的整数(10=最有信心),
      "strategy": "具体操作策略(30字以内，如：集合竞价低吸，止损价XX元)",
      "risk": "主要风险(20字以内)",
      "reason": "核心逻辑(30字以内)"
    }
  ]
}
仅返回 JSON，不要其他内容。"""

MIN_CIRC_MV_CONVERT_THRESHOLD = 10000
ADVISOR_TOP_STOCK_LIMIT = 12
LOW_LIQUIDITY_WARNING_THRESHOLD = 2
VIX_PANIC_LEVEL = 30
VIX_WARNING_LEVEL = 25
VIX_ELEVATED_LEVEL = 20


class TradeAdvisor:
    """AI 综合研判。"""

    _last_market_comment: str = ""

    def __init__(self, config: dict | None = None):
        self.config = config or load_config()
        self.llm = LLMClient(self.config.get("llm", {}))
        self.db_path = self.config.get("database", {}).get("sqlite_path", "data/quant.db")

    def advise_top_stocks(self, score_date: str | None = None) -> list[dict]:
        """
        对 score_date 的 Top 选股发起 AI 综合研判。
        返回研判结果列表并写入 TradeSignal.ai_verdict / ai_advice。
        """
        if score_date is None:
            score_date = date.today().strftime("%Y-%m-%d")

        # 1) 获取 Top 评分股
        stocks_info = self._build_stock_context(score_date)
        if not stocks_info:
            logger.info("AI研判: 无评分数据，跳过")
            return []

        # 2) 获取最新新闻上下文
        news_context = self._get_news_context()

        # 3) 获取市场全局概况
        market_context = self._get_market_context()

        # 4) 构建 prompt
        stock_lines = []
        for s in stocks_info:
            line = (
                f"#{s['rank']} {s['name']}({s['code']}) "
                f"综合分={s['score']:.1f} 建议={s['recommendation']} "
                f"连板={s['continuous_days']} 板块={s['sector']} "
                f"涨停原因={s['reason']} "
                f"封板时间={s['first_time']} 炸板={s['open_count']}次 "
                f"涨幅={s['change_pct']}% 换手={s['turnover']}% "
                f"流通市值={s['circ_mv_yi']:.1f}亿 "
                f"舆情分={s['sentiment_score']:.0f} 资金分={s['capital_score']:.0f}"
            )
            stock_lines.append(line)

        # 5) 获取隔夜美股/国际因子上下文
        us_context = self._get_us_market_context()

        user_message = (
            f"今日日期: {score_date}\n\n"
            f"===== 大盘环境 =====\n{market_context}\n\n"
        )
        if us_context:
            user_message += f"===== 隔夜美股/全球市场 =====\n{us_context}\n\n"
        user_message += (
            f"===== 多源热点综合（财联社红色+同花顺+东方财富） =====\n{news_context[:2000]}\n\n"
            f"===== Top{len(stocks_info)}只标的（已剔除一字板） =====\n"
            + "\n".join(stock_lines)
            + "\n\n请先给出一句话大盘点评（market_comment），然后逐只给出买入/观望/回避的判断和操作策略。"
        )

        logger.info(f"AI综合研判: 发送 {len(stocks_info)} 只标的到 DeepSeek...")
        result = self.llm.chat_json(
            user_message=user_message,
            system_message=ADVISOR_PROMPT,
            max_tokens=4096,
        )

        market_comment = result.get("market_comment", "")
        if market_comment:
            logger.info(f"AI大盘点评: {market_comment}")
            # 缓存到类变量供 UI 读取
            TradeAdvisor._last_market_comment = market_comment

        ai_stocks = result.get("stocks", [])
        if not ai_stocks:
            logger.warning("AI研判: DeepSeek 未返回有效结果")
            return []

        # 4) 写入数据库
        verdict_map = {s.get("code"): s for s in ai_stocks}
        saved = 0
        try:
            with get_db_session(self.db_path) as session:
                signals = (
                    session.query(TradeSignal)
                    .filter(TradeSignal.signal_date == score_date)
                    .all()
                )
                for sig in signals:
                    ai = verdict_map.get(sig.code)
                    if ai:
                        verdict = ai.get("verdict", "观望")
                        confidence = ai.get("confidence", 5)
                        strategy = ai.get("strategy", "")
                        risk = ai.get("risk", "")
                        reason = ai.get("reason", "")
                        sig.ai_verdict = verdict
                        sig.ai_advice = f"[信心{confidence}/10] {reason} | 策略:{strategy} | 风险:{risk}"
                        saved += 1

                # 对没有 TradeSignal 的 top 股票也创建记录
                existing_codes = {s.code for s in signals}
                for s_info in stocks_info:
                    code = s_info["code"]
                    if code in existing_codes:
                        continue
                    ai = verdict_map.get(code)
                    if not ai:
                        continue
                    verdict = ai.get("verdict", "观望")
                    confidence = ai.get("confidence", 5)
                    strategy = ai.get("strategy", "")
                    risk = ai.get("risk", "")
                    reason = ai.get("reason", "")
                    new_sig = TradeSignal(
                        code=code,
                        name=s_info["name"],
                        signal_date=score_date,
                        signal_type="buy" if "买" in verdict else "hold",
                        signal_strength=confidence / 10.0,
                        composite_score=s_info["score"],
                        reason=f"综合{s_info['score']:.1f}分 {s_info['reason']}",
                        ai_verdict=verdict,
                        ai_advice=f"[信心{confidence}/10] {reason} | 策略:{strategy} | 风险:{risk}",
                    )
                    session.add(new_sig)
                    saved += 1

        except Exception as e:
            logger.error(f"AI研判结果写入失败: {e}")

        logger.info(f"AI综合研判完成: {saved} 只标的已写入研判结果")
        for s in ai_stocks:
            logger.info(
                f"  [{s.get('verdict')}] {s.get('code')} "
                f"信心={s.get('confidence')}/10 "
                f"{s.get('reason')} | {s.get('strategy')}"
            )
        return ai_stocks

    def _build_stock_context(self, score_date: str) -> list[dict]:
        """构建 Top 股票的多维上下文信息（先剔除一字板，再取 Top 12）。"""
        from src.strategy.scorer import _is_yizi_ban

        stocks = []
        try:
            with get_db_session(self.db_path) as session:
                top_scores = (
                    session.query(StockScore)
                    .filter(StockScore.score_date == score_date)
                    .order_by(StockScore.rank.asc())
                    .limit(30)
                    .all()
                )
                if not top_scores:
                    return []

                codes = [s.code for s in top_scores]

                lu_map = {}
                lu_records = (
                    session.query(LimitUpStock)
                    .filter(
                        LimitUpStock.trade_date == score_date,
                        LimitUpStock.code.in_(codes),
                    )
                    .all()
                )
                for r in lu_records:
                    lu_map[r.code] = r

                sd_map = {}
                sd_records = (
                    session.query(StockDaily)
                    .filter(
                        StockDaily.trade_date == score_date,
                        StockDaily.code.in_(codes),
                    )
                    .all()
                )
                for r in sd_records:
                    sd_map[r.code] = r

                # 剔除一字板后取 Top 12 送入 AI
                rank_counter = 0
                for sc in top_scores:
                    lu = lu_map.get(sc.code)
                    sd = sd_map.get(sc.code)
                    # 一字板过滤
                    if lu and _is_yizi_ban(lu, sd):
                        continue
                    rank_counter += 1
                    circ_mv = (sd.circ_mv or 0) if sd else 0
                    stocks.append({
                        "code": sc.code,
                        "name": sc.name or "",
                        "rank": rank_counter,
                        "score": sc.composite_score or 0,
                        "recommendation": sc.recommendation or "",
                        "sentiment_score": sc.sentiment_score or 50,
                        "capital_score": sc.capital_flow_score or 50,
                        "continuous_days": lu.continuous_days if lu else 0,
                        "sector": lu.sector if lu else "",
                        "reason": lu.reason if lu else "",
                        "first_time": lu.first_limit_time if lu else "",
                        "open_count": lu.open_count if lu else 0,
                        "change_pct": sd.change_pct if sd else 0,
                        "turnover": sd.turnover if sd else 0,
                        "circ_mv_yi": circ_mv / 1e8 if circ_mv > MIN_CIRC_MV_CONVERT_THRESHOLD else circ_mv,
                    })
                    if rank_counter >= ADVISOR_TOP_STOCK_LIMIT:
                        break
        except Exception as e:
            logger.error(f"构建股票上下文失败: {e}")
        return stocks

    def _get_news_context(self) -> str:
        """获取最近的重要新闻摘要作为市场背景（含多源热点）。"""
        sections: list[str] = []
        try:
            with get_db_session(self.db_path) as session:
                cutoff = datetime.now() - timedelta(hours=12)
                seen: set[str] = set()

                # 1) 财联社红色/重要新闻
                cls_news = (
                    session.query(FinanceNews)
                    .filter(
                        FinanceNews.source == "cailianshe",
                        FinanceNews.category.in_(["red", "important"]),
                        FinanceNews.collected_at >= cutoff,
                    )
                    .order_by(FinanceNews.collected_at.desc())
                    .limit(15)
                    .all()
                )
                if cls_news:
                    lines = []
                    for n in cls_news:
                        title = (n.title or "").strip()[:100]
                        if title and title not in seen:
                            seen.add(title)
                            lines.append(f"★ {title}")
                    if lines:
                        sections.append("【财联社红色/重要新闻】\n" + "\n".join(lines))

                # 2) 同花顺热股
                ths_news = (
                    session.query(FinanceNews)
                    .filter(
                        FinanceNews.source == "ths_hot",
                        FinanceNews.collected_at >= cutoff,
                    )
                    .order_by(FinanceNews.collected_at.desc())
                    .limit(10)
                    .all()
                )
                if ths_news:
                    lines = []
                    for n in ths_news:
                        title = (n.title or "").strip()[:100]
                        if title and title not in seen:
                            seen.add(title)
                            lines.append(f"● {title}")
                    if lines:
                        sections.append("【同花顺热股】\n" + "\n".join(lines))

                # 3) 东方财富热门概念/涨停复盘
                em_news = (
                    session.query(FinanceNews)
                    .filter(
                        FinanceNews.source == "eastmoney_hot",
                        FinanceNews.collected_at >= cutoff,
                    )
                    .order_by(FinanceNews.collected_at.desc())
                    .limit(15)
                    .all()
                )
                if em_news:
                    lines = []
                    for n in em_news:
                        title = (n.title or "").strip()[:100]
                        if title and title not in seen:
                            seen.add(title)
                            lines.append(f"● {title}")
                    if lines:
                        sections.append("【东方财富热门概念/涨停复盘】\n" + "\n".join(lines))

        except Exception as e:
            logger.debug(f"获取新闻上下文失败: {e}")
        return "\n\n".join(sections) if sections else "暂无重要快讯"

    def _get_market_context(self) -> str:
        """获取市场全局概况文本（成交额、涨跌家数、指数、板块、情绪）。"""
        try:
            from src.collectors.stock_data import StockDataCollector

            overview = StockDataCollector._market_overview_cache
            if not overview or not overview.get("total_amount_yi"):
                # 缓存为空则实时采集一次
                collector = StockDataCollector(self.config)
                overview = collector.collect_market_overview()

            if not overview or not overview.get("total_amount_yi"):
                return "市场概况数据暂不可用"

            amount_wan_yi = overview.get("total_amount_yi", 0) / 10000
            lines = [
                f"两市总成交额: {amount_wan_yi:.2f}万亿元"
                f"{'（低于2万亿，缩量严重，不宜交易）' if amount_wan_yi < LOW_LIQUIDITY_WARNING_THRESHOLD else ''}",
                f"上涨/下跌/平盘: {overview.get('up_count', 0)}/{overview.get('down_count', 0)}/{overview.get('flat_count', 0)}",
                f"涨停/跌停: {overview.get('limit_up_count', 0)}/{overview.get('limit_down_count', 0)}",
                f"市场情绪: {overview.get('market_emotion', '未知')}",
                f"上证指数: {overview.get('sh_index', '')} ({overview.get('sh_change_pct', 0):+.2f}%)",
                f"深证成指: {overview.get('sz_index', '')} ({overview.get('sz_change_pct', 0):+.2f}%)",
                f"创业板指: {overview.get('cy_index', '')} ({overview.get('cy_change_pct', 0):+.2f}%)",
                f"北向资金净流入: {overview.get('northbound_net_yi', 0):+.2f}亿",
            ]

            top_secs = overview.get("top_sectors", [])
            if top_secs:
                sec_str = ", ".join(f"{s['name']}({s['pct']:+.1f}%)" for s in top_secs[:5])
                lines.append(f"领涨板块: {sec_str}")

            bot_secs = overview.get("bottom_sectors", [])
            if bot_secs:
                sec_str = ", ".join(f"{s['name']}({s['pct']:+.1f}%)" for s in bot_secs[:5])
                lines.append(f"领跌板块: {sec_str}")

            return "\n".join(lines)
        except Exception as e:
            logger.debug(f"获取市场概况失败: {e}")
            return "市场概况数据暂不可用"

    def _get_us_market_context(self) -> str:
        """获取隔夜美股表现和重点个股行情。"""
        parts = []
        try:
            from src.database.models import USMarketDaily, USStockEarnings
            with get_db_session(self.db_path) as session:
                us = session.query(USMarketDaily).order_by(USMarketDaily.trade_date.desc()).first()
                if us:
                    if us.nasdaq_change_pct is not None:
                        parts.append(f"纳斯达克: {us.nasdaq_close or ''} ({us.nasdaq_change_pct:+.2f}%)")
                    if us.sp500_change_pct is not None:
                        parts.append(f"标普500: {us.sp500_close or ''} ({us.sp500_change_pct:+.2f}%)")
                    if us.dow_jones_change_pct is not None:
                        parts.append(f"道琼斯: {us.dow_jones_close or ''} ({us.dow_jones_change_pct:+.2f}%)")
                    if us.vix is not None:
                        if us.vix > VIX_PANIC_LEVEL:
                            level = "恐慌"
                        elif us.vix > VIX_WARNING_LEVEL:
                            level = "警戒"
                        elif us.vix > VIX_ELEVATED_LEVEL:
                            level = "偏高"
                        else:
                            level = "正常"
                        parts.append(f"VIX恐慌指数: {us.vix:.1f} ({level})")
                    if us.china_concept_change_pct is not None:
                        parts.append(f"中概股指数: {us.china_concept_change_pct:+.2f}%")
                    if us.oil_price is not None:
                        parts.append(f"WTI原油: ${us.oil_price:.1f}")
                    if us.gold_price is not None:
                        parts.append(f"黄金: ${us.gold_price:.0f}")

                # 重点美股个股
                from datetime import timedelta
                cutoff = (date.today() - timedelta(days=2)).strftime("%Y-%m-%d")
                earnings = (
                    session.query(USStockEarnings)
                    .filter(USStockEarnings.report_date >= cutoff)
                    .order_by(USStockEarnings.report_date.desc())
                    .limit(20)
                    .all()
                )
                if earnings:
                    seen = set()
                    lines = []
                    for e in earnings:
                        if e.symbol in seen:
                            continue
                        seen.add(e.symbol)
                        if e.after_hours_change_pct is not None:
                            lines.append(f"{e.company_name}({e.symbol}) {e.after_hours_change_pct:+.2f}%")
                    if lines:
                        parts.append("重点个股: " + " | ".join(lines[:10]))
        except Exception as e:
            logger.debug(f"获取美股数据失败: {e}")
        return "\n".join(parts) if parts else ""
