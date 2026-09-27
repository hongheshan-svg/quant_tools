"""
国际事件对A股影响分析器
利用 LLM 分析隔夜美股、财报、国际新闻对 A 股的传导效应
"""

import json
from datetime import date, datetime, timedelta

from loguru import logger

from src.analyzers.llm_client import LLMClient
from src.collectors.us_earnings import USEarningsCollector
from src.config_loader import load_config
from src.database.db import get_db_session
from src.database.models import (
    GlobalImpactAnalysis,
    GlobalNews,
    USMarketDaily,
    USStockEarnings,
)

VIX_PANIC_LEVEL = 30
VIX_WARNING_LEVEL = 25
VIX_ELEVATED_LEVEL = 20
NEWS_IMPORTANCE_HIGHLIGHT = 7

GLOBAL_IMPACT_PROMPT = """你是一位精通全球宏观经济和A股市场的高级分析师，擅长从国际事件中判断对A股的传导效应。

以下是最新的国际市场信息：

{global_data}

美股-A股产业链映射关系参考：
{mapping_table}

**分析重点**：
1. 美股三大指数涨跌→A股开盘情绪传导
2. VIX恐慌指数→风险偏好变化（VIX>25警戒,>30恐慌→A股承压）
3. 中概股指数→互联网/平台经济板块映射
4. 头部美股财报（英伟达/苹果/特斯拉/微软等）→对应A股产业链传导
5. 美联储政策/经济数据→利率敏感型板块
6. 原油/黄金/大宗商品→能源/贵金属/有色板块
7. 人民币汇率→出口/进口企业影响
8. 地缘政治/科技管制→特定板块风险

请综合分析以上信息对今日A股的影响，以 JSON 格式返回：
{{
  "overall_direction": "bullish(利好)/bearish(利空)/neutral(中性)",
  "overall_impact_score": 1到10的整数,
  "key_events": [
    {{
      "event": "事件摘要",
      "impact_direction": "bullish/bearish/neutral",
      "affected_sectors": ["影响的A股板块"],
      "benefited_stocks": ["受益个股代码和名称"],
      "hurt_stocks": ["受损个股代码和名称"],
      "impact_score": 1到10,
      "duration": "短期/中期/长期",
      "reason": "传导逻辑分析"
    }}
  ],
  "summary": "今日国际因素对A股影响的综合总结(100字内)"
}}"""


