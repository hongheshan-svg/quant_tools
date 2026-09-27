"""桌面端统一流程编排。"""

import threading
from datetime import date, datetime
from typing import Any

from loguru import logger

from src.config_loader import load_config
from src.database.db import init_db
from src.services.collector_orchestrator import CollectorOrchestrator
from src.services.self_learning import SelfLearningService
from src.trading.execution_service import ExecutionService

MARKET_CLOSE_HHMM = "15:00"


def _after_close(now: datetime | None = None) -> bool:
    """是否已收盘（按时间判断，交易日由调用方负责）。"""
    return (now or datetime.now()).strftime("%H:%M") >= MARKET_CLOSE_HHMM


class PipelineService:
    """业务流水线服务。"""

    def __init__(self, config: dict | None = None):
        self.config = config or load_config()
        self.db_path = self.config.get("database", {}).get("sqlite_path", "data/quant.db")
        init_db(self.db_path)
        self.collector = CollectorOrchestrator(self.config)
        self.learning = SelfLearningService(self.config)
        self.execution = ExecutionService(self.config)
        # 界面各按钮在不同工作线程调用交易接口，串行化避免重复下单
        self._execution_lock = threading.Lock()

    def collect(self) -> dict[str, Any]:
        """采集所有数据（行情、新闻、涨停池、龙虎榜、北向资金、国际数据等）。"""
        return self.collector.collect_all()

    def refresh_market_overview(self) -> dict[str, Any]:
        """仅刷新市场全局概况（成交额、涨跌家数等）。"""
        return self.collector.collect_market_overview()

    def premarket_predict(self) -> dict[str, Any]:
        """AI涨停预测：根据当前所有数据预测最可能涨停的10只，并为买入信号生成待确认订单。"""
        logger.info("开始AI涨停预测...")
        try:
            from src.services.premarket_predictor import LimitUpPredictor
            predictor = LimitUpPredictor(self.config)
            predictions = predictor.predict()
        except Exception as e:
            logger.error(f"涨停预测异常: {e}")
            return {"prediction_count": 0, "error": str(e)}

        result: dict[str, Any] = {"prediction_count": len(predictions)}
        if predictions and self.execution.enabled:
            result["orders"] = self.prepare_orders(signal_date=date.today().strftime("%Y-%m-%d"))
        # 收盘后的预测顺带生成当天的 LLM 大盘复盘（每个交易日只生成一次），日报里会带上
        if predictions and _after_close() and self.config.get("market_review", {}).get("enabled", True):
            result["review"] = self.market_review().get("stance", "")
        if predictions and self.config.get("notifier", {}).get("daily_report_enabled", True):
            result["report"] = self.push_daily_report()
        return result

    def push_daily_report(self) -> dict[str, Any]:
        """推送每日报告到已启用的机器人（未启用任何渠道时直接返回）。"""
        from src.services.daily_report import DailyReportService

        try:
            return DailyReportService(self.config).push()
        except Exception as e:
            logger.error(f"每日报告推送异常: {e}")
            return {"pushed": False, "error": str(e)}

    # ---- 个股查询 ----

    def search_stocks(self, text: str, limit: int = 20) -> list[dict[str, Any]]:
        """按代码、名称、拼音首字母搜索股票。"""
        from src.services.stock_search import StockSearch

        return StockSearch(self.db_path).search(text, limit)

    def ensure_history(self, code: str, name: str = "") -> int:
        """本地日线不足时联网补齐，返回新写入的根数。"""
        from src.collectors.daily_history import ensure_daily_history

        try:
            return ensure_daily_history(code, self.db_path, name)
        except Exception as e:
            logger.warning(f"补齐日线异常 [{code}]: {e}")
            return 0

    def stock_news(self, code: str, refresh: bool = False) -> dict[str, Any]:
        """个股近 7 天新闻和近 30 天公告（东方财富）。"""
        from src.collectors.stock_news import get_stock_news

        return get_stock_news(code, refresh=refresh)

    def market_review(self, force: bool = False) -> dict[str, Any]:
        """LLM 大盘复盘（趋势/情绪/主线 → 次日姿态、仓位、关注与回避方向）。"""
        from src.services.market_review import MarketReviewService

        try:
            return MarketReviewService(self.config).generate(force=force)
        except Exception as e:
            logger.error(f"大盘复盘异常: {e}")
            return {"error": str(e)}

    def latest_market_review(self) -> dict[str, Any] | None:
        from src.services.market_review import MarketReviewService

        return MarketReviewService(self.config).get()

    def screen_stocks(self) -> dict[str, Any]:
        """全市场策略选股（放量突破、强势未板、龙回头、主线补涨、缩量回踩、超跌反弹）。"""
        from src.strategy.screener import StrategyScreener

        try:
            return StrategyScreener(self.config).run().to_dict()
        except Exception as e:
            logger.error(f"策略选股异常: {e}")
            return {"picks": [], "notes": [f"选股失败: {e}"], "stats": {}, "trade_date": "", "regime": ""}

    def latest_screening(self) -> dict[str, Any]:
        """最近一次选股结果（附次日涨幅）和近 30 天各策略的次日表现。"""
        from src.strategy.screener import StrategyScreener

        screener = StrategyScreener(self.config)
        return {"picks": screener.latest(), "performance": screener.performance()}

    def market_regime(self) -> dict[str, Any]:
        """大盘环境评估（进攻/均衡/防守/冰点 + 情绪周期）。"""
        from src.analyzers.market_regime import MarketRegimeAnalyzer

        regime = MarketRegimeAnalyzer(self.config).analyze()
        return {**regime.to_dict(), "summary": regime.summary()}

    def diagnose_stock(self, code: str, force: bool = False) -> dict[str, Any]:
        """个股 AI 诊断（决策仪表盘）；30 分钟内的结果直接复用，force=True 时重新诊断。"""
        from src.services.stock_diagnosis import StockDiagnosisService

        try:
            return StockDiagnosisService(self.config).diagnose(code, force=force)
        except Exception as e:
            logger.error(f"个股诊断异常 [{code}]: {e}")
            return {"code": code, "error": str(e)}

    def latest_diagnosis(self, code: str) -> dict[str, Any] | None:
        from src.services.stock_diagnosis import StockDiagnosisService

        return StockDiagnosisService(self.config).latest(code)

    def main_themes(self, dimension: str = "concept") -> list[dict[str, Any]]:
        """近 5 日涨停池量化的主线（阶段、热度、梯队、龙头）；dimension 为 concept（题材）或 industry（行业）。"""
        from src.analyzers.theme_tracker import ThemeTracker

        return [t.to_dict() for t in ThemeTracker(self.config).analyze(dimension=dimension)]

    @staticmethod
    def data_source_status() -> list[dict[str, Any]]:
        """本进程内各数据源的健康状态（内存数据，无 IO）。"""
        from src.collectors.source_chain import source_health

        return source_health.snapshot()

    def signal_performance(self, lookback_days: int = 60) -> dict[str, Any]:
        """近 lookback_days 天交易信号的绩效回测（只读统计）。"""
        from src.services.signal_performance import SignalPerformanceService

        return SignalPerformanceService(self.config).evaluate(lookback_days)

    # ---- 交易执行（模拟盘） ----

    def prepare_orders(self, signal_date: str | None = None) -> dict[str, Any]:
        """把交易信号转为订单；未指定日期时使用最新信号日期。"""
        try:
            with self._execution_lock:
                return self.execution.execute_signals(signal_date)
        except Exception as e:
            logger.error(f"生成订单异常: {e}")
            return {"prepared": 0, "confirmed": 0, "error": str(e)}

    def check_exits(self) -> dict[str, Any]:
        """检查模拟盘持仓的止损止盈，触发时生成卖出订单。"""
        try:
            with self._execution_lock:
                return self.execution.generate_exit_orders()
        except Exception as e:
            logger.error(f"止损止盈检查异常: {e}")
            return {"exit_orders": 0, "confirmed": 0, "error": str(e)}

    def check_alerts(self) -> dict[str, Any]:
        """盘中提醒：封板/炸板/跌破止损/达到目标价/大跌/自定义规则，交易时段外直接跳过。"""
        from src.services.alert_service import AlertService

        try:
            return AlertService(self.config).run()
        except Exception as e:
            logger.error(f"盘中提醒检查异常: {e}")
            return {"alerts": 0, "error": str(e)}

    def recent_alerts(self, limit: int = 200) -> list[dict[str, Any]]:
        from src.services.alert_service import AlertService

        return AlertService(self.config).recent(limit)

    def confirm_order(self, order_id: str) -> dict[str, Any]:
        with self._execution_lock:
            return self.execution.confirm_and_send(order_id, operator="desktop")

    def cancel_order(self, order_id: str) -> dict[str, Any]:
        with self._execution_lock:
            return self.execution.cancel_order(order_id, operator="desktop")

    def trading_snapshot(self) -> dict[str, Any]:
        with self._execution_lock:
            return self.execution.get_trading_snapshot()

    def self_learn(self) -> dict[str, Any]:
        """执行自学习（评估历史效果并更新参数）。"""
        logger.info("开始系统自学习...")
        try:
            return self.learning.run_daily_learning()
        except Exception as e:
            logger.error(f"自学习异常: {e}")
            return {"status": "error", "error": str(e)}

    def run_full(self) -> dict[str, Any]:
        """一键全流程 = 采集最新数据 + AI涨停预测。"""
        result: dict[str, Any] = {}
        result["collect"] = self.collect()
        result["learn"] = self.self_learn()
        result["predict"] = self.premarket_predict()
        return result
