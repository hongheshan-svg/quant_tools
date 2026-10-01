"""
任务调度器 - APScheduler 定时采集和分析

收盘后的每日任务（分析、信号、复盘日报、自学习、信号评估、自选股仪表盘、提醒日报）默认在独立子进程中运行，
超过 scheduler.job_timeout_minutes 未完成就终止整个进程树并推送系统错误：外部接口（如部分 AKShare 请求没有超时）
卡住时不会一直占着线程、拖住后面的任务。间隔采集任务仍在本进程运行（市场概况缓存、盘中提醒状态都在进程内）。
"""

import os
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from datetime import date, datetime, timedelta
from pathlib import Path

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


def _report_error(config: dict, source: str, error: Exception) -> None:
    """任务出错时推送系统错误通知（限频；推送失败不影响任务）。"""
    try:
        from src.services.system_alerts import report_error

        report_error(config, source, error)
    except Exception as e:
        logger.error(f"系统错误推送异常: {e}")


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
            _report_error(config, "热搜数据采集", e)


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
        _report_error(config, "财联社快讯采集", e)


def _run_rss_collection(config: dict):
    """执行 RSS/Atom 资讯源采集（与交易日无关）"""
    cfg = config.get("intelligence") or {}
    if not cfg.get("enabled", True):
        return
    from src.collectors.rss import RSSCollector, _enabled_sources, save_items

    if not _enabled_sources(config):
        return
    collector = RSSCollector(config)
    try:
        items = collector.safe_collect()
        added = save_items(items, config.get("database", {}).get("sqlite_path", "data/quant.db"),
                           int(cfg.get("keep_days", 7)))
        logger.info(f"RSS 资讯入库 {added} 条（抓到 {len(items)} 条）")
    except Exception as e:
        logger.error(f"RSS 采集任务异常: {e}")
        _report_error(config, "RSS 资讯源采集", e)
    finally:
        collector.close()


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
        _report_error(config, "行情数据采集", e)
        return
    try:
        collector.collect_market_overview()  # 首页指数、涨跌家数、成交额随行情更新
    except Exception as e:
        logger.warning(f"市场概况刷新失败: {e}")

    # 行情更新后检查模拟盘持仓的止损止盈
    try:
        from src.trading.execution_service import ExecutionService

        execution = ExecutionService(config)
        if execution.enabled:
            execution.generate_exit_orders()
    except Exception as e:
        logger.error(f"止损止盈检查异常: {e}")
        _report_error(config, "止损止盈检查", e)

    # 盘中提醒（封板/炸板/跌破止损/大跌/自定义规则）
    try:
        from src.services.alert_service import AlertService

        AlertService(config).run()
    except Exception as e:
        logger.error(f"盘中提醒检查异常: {e}")
        _report_error(config, "盘中提醒检查", e)


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
            _report_error(config, "国际数据采集", e)


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
        _report_error(config, "每日综合分析", e)

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
                _report_error(config, "策略回测", e)
        try:
            StrategyScreener(config).run()
        except Exception as e:
            logger.error(f"策略选股任务异常: {e}")
            _report_error(config, "策略选股", e)


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
        _report_error(config, "每日信号生成", e)
        return

    # AI 研判：给 Top 评分股写入 买入/观望/回避，观望和回避的信号不会生成订单
    if config.get("strategy", {}).get("ai_advisor_enabled", True):
        try:
            from src.services.trade_advisor import TradeAdvisor

            TradeAdvisor(config).advise_top_stocks(date.today().strftime("%Y-%m-%d"))
        except Exception as e:
            logger.error(f"AI 研判任务异常: {e}")
            _report_error(config, "AI 研判", e)

    # 信号 → 待确认订单（开启 trading.auto_confirm 时直接在模拟盘成交）
    try:
        from src.trading.execution_service import ExecutionService

        execution = ExecutionService(config)
        if execution.enabled:
            execution.execute_signals(signal_date=date.today().strftime("%Y-%m-%d"))
    except Exception as e:
        logger.error(f"交易执行任务异常: {e}")
        _report_error(config, "交易执行", e)


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
            _report_error(config, "大盘复盘", e)
    if not config.get("notifier", {}).get("daily_report_enabled", True):
        return
    from src.services.daily_report import DailyReportService

    try:
        DailyReportService(config).push()
    except Exception as e:
        logger.error(f"每日报告推送异常: {e}")
        _report_error(config, "每日报告推送", e)


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
        _report_error(config, "自选股决策仪表盘", e)


