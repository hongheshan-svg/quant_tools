"""桌面端统一流程编排。"""

from typing import Any

from loguru import logger

from src.config_loader import load_config
from src.database.db import init_db
from src.services.collector_orchestrator import CollectorOrchestrator
from src.services.self_learning import SelfLearningService


class PipelineService:
    """业务流水线服务。"""

    def __init__(self, config: dict | None = None):
        self.config = config or load_config()
        self.db_path = self.config.get("database", {}).get("sqlite_path", "data/quant.db")
        init_db(self.db_path)
        self.collector = CollectorOrchestrator(self.config)
        self.learning = SelfLearningService(self.config)

    def collect(self) -> dict[str, Any]:
        """采集所有数据（行情、新闻、涨停池、龙虎榜、北向资金、国际数据等）。"""
        return self.collector.collect_all()

    def refresh_market_overview(self) -> dict[str, Any]:
        """仅刷新市场全局概况（成交额、涨跌家数等）。"""
        return self.collector.collect_market_overview()

    def premarket_predict(self) -> dict[str, Any]:
        """AI涨停预测：根据当前所有数据预测最可能涨停的10只。"""
        logger.info("开始AI涨停预测...")
        try:
            from src.services.premarket_predictor import LimitUpPredictor
            predictor = LimitUpPredictor(self.config)
            predictions = predictor.predict()
            return {"prediction_count": len(predictions)}
        except Exception as e:
            logger.error(f"涨停预测异常: {e}")
            return {"prediction_count": 0, "error": str(e)}

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
