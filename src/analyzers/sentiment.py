"""
舆情情绪分析器
利用 LLM 分析热搜和新闻对 A 股个股的影响
重点：针对涨停股进行定向情绪分析
"""

import threading
from datetime import date, datetime, timedelta

from loguru import logger

from src.analyzers.llm_client import LLMClient
from src.config_loader import load_config
from src.database.db import get_db_session
from src.database.models import FinanceNews, HotSearch, LimitUpStock, SentimentAnalysis

NEUTRAL_IMPACT_MAX_SCORE = 2

SYSTEM_PROMPT = """你是一位资深A股分析师，擅长从社会热点和财经新闻中挖掘投资机会。

你的任务是分析以下热搜/新闻标题，找出可能影响A股市场的信息。

请以 JSON 格式返回分析结果，格式如下：
{
  "items": [
    {
      "original_text": "原始标题",
      "related_stock_code": "股票代码(如600000)，无关则为null",
      "related_stock_name": "股票名称，无关则为null",
      "related_sector": "关联板块名称，无关则为null",
      "sentiment": "bullish(利好)/bearish(利空)/neutral(中性/无关)",
      "impact_score": 1到10的整数(1=几乎无影响, 10=重大影响),
      "duration_estimate": "短期(1-3天)/中期(1-2周)/长期(1月以上)",
      "analysis_reason": "简要分析理由(30字以内)"
    }
  ]
}

注意：
1. 只关注与股市、经济、产业相关的内容，娱乐八卦等标记为 neutral，impact_score 为 1
2. 尽量给出具体的股票代码，如果只能关联到板块也可以
3. 同一个事件可以关联多只股票，请分别列出
4. 评分要客观，不要夸大影响"""


LIMITUP_ANALYSIS_PROMPT = """你是一位资深A股短线交易分析师，擅长分析涨停股的驱动因素和次日走势。

今日涨停股列表如下：
{limit_up_stocks}

今日市场上的主要新闻和信息：
{news_context}

请对每只涨停股进行分析，判断其涨停驱动力和次日预期，以 JSON 格式返回：
{{
  "items": [
    {{
      "related_stock_code": "股票代码(如600000)",
      "related_stock_name": "股票名称",
      "related_sector": "所属板块/概念",
      "sentiment": "bullish(看好次日继续涨停或大涨)/bearish(看空次日)/neutral(不确定)",
      "impact_score": 1到10的整数(次日继续涨停的可能性，10=非常可能),
      "duration_estimate": "短期(1-3天)/中期(1-2周)/长期(1月以上)",
      "analysis_reason": "50字以内的分析理由，包含驱动因素"
    }}
  ]
}}

分析要点：
1. 连板股重点看题材热度和市场合力，评估接力意愿
2. 首板股重点看涨停原因是否为当前市场主线
3. 早盘涨停 > 尾盘涨停（封板时间越早越强）
4. 封单大且未开板 > 多次开板（封单强度）
5. 同板块多只涨停 = 板块效应强
6. 结合新闻和消息面判断题材能否持续发酵
7. 请务必为每只涨停股都给出分析，不要遗漏"""