class GlobalImpactAnalyzer:
    """国际事件对A股影响分析器"""

    def __init__(self, config: dict = None):
        self.config = config or load_config()
        self.llm = LLMClient(self.config.get("llm", {}))
        self.db_path = self.config.get("database", {}).get("sqlite_path", "data/quant.db")

    def analyze_today(self) -> dict:
        """分析今日国际因素对A股的影响"""
        today = date.today().strftime("%Y-%m-%d")
        logger.info(f"开始国际因子影响分析: {today}")

        # 1. 收集国际数据
        global_data = self._gather_global_data(today)
        if not global_data:
            logger.info("暂无国际数据可分析")
            return {}

        # 2. 构建映射表文本
        mapping_text = self._build_mapping_text()

        # 3. LLM 分析
        user_message = GLOBAL_IMPACT_PROMPT.format(
            global_data=global_data,
            mapping_table=mapping_text,
        )

        try:
            result = self.llm.chat_json(
                user_message=user_message,
                system_message="",
            )

            # 4. 保存分析结果
            self._save_analysis(today, result)
            logger.info(f"国际因子分析完成: 总体方向={result.get('overall_direction', 'N/A')}")
            return result

        except Exception as e:
            logger.error(f"国际因子分析失败: {e}")
            return {}

    def _gather_global_data(self, trade_date: str) -> str:
        """汇总国际数据"""
        parts = []

        try:
            with get_db_session(self.db_path) as session:
                # 1. 隔夜美股表现
                us_market = (
                    session.query(USMarketDaily)
                    .order_by(USMarketDaily.trade_date.desc())
                    .first()
                )
                if us_market:
                    parts.append("【隔夜美股表现】")
                    if us_market.dow_jones_change_pct is not None:
                        parts.append(f"道琼斯: {us_market.dow_jones_close or ''} ({us_market.dow_jones_change_pct:+.2f}%)")
                    if us_market.nasdaq_change_pct is not None:
                        parts.append(f"纳斯达克: {us_market.nasdaq_close or ''} ({us_market.nasdaq_change_pct:+.2f}%)")
                    if us_market.sp500_change_pct is not None:
                        parts.append(f"标普500: {us_market.sp500_close or ''} ({us_market.sp500_change_pct:+.2f}%)")
                    if us_market.vix is not None:
                        if us_market.vix > VIX_PANIC_LEVEL:
                            vix_level = "恐慌"
                        elif us_market.vix > VIX_WARNING_LEVEL:
                            vix_level = "警戒"
                        elif us_market.vix > VIX_ELEVATED_LEVEL:
                            vix_level = "偏高"
                        else:
                            vix_level = "正常"
                        parts.append(f"VIX恐慌指数: {us_market.vix:.1f} ({vix_level})")
                    if us_market.china_concept_change_pct is not None:
                        parts.append(f"中概股指数: {us_market.china_concept_index or ''} ({us_market.china_concept_change_pct:+.2f}%)")
                    if us_market.oil_price is not None:
                        parts.append(f"WTI原油: ${us_market.oil_price:.2f}")
                    if us_market.gold_price is not None:
                        parts.append(f"黄金: ${us_market.gold_price:.2f}")
                    parts.append("")

                # 2. 美股重点公司动态（去重保留最新）
                cutoff_date = (date.today() - timedelta(days=3)).strftime("%Y-%m-%d")
                earnings = (
                    session.query(USStockEarnings)
                    .filter(USStockEarnings.report_date >= cutoff_date)
                    .order_by(USStockEarnings.report_date.desc())
                    .limit(40)
                    .all()
                )
                if earnings:
                    parts.append("【美股头部公司动态】")
                    seen_symbols = set()
                    for e in earnings:
                        if e.symbol in seen_symbols:
                            continue
                        seen_symbols.add(e.symbol)
                        line = f"{e.company_name}({e.symbol})"
                        if e.after_hours_change_pct is not None:
                            line += f" 涨跌幅: {e.after_hours_change_pct:+.2f}%"
                        if e.eps_surprise_pct is not None:
                            line += f" EPS超预期: {e.eps_surprise_pct:+.1f}%"
                        if e.guidance:
                            line += f" 指引: {e.guidance[:50]}"
                        parts.append(line)
                    parts.append("")

                # 3. 国际新闻（多源: 财联社/华尔街见闻/金十/东方财富）
                cutoff = datetime.now() - timedelta(hours=24)
                news = (
                    session.query(GlobalNews)
                    .filter(GlobalNews.collected_at >= cutoff)
                    .order_by(GlobalNews.importance.desc())
                    .limit(40)
                    .all()
                )
                if news:
                    parts.append("【国际重大新闻（多源汇总）】")
                    for n in news:
                        src_tag = ""
                        if "wallstreetcn" in (n.source or ""):
                            src_tag = "华尔街见闻"
                        elif "jin10" in (n.source or ""):
                            src_tag = "金十"
                        elif "eastmoney" in (n.source or ""):
                            src_tag = "东财"
                        elif "cailianshe" in (n.source or ""):
                            src_tag = "财联社"
                        else:
                            src_tag = n.source or ""
                        imp_tag = f"★{n.importance}" if n.importance >= NEWS_IMPORTANCE_HIGHLIGHT else ""
                        parts.append(f"[{n.category}][{src_tag}]{imp_tag} {n.title}")
                    parts.append("")

        except Exception as e:
            logger.error(f"汇总国际数据失败: {e}")

        return "\n".join(parts) if parts else ""

    @staticmethod
    def _build_mapping_text() -> str:
        """构建美股-A股映射表文本"""
        mapping = USEarningsCollector.get_mapping()
        lines = []
        for symbol, info in mapping.items():
            sectors = ", ".join(info.get("a_share_sectors", []))
            stocks = ", ".join(info.get("a_share_stocks", []))
            lines.append(f"{info['name']}({symbol}) -> 板块: {sectors} | 个股: {stocks}")
        return "\n".join(lines)

    def _save_analysis(self, analysis_date: str, result: dict):
        """保存分析结果"""
        try:
            with get_db_session(self.db_path) as session:
                existing = session.query(GlobalImpactAnalysis).filter_by(
                    analysis_date=analysis_date
                ).first()

                data = {
                    "overall_direction": result.get("overall_direction", "neutral"),
                    "overall_impact_score": result.get("overall_impact_score", 5),
                    "affected_sectors": json.dumps(
                        [e.get("affected_sectors", []) for e in result.get("key_events", [])],
                        ensure_ascii=False,
                    ),
                    "benefited_stocks": json.dumps(
                        [e.get("benefited_stocks", []) for e in result.get("key_events", [])],
                        ensure_ascii=False,
                    ),
                    "hurt_stocks": json.dumps(
                        [e.get("hurt_stocks", []) for e in result.get("key_events", [])],
                        ensure_ascii=False,
                    ),
                    "analysis_detail": json.dumps(result, ensure_ascii=False),
                }

                if existing:
                    for k, v in data.items():
                        setattr(existing, k, v)
                else:
                    record = GlobalImpactAnalysis(
                        analysis_date=analysis_date,
                        **data,
                    )
                    session.add(record)

        except Exception as e:
            logger.error(f"国际因子分析结果保存失败: {e}")

    def get_latest_impact(self) -> dict | None:
        """获取最新的国际因子分析结果"""
        try:
            with get_db_session(self.db_path) as session:
                record = (
                    session.query(GlobalImpactAnalysis)
                    .order_by(GlobalImpactAnalysis.analysis_date.desc())
                    .first()
                )
                if record and record.analysis_detail:
                    return json.loads(record.analysis_detail)
        except Exception as e:
            logger.error(f"获取国际因子分析结果失败: {e}")
        return None
