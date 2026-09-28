"""
任务调度器 - APScheduler 定时采集和分析
"""

from datetime import date, datetime, timedelta

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from loguru import logger


def _skip_non_trade_day(config: dict, job_name: str) -> bool:
    """非交易日（周末、法定节假日）跳过行情和分析类任务。"""
    from src import trading_calendar

    db_path = config.get("database", {}).get("sqlite_path", "data/quant.db")
    trading_calendar.load(db_path)
    if trading_calendar.is_trade_day():
        return False
    logger.info(f"今日非交易日，跳过: {job_name}")
    return True


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
    if _skip_non_trade_day(config, "行情数据采集"):
        return
    from src.collectors.stock_data import StockDataCollector
    collector = StockDataCollector(config)
    try:
        collector.safe_collect()
    except Exception as e:
        logger.error(f"行情数据采集任务异常: {e}")
        return

    # 行情更新后检查模拟盘持仓的止损止盈
    try:
        from src.trading.execution_service import ExecutionService

        execution = ExecutionService(config)
        if execution.enabled:
            execution.generate_exit_orders()
    except Exception as e:
        logger.error(f"止损止盈检查异常: {e}")

    # 盘中提醒（封板/炸板/跌破止损/大跌/自定义规则）
    try:
        from src.services.alert_service import AlertService

        AlertService(config).run()
    except Exception as e:
        logger.error(f"盘中提醒检查异常: {e}")


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
    if _skip_non_trade_day(config, "每日综合分析"):
        return
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

    # 5. 全市场策略选股（结果供 AI 涨停预测参考，并统计各策略的次日表现）
    screening = config.get("screening", {})
    if screening.get("enabled", True):
        from src.strategy.screener import StrategyScreener

        # 距上次历史回测超过 backtest_interval_days 天时先回测，更新选股排序用的策略权重
        interval = int(screening.get("backtest_interval_days", 7))
        if interval > 0:
            try:
                _run_strategy_backtest_if_due(config, interval)
            except Exception as e:
                logger.error(f"策略回测任务异常: {e}")
        try:
            StrategyScreener(config).run()
        except Exception as e:
            logger.error(f"策略选股任务异常: {e}")


def _run_strategy_backtest_if_due(config: dict, interval_days: int) -> bool:
    from src.strategy.strategy_backtest import StrategyBacktester

    backtester = StrategyBacktester(config)
    latest = backtester.latest()
    if latest and latest.get("created_at", "") >= (datetime.now() - timedelta(days=interval_days)).strftime("%Y-%m-%d %H:%M"):
        return False
    backtester.run(days=int(config.get("screening", {}).get("backtest_days", 60)))
    return True


def _run_signal_generation(config: dict):
    """每日信号生成"""
    if _skip_non_trade_day(config, "交易信号生成"):
        return
    from src.strategy.scorer import CompositeScorer

    logger.info("===== 开始生成交易信号 =====")
    try:
        scorer = CompositeScorer(config)
        scorer.generate_signals()
        logger.info("===== 交易信号生成完成 =====")
    except Exception as e:
        logger.error(f"信号生成任务异常: {e}")
        return

    # AI 研判：给 Top 评分股写入 买入/观望/回避，观望和回避的信号不会生成订单
    if config.get("strategy", {}).get("ai_advisor_enabled", True):
        try:
            from src.services.trade_advisor import TradeAdvisor

            TradeAdvisor(config).advise_top_stocks(date.today().strftime("%Y-%m-%d"))
        except Exception as e:
            logger.error(f"AI 研判任务异常: {e}")

    # 信号 → 待确认订单（开启 trading.auto_confirm 时直接在模拟盘成交）
    try:
        from src.trading.execution_service import ExecutionService

        execution = ExecutionService(config)
        if execution.enabled:
            execution.execute_signals(signal_date=date.today().strftime("%Y-%m-%d"))
    except Exception as e:
        logger.error(f"交易执行任务异常: {e}")


