"""
任务调度器 - APScheduler 定时采集和分析
"""

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from loguru import logger


def _run_hot_search_collection(config: dict):
    """执行热搜采集任务"""
    from src.collectors.douyin import DouyinCollector
    from src.collectors.toutiao import ToutiaoCollector
    from src.collectors.weibo import WeiboCollector
    from src.database.db import bulk_insert
    from src.database.models import HotSearch

    collectors = [
        WeiboCollector(config),
        DouyinCollector(config),
        ToutiaoCollector(config),
    ]

    for collector in collectors:
        try:
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
                bulk_insert(records, config.get("database", {}).get("sqlite_path", "data/quant.db"))
        except Exception as e:
            logger.error(f"热搜采集任务异常 [{collector.SOURCE_NAME}]: {e}")


def _run_cailianshe_collection(config: dict):
    """执行财联社采集任务"""
    from src.collectors.cailianshe import CailiansheCollector
    from src.database.db import bulk_insert
    from src.database.models import FinanceNews

    collector = CailiansheCollector(config)
    try:
        items = collector.safe_collect()
        records = [
            FinanceNews(
                source=collector.SOURCE_NAME,
                title=item.get("title", ""),
                content=item.get("content"),
                news_time=item.get("news_time"),
                category=item.get("category", "快讯"),
                tags=item.get("tags"),
                url=item.get("url"),
            )
            for item in items
        ]
        if records:
            bulk_insert(records, config.get("database", {}).get("sqlite_path", "data/quant.db"))
    except Exception as e:
        logger.error(f"财联社采集任务异常: {e}")


def _run_stock_data_collection(config: dict):
    """执行行情数据采集任务"""
    from src.collectors.stock_data import StockDataCollector
    collector = StockDataCollector(config)
    try:
        collector.safe_collect()
    except Exception as e:
        logger.error(f"行情数据采集任务异常: {e}")


def _run_global_data_collection(config: dict):
    """执行国际数据采集任务"""
    from src.collectors.global_news import GlobalNewsCollector
    from src.collectors.us_earnings import USEarningsCollector

    for CollectorClass in [USEarningsCollector, GlobalNewsCollector]:
        try:
            collector = CollectorClass(config)
            collector.safe_collect()
        except Exception as e:
            logger.error(f"国际数据采集异常 [{CollectorClass.__name__}]: {e}")


def _run_daily_analysis(config: dict):
    """每日综合分析 - 收盘后运行"""
    from src.analyzers.global_impact import GlobalImpactAnalyzer
    from src.analyzers.limit_up import LimitUpAnalyzer
    from src.analyzers.sentiment import SentimentAnalyzer
    from src.strategy.scorer import CompositeScorer

    logger.info("===== 开始每日综合分析 =====")

    try:
        # 1. 舆情分析
        sentiment = SentimentAnalyzer(config)
        sentiment.analyze_today()

        # 2. 涨停板分析
        limit_up = LimitUpAnalyzer(config)
        limit_up.analyze_today()

        # 3. 国际因子分析
        global_impact = GlobalImpactAnalyzer(config)
        global_impact.analyze_today()

        # 4. 综合评分
        scorer = CompositeScorer(config)
        top_stocks = scorer.score_today()

        logger.info(f"===== 每日分析完成, Top{len(top_stocks)}选股已生成 =====")
    except Exception as e:
        logger.error(f"每日分析任务异常: {e}")


def _run_signal_generation(config: dict):
    """每日信号生成"""
    from src.strategy.scorer import CompositeScorer

    logger.info("===== 开始生成交易信号 =====")
    try:
        scorer = CompositeScorer(config)
        scorer.generate_signals()
        logger.info("===== 交易信号生成完成 =====")
    except Exception as e:
        logger.error(f"信号生成任务异常: {e}")


def _run_self_learning(config: dict):
    """每日自学习任务。"""
    from src.services.self_learning import SelfLearningService

    logger.info("===== 开始系统自学习 =====")
    try:
        service = SelfLearningService(config)
        result = service.run_daily_learning()
        logger.info(f"===== 系统自学习完成: {result} =====")
    except Exception as e:
        logger.error(f"自学习任务异常: {e}")


def start_scheduler(config: dict):
    """启动任务调度器"""
    sched_cfg = config.get("scheduler", {})
    scheduler = BlockingScheduler()

    # 热搜采集（每30分钟）
    interval = sched_cfg.get("hot_search_interval", 30)
    scheduler.add_job(
        _run_hot_search_collection,
        trigger=IntervalTrigger(minutes=interval),
        args=[config],
        id="hot_search",
        name="热搜数据采集",
    )

    # 财联社采集（每5分钟）
    interval = sched_cfg.get("cailianshe_interval", 5)
    scheduler.add_job(
        _run_cailianshe_collection,
        trigger=IntervalTrigger(minutes=interval),
        args=[config],
        id="cailianshe",
        name="财联社快讯采集",
    )

    # 行情数据采集（每15分钟，交易时间内）
    interval = sched_cfg.get("stock_data_interval", 15)
    scheduler.add_job(
        _run_stock_data_collection,
        trigger=IntervalTrigger(minutes=interval),
        args=[config],
        id="stock_data",
        name="行情数据采集",
    )

    # 国际数据采集（每30分钟）
    global_cfg = config.get("global_data", {})
    interval = global_cfg.get("global_news_interval", 30)
    scheduler.add_job(
        _run_global_data_collection,
        trigger=IntervalTrigger(minutes=interval),
        args=[config],
        id="global_data",
        name="国际数据采集",
    )

    # 每日综合分析（收盘后15:30）
    analysis_time = sched_cfg.get("daily_analysis_time", "15:30")
    hour, minute = analysis_time.split(":")
    scheduler.add_job(
        _run_daily_analysis,
        trigger=CronTrigger(hour=int(hour), minute=int(minute), day_of_week="mon-fri"),
        args=[config],
        id="daily_analysis",
        name="每日综合分析",
    )

    # 每日信号生成（16:00）
    signal_time = sched_cfg.get("daily_signal_time", "16:00")
    hour, minute = signal_time.split(":")
    scheduler.add_job(
        _run_signal_generation,
        trigger=CronTrigger(hour=int(hour), minute=int(minute), day_of_week="mon-fri"),
        args=[config],
        id="signal_generation",
        name="每日信号生成",
    )

    # 每日自学习（16:20）
    learn_time = sched_cfg.get("self_learning_time", "16:20")
    hour, minute = learn_time.split(":")
    scheduler.add_job(
        _run_self_learning,
        trigger=CronTrigger(hour=int(hour), minute=int(minute), day_of_week="mon-fri"),
        args=[config],
        id="self_learning",
        name="每日自学习",
    )

    logger.info(f"调度器已配置 {len(scheduler.get_jobs())} 个任务:")
    for job in scheduler.get_jobs():
        logger.info(f"  - {job.name} ({job.trigger})")

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("调度器已停止")