class SentimentAnalyzer:
    """舆情情绪分析器"""

    def __init__(self, config: dict = None):
        self.config = config or load_config()
        self.llm = LLMClient(self.config.get("llm", {}))
        self.db_path = self.config.get("database", {}).get("sqlite_path", "data/quant.db")
        self.analysis_workers = int(self.config.get("llm", {}).get("analysis_workers", 3))
        self._news_context_cache: dict[str, str] = {}
        self._news_context_cache_at: dict[str, datetime] = {}
        self._cache_lock = threading.Lock()

    def analyze_today(self):
        """分析今日所有采集到的舆情数据"""
        logger.info("开始今日舆情分析...")

        # 1. 获取待分析的热搜数据
        hot_searches = self._get_unanalyzed_hot_searches()
        if hot_searches:
            self._analyze_batch(hot_searches, "hot_search")

        # 2. 获取待分析的财经新闻
        finance_news = self._get_unanalyzed_news()
        if finance_news:
            self._analyze_batch(finance_news, "finance_news")

        # 3. 针对涨停股进行定向分析（最关键）
        self.analyze_limit_up_stocks()

        logger.info("今日舆情分析完成")

    def analyze_limit_up_stocks(self):
        """
        针对今日涨停股进行定向情绪分析
        将涨停股列表 + 今日新闻 一起发送给 LLM 进行综合分析
        """
        today = date.today().strftime("%Y-%m-%d")
        logger.info("开始涨停股定向情绪分析...")

        # 获取今日涨停股
        limit_up_stocks = []
        try:
            with get_db_session(self.db_path) as session:
                records = (
                    session.query(LimitUpStock)
                    .filter(LimitUpStock.trade_date == today)
                    .order_by(LimitUpStock.continuous_days.desc())
                    .all()
                )
                for r in records:
                    name = r.name or ""
                    if "ST" in name:
                        continue
                    limit_up_stocks.append({
                        "code": r.code,
                        "name": name,
                        "continuous_days": r.continuous_days or 1,
                        "sector": r.sector or "",
                        "reason": r.reason or "",
                        "seal_amount": r.seal_amount,
                        "first_limit_time": r.first_limit_time or "",
                        "open_count": r.open_count or 0,
                    })
        except Exception as e:
            logger.error(f"获取涨停股失败: {e}")
            return

        if not limit_up_stocks:
            logger.info("无涨停股数据，跳过定向分析")
            return

        # 获取今日新闻上下文（财联社 + 韭研公社）
        news_context = self._get_news_context()

        # 构建涨停股文本
        stock_lines = []
        for s in limit_up_stocks:
            seal = f"{s['seal_amount']/1e8:.1f}亿" if s.get('seal_amount') else "N/A"
            board_label = f"{s['continuous_days']}连板" if s["continuous_days"] > 1 else "首板"
            line = (
                f"- {s['name']}({s['code']}) "
                f"{board_label} "
                f"板块:{s['sector']} 封单:{seal} "
                f"首封:{s['first_limit_time']} 开板:{s['open_count']}次"
            )
            if s['reason'] and s['reason'] != s['sector']:
                line += f" 原因:{s['reason']}"
            stock_lines.append(line)

        # 分批分析（每批最多15只，避免超过 token 限制）
        batch_size = 15
        jobs = []
        for i in range(0, len(limit_up_stocks), batch_size):
            batch_stocks = limit_up_stocks[i:i + batch_size]
            batch_lines = stock_lines[i:i + batch_size]
            prompt = LIMITUP_ANALYSIS_PROMPT.format(
                limit_up_stocks="\n".join(batch_lines),
                news_context=news_context[:2000],
            )
            jobs.append(
                {
                    "user_message": f"请分析以下{len(batch_stocks)}只涨停股的次日走势预期：",
                    "system_message": prompt,
                    "max_tokens": 8192,
                    "meta": {"batch": i // batch_size + 1},
                }
            )

        total_analyzed = 0
        for output in self.llm.chat_json_batch(jobs=jobs, max_workers=self.analysis_workers):
            meta = output.get("meta", {})
            payload = output.get("payload", {})
            analysis_items = payload.get("items", [])
            total_analyzed += len(analysis_items)
            logger.info(
                f"涨停股分析批次 {meta.get('batch', '?')}: "
                f"{len(analysis_items)} 条结果 (累计 {total_analyzed})"
            )
            self._save_analysis(analysis_items, "limit_up_analysis", [])

        logger.info(f"涨停股定向分析完成: 共 {total_analyzed} 条分析结果")

    def _get_news_context(self) -> str:
        """获取今日新闻上下文文本"""
        today = date.today().strftime("%Y-%m-%d")
        with self._cache_lock:
            ts = self._news_context_cache_at.get(today)
            if ts and datetime.now() - ts < timedelta(minutes=10):
                return self._news_context_cache.get(today, "暂无新闻数据")

        lines = []
        try:
            with get_db_session(self.db_path) as session:
                cutoff = datetime.now() - timedelta(hours=24)

                # 财联社重要新闻
                cls_news = (
                    session.query(FinanceNews)
                    .filter(
                        FinanceNews.source == "cailianshe",
                        FinanceNews.collected_at >= cutoff,
                        FinanceNews.category.in_(["red", "important"]),
                    )
                    .limit(20)
                    .all()
                )
                lines.extend(f"[财联社重要] {r.title}" for r in cls_news)

                # 韭研公社
                jy_news = (
                    session.query(FinanceNews)
                    .filter(
                        FinanceNews.source == "jiuyan",
                        FinanceNews.collected_at >= cutoff,
                    )
                    .limit(20)
                    .all()
                )
                lines.extend(
                    f"[韭研-{r.category or ''}] {(r.title[:200] if r.title else '')}"
                    for r in jy_news
                )

                # 财联社普通快讯
                cls_normal = (
                    session.query(FinanceNews)
                    .filter(
                        FinanceNews.source == "cailianshe",
                        FinanceNews.collected_at >= cutoff,
                        FinanceNews.category == "normal",
                    )
                    .limit(20)
                    .all()
                )
                lines.extend(f"[财联社] {r.title}" for r in cls_normal)

        except Exception as e:
            logger.error(f"获取新闻上下文失败: {e}")
        text = "\n".join(lines) if lines else "暂无新闻数据"
        with self._cache_lock:
            self._news_context_cache[today] = text
            self._news_context_cache_at[today] = datetime.now()
        return text

    def _get_unanalyzed_hot_searches(self) -> list[dict]:
        """获取尚未分析的热搜数据"""
        items = []
        try:
            with get_db_session(self.db_path) as session:
                # 获取最近24小时的热搜
                cutoff = datetime.now() - timedelta(hours=24)
                records = (
                    session.query(HotSearch)
                    .filter(HotSearch.collected_at >= cutoff)
                    .order_by(HotSearch.hot_value.desc())
                    .limit(100)
                    .all()
                )
                # 去重
                seen = set()
                for r in records:
                    if r.title not in seen:
                        seen.add(r.title)
                        items.append({
                            "id": r.id,
                            "text": r.title,
                            "source": r.source,
                        })
        except Exception as e:
            logger.error(f"获取热搜数据失败: {e}")
        return items

    def _get_unanalyzed_news(self) -> list[dict]:
        """获取尚未分析的财经新闻"""
        items = []
        try:
            with get_db_session(self.db_path) as session:
                cutoff = datetime.now() - timedelta(hours=24)
                records = (
                    session.query(FinanceNews)
                    .filter(FinanceNews.collected_at >= cutoff)
                    .order_by(FinanceNews.id.desc())
                    .limit(100)
                    .all()
                )
                seen = set()
                for r in records:
                    if r.title not in seen:
                        seen.add(r.title)
                        items.append({
                            "id": r.id,
                            "text": r.title,
                            "source": r.source,
                        })
        except Exception as e:
            logger.error(f"获取财经新闻失败: {e}")
        return items

    def _analyze_batch(self, items: list[dict], source_type: str):
        """批量分析一组文本"""
        # 分批处理，每批15条（避免超过 token 限制导致 JSON 截断）
        batch_size = 15
        jobs = []
        batch_map: dict[int, list[dict]] = {}
        for i in range(0, len(items), batch_size):
            batch_idx = i // batch_size + 1
            batch = items[i:i + batch_size]
            texts = [item["text"] for item in batch]
            user_message = "请分析以下热搜/新闻标题对A股的影响：\n\n"
            for idx, text in enumerate(texts, 1):
                user_message += f"{idx}. {text}\n"
            batch_map[batch_idx] = batch
            jobs.append(
                {
                    "user_message": user_message,
                    "system_message": SYSTEM_PROMPT,
                    "max_tokens": 4096,
                    "meta": {"batch": batch_idx, "source_type": source_type},
                }
            )

        for output in self.llm.chat_json_batch(jobs=jobs, max_workers=self.analysis_workers):
            meta = output.get("meta", {})
            payload = output.get("payload", {})
            batch_idx = int(meta.get("batch", 0))
            analysis_items = payload.get("items", [])
            logger.info(
                f"{source_type} 分析批次 {batch_idx}: {len(analysis_items)} 条"
            )
            self._save_analysis(analysis_items, source_type, batch_map.get(batch_idx, []))

    def _save_analysis(self, analysis_items: list[dict], source_type: str, batch: list[dict]):
        """保存分析结果到数据库"""
        try:
            with get_db_session(self.db_path) as session:
                for item in analysis_items:
                    # 过滤无关内容
                    if item.get("sentiment") == "neutral" and item.get("impact_score", 0) <= NEUTRAL_IMPACT_MAX_SCORE:
                        continue

                    record = SentimentAnalysis(
                        source_type=source_type,
                        original_text=item.get("original_text", ""),
                        related_stock_code=item.get("related_stock_code"),
                        related_stock_name=item.get("related_stock_name"),
                        related_sector=item.get("related_sector"),
                        sentiment=item.get("sentiment", "neutral"),
                        impact_score=item.get("impact_score", 1),
                        duration_estimate=item.get("duration_estimate"),
                        analysis_reason=item.get("analysis_reason"),
                    )
                    session.add(record)

            logger.info(f"舆情分析结果已保存: {len(analysis_items)} 条")
        except Exception as e:
            logger.error(f"舆情分析结果保存失败: {e}")