def _run_daily_report(config: dict):
    """LLM 大盘复盘 + 每日报告推送（大盘复盘 + 信号 + 订单 + 模拟盘 + 信号绩效）。"""
    if _skip_non_trade_day(config, "每日报告推送"):
        return
    if config.get("market_review", {}).get("enabled", True):
        from src.services.market_review import MarketReviewService

        try:
            MarketReviewService(config).generate()
        except Exception as e:
            logger.error(f"大盘复盘任务异常: {e}")
    if not config.get("notifier", {}).get("daily_report_enabled", True):
        return
    from src.services.daily_report import DailyReportService

    try:
        DailyReportService(config).push()
    except Exception as e:
        logger.error(f"每日报告推送异常: {e}")


def _run_watchlist_report(config: dict):
    """自选股决策仪表盘：逐只 AI 诊断并推送。"""
    if _skip_non_trade_day(config, "自选股决策仪表盘"):
        return
    if not config.get("watchlist", {}).get("daily_report", True):
        return
    from src.services.watchlist_report import WatchlistReportService

    try:
        WatchlistReportService(config).run()
    except Exception as e:
        logger.error(f"自选股决策仪表盘任务异常: {e}")


def _run_self_learning(config: dict):
    """每日自学习任务。"""
    if _skip_non_trade_day(config, "系统自学习"):
        return
    from src.services.self_learning import SelfLearningService

    logger.info("===== 开始系统自学习 =====")
    try:
        service = SelfLearningService(config)
        result = service.run_daily_learning()
        logger.info(f"===== 系统自学习完成: {result} =====")
    except Exception as e:
        logger.error(f"自学习任务异常: {e}")


def build_scheduler(config: dict, scheduler=None):
    """把全部定时任务加到调度器上（默认 BlockingScheduler；API 服务传入 BackgroundScheduler）。"""
    sched_cfg = config.get("scheduler", {})
    scheduler = scheduler if scheduler is not None else BlockingScheduler()

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

    # 每日报告推送（16:10，信号和订单生成之后）
    report_time = sched_cfg.get("daily_report_time", "16:10")
    hour, minute = report_time.split(":")
    scheduler.add_job(
        _run_daily_report,
        trigger=CronTrigger(hour=int(hour), minute=int(minute), day_of_week="mon-fri"),
        args=[config],
        id="daily_report",
        name="每日报告推送",
    )

    # 自选股决策仪表盘（16:30，大盘复盘和日报之后）
    watchlist_time = sched_cfg.get("watchlist_report_time", "16:30")
    hour, minute = watchlist_time.split(":")
    scheduler.add_job(
        _run_watchlist_report,
        trigger=CronTrigger(hour=int(hour), minute=int(minute), day_of_week="mon-fri"),
        args=[config],
        id="watchlist_report",
        name="自选股决策仪表盘",
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
    return scheduler


# 一次性运行（GitHub Actions、Docker 或系统定时任务在收盘后调用）：按定时任务的先后顺序执行
ONCE_STEPS: dict[str, tuple[str, tuple]] = {
    "collect": ("数据采集", (_run_hot_search_collection, _run_cailianshe_collection,
                             _run_stock_data_collection, _run_global_data_collection)),
    "analysis": ("每日综合分析", (_run_daily_analysis,)),
    "signals": ("交易信号", (_run_signal_generation,)),
    "report": ("大盘复盘与日报", (_run_daily_report,)),
    "learn": ("自学习", (_run_self_learning,)),
    "watchlist": ("自选股仪表盘", (_run_watchlist_report,)),
}


def run_once(config: dict, steps: list[str] | None = None) -> list[dict]:
    """依次执行各步骤一次，返回每步用时。非交易日时行情和分析类步骤照常跳过。"""
    import time

    selected = steps or list(ONCE_STEPS)
    unknown = [s for s in selected if s not in ONCE_STEPS]
    if unknown:
        raise ValueError(f"未知步骤: {', '.join(unknown)}（可选: {', '.join(ONCE_STEPS)}）")
    results = []
    for key in ONCE_STEPS:  # 按固定顺序执行，与传入顺序无关
        if key not in selected:
            continue
        label, jobs = ONCE_STEPS[key]
        logger.info(f"===== [{key}] {label} =====")
        started = time.monotonic()
        for job in jobs:
            job(config)
        results.append({"step": key, "label": label, "seconds": round(time.monotonic() - started, 1)})
    return results


def start_scheduler(config: dict):
    """启动任务调度器（阻塞）"""
    scheduler = build_scheduler(config)
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("调度器已停止")