def _run_alert_digest(config: dict):
    """盘中提醒日报：汇总当天的提醒记录并推送。"""
    if _skip_non_trade_day(config, "盘中提醒日报"):
        return
    try:
        from src.services.alert_service import AlertService

        AlertService(config).digest()
    except Exception as e:
        logger.error(f"盘中提醒日报异常: {e}")
        _report_error(config, "盘中提醒日报", e)


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
        _report_error(config, "每日自学习", e)


def _run_signal_lifecycle(config: dict):
    """决策信号评估：更新有效期、止损止盈和后验收益。"""
    if _skip_non_trade_day(config, "决策信号评估"):
        return
    from src.services.decision_signals import DecisionSignalService

    logger.info("===== 开始决策信号评估 =====")
    try:
        result = DecisionSignalService(config).evaluate()
        logger.info(f"===== 决策信号评估完成: {result} =====")
    except Exception as e:
        logger.error(f"决策信号评估异常: {e}")
        _report_error(config, "决策信号评估", e)
    try:
        from src.services.skill_consult import SkillOpinionService

        result = SkillOpinionService(config).evaluate()
        logger.info(f"策略观点评估完成: {result}")
    except Exception as e:
        logger.error(f"策略观点评估异常: {e}")
        _report_error(config, "策略观点评估", e)


# 定时任务清单：任务 id -> (中文名, 任务函数)；build_scheduler 注册、Web 定时任务面板和「立即运行」共用
JOBS: dict[str, tuple[str, Callable[[dict], None]]] = {
    "hot_search": ("热搜数据采集", _run_hot_search_collection),
    "cailianshe": ("财联社快讯采集", _run_cailianshe_collection),
    "rss": ("RSS 资讯源采集", _run_rss_collection),
    "stock_data": ("行情数据采集", _run_stock_data_collection),
    "global_data": ("国际数据采集", _run_global_data_collection),
    "daily_analysis": ("每日综合分析", _run_daily_analysis),
    "signal_generation": ("每日信号生成", _run_signal_generation),
    "daily_report": ("大盘复盘与日报", _run_daily_report),
    "watchlist_report": ("自选股决策仪表盘", _run_watchlist_report),
    "self_learning": ("每日自学习", _run_self_learning),
    "signal_lifecycle": ("决策信号评估", _run_signal_lifecycle),
    "alert_digest": ("盘中提醒日报", _run_alert_digest),   # 仅 alerts.daily_digest 为真时注册
}

# 在独立子进程中运行、受总时长限制的每日任务
ISOLATED_JOBS = frozenset({"daily_analysis", "signal_generation", "daily_report", "watchlist_report",
                           "self_learning", "signal_lifecycle", "alert_digest"})
DEFAULT_JOB_TIMEOUT_MINUTES = 90
JOB_FAILED_EXIT = 2          # 子进程里任务抛异常（已自行推送系统错误）时的退出码
KILL_GRACE_SECONDS = 10
SERVER_SCRIPT = Path(__file__).resolve().parent.parent / "server.py"


def _job_command(job_id: str) -> list[str]:
    """运行单个任务的子进程命令：打包后是后台程序本身，源码运行是 python server.py"""
    if getattr(sys, "frozen", False):
        return [sys.executable, "--run-job", job_id, "--workdir", os.getcwd()]
    return [sys.executable, str(SERVER_SCRIPT), "--run-job", job_id, "--workdir", os.getcwd()]


