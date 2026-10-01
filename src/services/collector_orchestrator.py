"""
多线程采集编排器。
「采集数据」并发采集新闻、行情、国际新闻和美股数据，缺失的数据源分组重试。
"""

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from typing import Any

from loguru import logger

from src.collectors.fund_flow import collect_fund_flow
from src.collectors.source_chain import source_health
from src.config_loader import load_config
from src.database.db import get_db_session, init_db
from src.database.models import FinanceNews, HotSearch
from src.services.realtime_news_ai import RealtimeNewsAIProcessor


class CollectorOrchestrator:
    """并发采集入口。"""

    def __init__(self, config: dict | None = None):
        self.config = config or load_config()
        self.db_path = self.config.get("database", {}).get("sqlite_path", "data/quant.db")
        desktop_cfg = self.config.get("desktop", {})
        self.news_workers = int(desktop_cfg.get("news_workers", 3))
        self.market_workers = int(desktop_cfg.get("market_workers", 4))
        self.enable_realtime_ai = bool(desktop_cfg.get("enable_realtime_ai", True))
        self.collect_max_attempts = max(1, int(desktop_cfg.get("collect_max_attempts", 3)))
        self.required_news_sources = (
            "cailianshe",
            "xueqiu",
            "jiuyan",
            "hot_topics",
            "weibo",
            "douyin",
            "toutiao",
        )
        init_db(self.db_path)
        self.realtime_ai = RealtimeNewsAIProcessor(self.config)

    def collect_news_parallel(self) -> dict[str, int]:
        """并发采集新闻与热搜数据并写库。"""
        logger.info("并发采集新闻数据...")
        results = {
            "cailianshe": 0,
            "xueqiu": 0,
            "jiuyan": 0,
            "hot_topics": 0,
            "weibo": 0,
            "douyin": 0,
            "toutiao": 0,
            "rss": 0,
        }
        tasks = {
            "cailianshe": self._collect_cailianshe,
            "xueqiu": self._collect_xueqiu,
            "jiuyan": self._collect_jiuyan,
            "hot_topics": self._collect_hot_topics,
            "weibo": self._collect_weibo_hot_search,
            "douyin": self._collect_douyin_hot_search,
            "toutiao": self._collect_toutiao_hot_search,
            "rss": self.collect_rss,  # 可选组，不在 required_news_sources 里
        }
        with ThreadPoolExecutor(max_workers=max(self.news_workers, 8), thread_name_prefix="news") as executor:
            future_map = {executor.submit(func): name for name, func in tasks.items()}
            for future in as_completed(future_map):
                name = future_map[future]
                try:
                    results[name] = int(future.result())
                    if name != "rss":  # RSS 新增 0 条属正常（去重），健康记录由采集器按源记录
                        source_health.record("资讯采集", name, results[name] > 0)
                except Exception as e:
                    logger.error(f"[{name}] 采集异常: {e}")
                    results[name] = 0
                    source_health.record("资讯采集", name, False, str(e))
        logger.info(f"新闻并发采集完成: {results}")
        return results

    def collect_rss(self) -> int:
        """采集 RSS 资讯源并写库（非必需组：未配置或未启用时直接返回 0，失败只记日志）。"""
        cfg = self.config.get("intelligence") or {}
        if not cfg.get("enabled", True):
            return 0
        from src.collectors.rss import RSSCollector, _enabled_sources, save_items

        if not _enabled_sources(self.config):
            return 0
        collector = RSSCollector(self.config)
        try:
            added = save_items(collector.safe_collect(), self.db_path, int(cfg.get("keep_days", 7)))
            return added
        except Exception as e:
            logger.error(f"[rss] 采集异常: {e}")
            return 0
        finally:
            collector.close()

    def collect_market_parallel(self) -> dict[str, Any]:
        """
        并发采集行情相关数据。
        Group-B: realtime/limitup/dragon_tiger/northbound 并发执行。
        """
        from src import trading_calendar

        if not trading_calendar.market_data_ready():
            logger.info("非交易日或未到 9:25，跳过当天行情采集（接口此时返回的是上一个交易日的数据）")
            result = {k: "skipped" for k in ("realtime_quotes", "limit_up_pool", "dragon_tiger", "northbound_flow", "fund_flow")}
            try:
                from src.collectors.stock_data import StockDataCollector

                # 最近一个交易日缺行情时（如新装后遇到节假日）按该交易日补齐，否则各页面都没有数据
                result["last_session"] = StockDataCollector(self.config).fill_last_session(self.db_path)
            except Exception as e:
                logger.warning(f"补齐最近交易日行情异常: {e}")
            return result
        logger.info("并发采集行情数据...")
        try:
            from src.collectors.stock_data import StockDataCollector
        except Exception as e:
            logger.error(f"加载行情采集器失败: {e}")
            return {
                "realtime_quotes": f"error: {e}",
                "limit_up_pool": f"error: {e}",
                "dragon_tiger": f"error: {e}",
                "northbound_flow": f"error: {e}",
                "fund_flow": f"error: {e}",
            }

        collector = StockDataCollector(self.config)
        today = date.today().strftime("%Y-%m-%d")
        tasks = {
            "realtime_quotes": lambda: collector._collect_realtime_quotes(today, self.db_path),
            "limit_up_pool": lambda: collector._collect_limit_up_pool(today, self.db_path),
            "dragon_tiger": lambda: collector._collect_dragon_tiger(today, self.db_path),
            "northbound_flow": lambda: collector._collect_northbound_flow(today, self.db_path),
            "fund_flow": lambda: collect_fund_flow(today, self.db_path),
        }
        result = {"realtime_quotes": "ok", "limit_up_pool": "ok", "dragon_tiger": "ok", "northbound_flow": "ok", "fund_flow": "ok"}
        with ThreadPoolExecutor(max_workers=self.market_workers, thread_name_prefix="market") as executor:
            future_map = {executor.submit(func): name for name, func in tasks.items()}
            for future in as_completed(future_map):
                name = future_map[future]
                try:
                    future.result()
                except Exception as e:
                    logger.error(f"[{name}] 采集异常: {e}")
                    result[name] = f"error: {e}"
        logger.info(f"行情并发采集完成: {result}")
        return result

    def collect_market_overview(self) -> dict[str, Any]:
        """采集市场全局概况（成交额、涨跌家数、板块等）。"""
        try:
            from src.collectors.stock_data import StockDataCollector
            collector = StockDataCollector(self.config)
            return collector.collect_market_overview()
        except Exception as e:
            logger.error(f"市场概况采集失败: {e}")
            return {}

    def _collect_market_and_overview(self) -> tuple[dict[str, Any], dict[str, Any]]:
        """先采行情再算市场概况：概况的涨跌家数、成交额从刚写入的行情统计，并发执行会读到旧数据"""
        market = self.collect_market_parallel()
        return market, self.collect_market_overview()

    def collect_all(self) -> dict[str, Any]:
        """一次执行新闻+行情+市场概况+国际+美股并发采集，并强校验全源稳定。"""
        result: dict[str, Any] = {
            "news": {},
            "market": {},
            "overview": {},
            "global_news": 0,
            "us_earnings": 0,
        }

        # 首轮：全量并发采集
        with ThreadPoolExecutor(max_workers=4, thread_name_prefix="collect-all") as executor:
            fut_news = executor.submit(self.collect_news_parallel)
            fut_market = executor.submit(self._collect_market_and_overview)
            fut_global = executor.submit(self._collect_global_news)
            fut_us = executor.submit(self._collect_us_earnings)
            result["news"] = fut_news.result()
            result["market"], result["overview"] = fut_market.result()
            result["global_news"] = fut_global.result()
            result["us_earnings"] = fut_us.result()

        missing = self._check_missing_sources(result)
        attempt = 1

        # 后续：仅重试缺失分组
        while missing and attempt < self.collect_max_attempts:
            attempt += 1
            retry_groups = {m.split(".", 1)[0] for m in missing}
            logger.warning(f"全源校验未通过，开始第{attempt}轮重试: {sorted(missing)}")
            with ThreadPoolExecutor(max_workers=max(1, len(retry_groups)), thread_name_prefix="collect-retry") as executor:
                futs = {}
                if "news" in retry_groups:
                    futs["news"] = executor.submit(self.collect_news_parallel)
                if "market" in retry_groups:
                    futs["market"] = executor.submit(self._collect_market_and_overview)
                if "global_news" in retry_groups:
                    futs["global_news"] = executor.submit(self._collect_global_news)
                if "us_earnings" in retry_groups:
                    futs["us_earnings"] = executor.submit(self._collect_us_earnings)

                for key, fut in futs.items():
                    try:
                        if key == "market":
                            result["market"], result["overview"] = fut.result()
                        else:
                            result[key] = fut.result()
                    except Exception as e:
                        logger.error(f"重试分组失败 [{key}]: {e}")
            missing = self._check_missing_sources(result)
            if missing:
                time.sleep(0.6)

        result["all_sources_ok"] = len(missing) == 0
        result["missing_sources"] = missing
        result["collect_attempts"] = attempt
        if missing:
            logger.error(f"全源稳定校验失败: {missing}")
        else:
            logger.info(f"全源稳定校验通过，共尝试 {attempt} 轮")
        return result

    def _check_missing_sources(self, result: dict[str, Any]) -> list[str]:
        """校验必须源是否采集成功。"""
        missing: list[str] = []

        news = result.get("news", {}) or {}
        missing.extend(
            f"news.{source}"
            for source in self.required_news_sources
            if int(news.get(source, 0) or 0) <= 0
        )

        market = result.get("market", {}) or {}
        for key in ("realtime_quotes", "limit_up_pool", "dragon_tiger", "northbound_flow"):
            value = str(market.get(key, ""))
            if value.startswith("error"):
                missing.append(f"market.{key}")

        if int(result.get("global_news", 0) or 0) <= 0:
            missing.append("global_news")

        if int(result.get("us_earnings", 0) or 0) <= 0:
            missing.append("us_earnings")

        return missing

    def _collect_cailianshe(self) -> int:
        from src.collectors.cailianshe import CailiansheCollector

        collector = CailiansheCollector(self.config)
        items = collector.collect()

        # 1) 先入库，确保 FinanceNews 记录存在
        records = []
        for item in items:
            # 合并 tags：原始标签 + 关联股票 + 关联板块
            tag_parts = []
            raw_tags = item.get("tags", "")
            if raw_tags:
                tag_parts.append(raw_tags)
            stocks = item.get("stocks", [])
            if stocks:
                stock_names = [s.get("name", "") for s in stocks if s.get("name")]
                if stock_names:
                    tag_parts.append("个股:" + ",".join(stock_names[:5]))
            plates = item.get("plates", [])
            if plates:
                tag_parts.append("板块:" + ",".join(plates[:3]))
            combined_tags = " | ".join(filter(None, tag_parts))

            records.append(
                FinanceNews(
                    source="cailianshe",
                    title=item.get("title", ""),
                    content=item.get("content", ""),
                    news_time=item.get("news_time"),
                    category=item.get("importance", "normal"),
                    tags=combined_tags,
                    url=item.get("url", ""),
                )
            )
        if records:
            with get_db_session(self.db_path) as session:
                session.add_all(records)

        # 2) 入库后再送 AI（红色/重要快讯），AI 分析结果会回写到 FinanceNews.tags
        if self.enable_realtime_ai:
            try:
                ai_count = self.realtime_ai.process_cailianshe_stream(items)
                if ai_count:
                    logger.info(f"财联社实时AI: {ai_count} 条重要快讯已分析并回标")
            except Exception as e:
                logger.error(f"财联社实时AI处理失败: {e}")

        return len(records)

    def _collect_xueqiu(self) -> int:
        from src.collectors.xueqiu import XueqiuCollector

        collector = XueqiuCollector(self.config)
        items = collector.collect()
        records = [
            FinanceNews(
                source="xueqiu",
                title=item.get("title", ""),
                content=item.get("content", ""),
                category=item.get("category", ""),
                url=item.get("url", ""),
                tags=item.get("stock_code", ""),
            )
            for item in items
        ]
        if records:
            with get_db_session(self.db_path) as session:
                session.add_all(records)
        return len(records)

    def _collect_jiuyan(self) -> int:
        from src.collectors.jiuyan import JiuyanCollector

        collector = JiuyanCollector(self.config)
        items = collector.collect()
        records = []
        for item in items:
            # 把 category 和 stock_codes 合并作为标签
            cat = item.get("category", "")
            stock_codes = item.get("stock_code", "") or item.get("stock_codes", "")
            tag_parts = list(filter(None, [cat, stock_codes]))
            records.append(
                FinanceNews(
                    source="jiuyan",
                    title=item.get("title", ""),
                    content=item.get("content", ""),
                    category=item.get("category", ""),
                    url=item.get("url", ""),
                    tags=" | ".join(tag_parts) if tag_parts else "",
                )
            )
        if records:
            with get_db_session(self.db_path) as session:
                session.add_all(records)
        return len(records)

    def _collect_hot_topics(self) -> int:
        """采集同花顺热股 + 东方财富热门概念/涨停复盘。"""
        try:
            from src.collectors.hot_topics import HotTopicCollector
            collector = HotTopicCollector(self.config)
            items = collector.collect()
            records = [
                FinanceNews(
                    source=item.get("source", "hot_topics"),
                    title=item.get("title", ""),
                    content=item.get("content", ""),
                    category=item.get("category", ""),
                    url=item.get("url", ""),
                    tags=item.get("tags", ""),
                )
                for item in items
            ]
            if records:
                with get_db_session(self.db_path) as session:
                    session.add_all(records)
            return len(records)
        except Exception as e:
            logger.error(f"热点采集异常: {e}")
            return 0

    def _collect_hot_search_source(self, collector_cls) -> int:
        """采集单个热搜源并写入 hot_search 表。"""
        collector = collector_cls(self.config)
        items = collector.safe_collect()
        records = [
            HotSearch(
                source=collector.SOURCE_NAME,
                title=item.get("title", ""),
                rank=item.get("rank"),
                hot_value=item.get("hot_value"),
                category=item.get("category"),
                url=item.get("url"),
            )
            for item in items
        ]
        if records:
            with get_db_session(self.db_path) as session:
                session.add_all(records)
        return len(records)

    def _collect_weibo_hot_search(self) -> int:
        from src.collectors.weibo import WeiboCollector
        return self._collect_hot_search_source(WeiboCollector)

    def _collect_douyin_hot_search(self) -> int:
        from src.collectors.douyin import DouyinCollector
        return self._collect_hot_search_source(DouyinCollector)

    def _collect_toutiao_hot_search(self) -> int:
        from src.collectors.toutiao import ToutiaoCollector
        return self._collect_hot_search_source(ToutiaoCollector)

    def _collect_global_news(self) -> int:
        """采集国际新闻（财联社国际/华尔街见闻/金十数据/东方财富全球 + 大宗商品/VIX/汇率）。"""
        try:
            from src.collectors.global_news import GlobalNewsCollector
            collector = GlobalNewsCollector(self.config)
            items = collector.collect()
            got_sources = {str(x.get("source", "")) for x in (items or []) if isinstance(x, dict)}
            required_sources = {"cailianshe_global", "wallstreetcn", "jin10", "eastmoney_global"}
            missing = sorted(required_sources - got_sources)
            for source in sorted(required_sources):
                source_health.record("国际新闻", source, source in got_sources)
            if missing:
                logger.warning(f"国际新闻源不完整，缺失: {missing}")
                return 0
            logger.info(f"国际新闻多源采集完成: {len(items)} 条，sources={sorted(got_sources)}")
            return len(items)
        except Exception as e:
            logger.error(f"国际新闻采集异常: {e}")
            return 0

    def _collect_us_earnings(self) -> int:
        """采集美股头部企业行情/财报/VIX/中概股指数。"""
        try:
            from src.collectors.us_earnings import USEarningsCollector
            collector = USEarningsCollector(self.config)
            collector.collect()
            metrics = getattr(collector, "last_run_metrics", {}) or {}
            for key, value in metrics.items():
                source_health.record("美股数据", key, int(value or 0) > 0)

            market_ok = int(metrics.get("us_market_daily", 0)) > 0
            stocks_ok = int(metrics.get("us_stock_earnings", 0)) > 0
            hot_estimate_ok = (
                int(metrics.get("us_hot_24h", 0)) > 0
                and int(metrics.get("us_estimate", 0)) > 0
            )
            news_ok = int(metrics.get("us_earnings_news", 0)) > 0

            if not (market_ok and stocks_ok and hot_estimate_ok and news_ok):
                logger.warning(f"美股数据分项未达标: {metrics}")
                return 0

            total = sum(int(v or 0) for v in metrics.values())
            logger.info(f"美股数据采集达标: {metrics}")
            return total
        except Exception as e:
            logger.error(f"美股数据采集异常: {e}")
            return 0

