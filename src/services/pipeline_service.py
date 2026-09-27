"""桌面端统一流程编排。"""

import threading
from datetime import date
from typing import Any

from loguru import logger

from src.config_loader import load_config
from src.database.db import init_db
from src.services.collector_orchestrator import CollectorOrchestrator
from src.services.self_learning import SelfLearningService
from src.trading.execution_service import ExecutionService


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
        return result

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