def _kill_tree(proc: subprocess.Popen) -> None:
    """终止子进程及其子孙（任务里可能启动了 Chromium）"""
    if proc.poll() is not None:
        return
    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True, timeout=30)
        else:
            os.killpg(proc.pid, signal.SIGTERM)
            try:
                proc.wait(timeout=KILL_GRACE_SECONDS)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, OSError):
        pass
    try:
        proc.wait(timeout=KILL_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        proc.kill()


def run_isolated(job_id: str, config: dict, command: list[str] | None = None) -> bool:
    """在独立子进程中运行一个每日任务，超时终止；返回是否正常完成。
    任务自身出错时子进程已推送系统错误，这里只对超时和异常退出推送。"""
    name = JOBS[job_id][0]
    timeout_minutes = float((config.get("scheduler") or {}).get("job_timeout_minutes") or DEFAULT_JOB_TIMEOUT_MINUTES)
    cmd = command or _job_command(job_id)
    started = time.monotonic()
    kwargs: dict = {"cwd": os.getcwd()}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | getattr(subprocess, "CREATE_NO_WINDOW", 0)
    else:
        kwargs["start_new_session"] = True   # 自成进程组，超时可以整组终止
    proc = subprocess.Popen(cmd, **kwargs)
    logger.info(f"[定时任务] {name} 在独立进程中运行（pid {proc.pid}，最长 {timeout_minutes:g} 分钟）")
    try:
        code = proc.wait(timeout=timeout_minutes * 60)
    except subprocess.TimeoutExpired:
        _kill_tree(proc)
        logger.error(f"[定时任务] {name} 超过 {timeout_minutes:g} 分钟未完成，已终止")
        _report_error(config, name, TimeoutError(f"超过 {timeout_minutes:g} 分钟未完成，已终止（可调大 scheduler.job_timeout_minutes）"))
        return False
    elapsed = time.monotonic() - started
    if code == 0:
        logger.info(f"[定时任务] {name} 完成，用时 {elapsed:.0f} 秒")
        return True
    if code != JOB_FAILED_EXIT:
        _report_error(config, name, RuntimeError(f"任务进程异常退出（退出码 {code}）"))
    logger.error(f"[定时任务] {name} 失败（退出码 {code}，用时 {elapsed:.0f} 秒）")
    return False


def run_job(job_id: str, config: dict) -> None:
    """定时任务和「立即运行」的统一入口：每日任务按配置在独立进程中运行，其余在本进程运行"""
    if job_id in ISOLATED_JOBS and (config.get("scheduler") or {}).get("isolate_daily_jobs", True):
        run_isolated(job_id, config)
    else:
        JOBS[job_id][1](config)


def run_job_entry(job_id: str) -> int:
    """子进程入口（server.py --run-job）：按当前目录的配置运行一个任务后退出"""
    from main import setup_logging
    from src.config_loader import load_config

    config = load_config()
    setup_logging(config)
    if job_id not in JOBS:
        logger.error(f"未知的定时任务: {job_id}")
        return JOB_FAILED_EXIT
    name, fn = JOBS[job_id]
    try:
        fn(config)
    except Exception as e:
        logger.exception(f"[定时任务] {name} 出错: {e}")
        _report_error(config, name, e)
        return JOB_FAILED_EXIT
    return 0


_WEEKDAYS = {"mon-fri": "工作日", "*": "每天"}


def _describe_trigger(trigger) -> str:
    """把触发器转成中文描述：间隔任务「每 30 分钟」，cron 任务「工作日 15:30」。"""
    if isinstance(trigger, IntervalTrigger):
        seconds = int(trigger.interval.total_seconds())
        if seconds % 3600 == 0:
            return f"每 {seconds // 3600} 小时"
        if seconds % 60 == 0:
            return f"每 {seconds // 60} 分钟"
        return f"每 {seconds} 秒"
    if isinstance(trigger, CronTrigger):
        fields = {f.name: str(f) for f in trigger.fields}
        dow = fields.get("day_of_week", "*")
        prefix = _WEEKDAYS.get(dow, f"每周 {dow}")
        hour, minute = fields.get("hour", "*"), fields.get("minute", "*")
        if hour.isdigit() and minute.isdigit():
            return f"{prefix} {int(hour):02d}:{int(minute):02d}"
        return f"{prefix} {hour}时{minute}分"
    return str(trigger)


def describe_jobs(scheduler) -> list[dict]:
    """列出定时任务（id、中文名、触发规则、下次运行时间、是否暂停）；scheduler 为 None 时返回全部已知任务。"""
    if scheduler is None:
        return [{"id": jid, "name": name, "trigger": "", "next_run_time": None, "paused": False}
                for jid, (name, _) in JOBS.items()]
    rows = []
    for job in scheduler.get_jobs():
        # 调度器未启动时 job 没有 next_run_time 属性（pending 状态）
        nxt = getattr(job, "next_run_time", None)
        rows.append({
            "id": job.id,
            "name": JOBS[job.id][0] if job.id in JOBS else job.name,
            "trigger": _describe_trigger(job.trigger),
            "next_run_time": nxt.isoformat() if nxt else None,
            "paused": bool(getattr(scheduler, "running", False)) and nxt is None,
        })
    return rows


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
        name=JOBS["hot_search"][0],
    )

    # 财联社采集（每5分钟）
    interval = sched_cfg.get("cailianshe_interval", 5)
    scheduler.add_job(
        _run_cailianshe_collection,
        trigger=IntervalTrigger(minutes=interval),
        args=[config],
        id="cailianshe",
        name=JOBS["cailianshe"][0],
    )

    # RSS/Atom 资讯源（默认每30分钟，最少5分钟）
    intel_cfg = config.get("intelligence") or {}
    from src.collectors.rss import _enabled_sources

    if intel_cfg.get("enabled", True) and _enabled_sources(config):
        scheduler.add_job(
            _run_rss_collection,
            trigger=IntervalTrigger(minutes=max(5, int(intel_cfg.get("interval_minutes", 30) or 30))),
            args=[config],
            id="rss",
            name=JOBS["rss"][0],
        )

    # 行情数据采集（每15分钟，交易时间内）
    interval = sched_cfg.get("stock_data_interval", 15)
    scheduler.add_job(
        _run_stock_data_collection,
        trigger=IntervalTrigger(minutes=interval),
        args=[config],
        id="stock_data",
        name=JOBS["stock_data"][0],
    )

    # 国际数据采集（每30分钟）
    global_cfg = config.get("global_data", {})
    interval = global_cfg.get("global_news_interval", 30)
    scheduler.add_job(
        _run_global_data_collection,
        trigger=IntervalTrigger(minutes=interval),
        args=[config],
        id="global_data",
        name=JOBS["global_data"][0],
    )

    # 每日综合分析（收盘后15:30）
    analysis_time = sched_cfg.get("daily_analysis_time", "15:30")
    hour, minute = analysis_time.split(":")
    scheduler.add_job(
        run_job,
        trigger=CronTrigger(hour=int(hour), minute=int(minute), day_of_week="mon-fri"),
        args=["daily_analysis", config],
        id="daily_analysis",
        name=JOBS["daily_analysis"][0],
    )

    # 每日信号生成（16:00）
    signal_time = sched_cfg.get("daily_signal_time", "16:00")
    hour, minute = signal_time.split(":")
    scheduler.add_job(
        run_job,
        trigger=CronTrigger(hour=int(hour), minute=int(minute), day_of_week="mon-fri"),
        args=["signal_generation", config],
        id="signal_generation",
        name=JOBS["signal_generation"][0],
    )

    # 每日报告推送（16:10，信号和订单生成之后）
    report_time = sched_cfg.get("daily_report_time", "16:10")
    hour, minute = report_time.split(":")
    scheduler.add_job(
        run_job,
        trigger=CronTrigger(hour=int(hour), minute=int(minute), day_of_week="mon-fri"),
        args=["daily_report", config],
        id="daily_report",
        name=JOBS["daily_report"][0],
    )

    # 自选股决策仪表盘（16:30，大盘复盘和日报之后）
    watchlist_time = sched_cfg.get("watchlist_report_time", "16:30")
    hour, minute = watchlist_time.split(":")
    scheduler.add_job(
        run_job,
        trigger=CronTrigger(hour=int(hour), minute=int(minute), day_of_week="mon-fri"),
        args=["watchlist_report", config],
        id="watchlist_report",
        name=JOBS["watchlist_report"][0],
    )

    # 每日自学习（16:20）
    learn_time = sched_cfg.get("self_learning_time", "16:20")
    hour, minute = learn_time.split(":")
    scheduler.add_job(
        run_job,
        trigger=CronTrigger(hour=int(hour), minute=int(minute), day_of_week="mon-fri"),
        args=["self_learning", config],
        id="self_learning",
        name=JOBS["self_learning"][0],
    )

    # 决策信号评估（16:25，自学习之后）
    hour, minute = str(sched_cfg.get("signal_lifecycle_time") or "16:25").split(":")
    scheduler.add_job(
        run_job,
        trigger=CronTrigger(hour=int(hour), minute=int(minute), day_of_week="mon-fri"),
        args=["signal_lifecycle", config],
        id="signal_lifecycle",
        name=JOBS["signal_lifecycle"][0],
    )

    # 盘中提醒日报（可选，默认 15:10）
    alerts_cfg = config.get("alerts") or {}
    if alerts_cfg.get("daily_digest", False):
        hour, minute = str(alerts_cfg.get("digest_time") or "15:10").split(":")
        scheduler.add_job(
            run_job,
            trigger=CronTrigger(hour=int(hour), minute=int(minute), day_of_week="mon-fri"),
            args=["alert_digest", config],
            id="alert_digest",
            name=JOBS["alert_digest"][0],
        )

    logger.info(f"调度器已配置 {len(scheduler.get_jobs())} 个任务:")
    for job in scheduler.get_jobs():
        logger.info(f"  - {job.name} ({job.trigger})")
    return scheduler


# 一次性运行（GitHub Actions、Docker 或系统定时任务在收盘后调用）：按定时任务的先后顺序执行
ONCE_STEPS: dict[str, tuple[str, tuple]] = {
    "collect": ("数据采集", (_run_hot_search_collection, _run_cailianshe_collection,
                             _run_rss_collection, _run_stock_data_collection, _run_global_data_collection)),
    "analysis": ("每日综合分析", (_run_daily_analysis,)),
    "signals": ("交易信号", (_run_signal_generation,)),
    "report": ("大盘复盘与日报", (_run_daily_report,)),
    "learn": ("自学习", (_run_self_learning, _run_signal_lifecycle)),
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
