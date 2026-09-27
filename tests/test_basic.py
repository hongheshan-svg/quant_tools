"""
基础功能测试
验证核心模块可正常导入和初始化
"""

import os
import sys

# 确保项目根目录在路径中
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_config_loader():
    """测试配置加载"""
    from src.config_loader import load_config
    config = load_config("config/settings.yaml")
    assert isinstance(config, dict)
    assert "llm" in config
    assert "database" in config
    assert "strategy" in config
    print("[PASS] test_config_loader")


def test_database_init():
    """测试数据库初始化"""
    from src.database import db as db_module
    # 重置全局引擎以使用测试数据库
    db_module._engine = None
    db_module._SessionFactory = None
    db_module.init_db(db_path="data/test_quant.db")
    assert os.path.exists("data/test_quant.db")
    # 关闭引擎连接再删除
    if db_module._engine:
        db_module._engine.dispose()
        db_module._engine = None
        db_module._SessionFactory = None
    os.remove("data/test_quant.db")
    print("[PASS] test_database_init")


def test_models_import():
    """测试模型导入"""
    from src.database.models import (
        DragonTigerBoard,
        FinanceNews,
        GlobalImpactAnalysis,
        GlobalNews,
        HotSearch,
        LimitUpStock,
        NorthboundFlow,
        SentimentAnalysis,
        StockDaily,
        StockScore,
        TradeSignal,
        USMarketDaily,
        USStockEarnings,
    )
    imported_models = (
        HotSearch,
        FinanceNews,
        StockDaily,
        LimitUpStock,
        DragonTigerBoard,
        NorthboundFlow,
        USStockEarnings,
        GlobalNews,
        USMarketDaily,
        SentimentAnalysis,
        GlobalImpactAnalysis,
        StockScore,
        TradeSignal,
    )
    assert all(model is not None for model in imported_models)
    print("[PASS] test_models_import")


def test_collectors_import():
    """测试采集器导入"""
    from src.collectors.base import BaseCollector
    from src.collectors.weibo import WeiboCollector
    from src.collectors.douyin import DouyinCollector
    from src.collectors.toutiao import ToutiaoCollector
    from src.collectors.cailianshe import CailiansheCollector
    from src.collectors.xueqiu import XueqiuCollector
    from src.collectors.jiuyan import JiuyanCollector
    from src.collectors.stock_data import StockDataCollector
    from src.collectors.us_earnings import USEarningsCollector
    from src.collectors.global_news import GlobalNewsCollector
    imported_collectors = (
        BaseCollector,
        WeiboCollector,
        DouyinCollector,
        ToutiaoCollector,
        CailiansheCollector,
        XueqiuCollector,
        JiuyanCollector,
        StockDataCollector,
        USEarningsCollector,
        GlobalNewsCollector,
    )
    assert all(collector is not None for collector in imported_collectors)
    print("[PASS] test_collectors_import")


def test_analyzers_import():
    """测试分析器导入"""
    from src.analyzers.llm_client import LLMClient
    from src.analyzers.sentiment import SentimentAnalyzer
    from src.analyzers.topic_extractor import TopicExtractor
    from src.analyzers.limit_up import LimitUpAnalyzer
    from src.analyzers.global_impact import GlobalImpactAnalyzer
    imported_analyzers = (
        LLMClient,
        SentimentAnalyzer,
        TopicExtractor,
        LimitUpAnalyzer,
        GlobalImpactAnalyzer,
    )
    assert all(analyzer is not None for analyzer in imported_analyzers)
    print("[PASS] test_analyzers_import")


def test_strategy_import():
    """测试策略模块导入"""
    from src.strategy.scorer import CompositeScorer
    from src.strategy.sentiment_score import calculate_sentiment_score
    from src.strategy.limit_up_score import calculate_limit_up_score
    from src.strategy.capital_score import calculate_capital_score
    from src.strategy.tech_score import calculate_tech_score
    from src.strategy.global_score import calculate_global_score
    from src.strategy.risk_manager import RiskManager
    imported_strategy_items = (
        CompositeScorer,
        calculate_sentiment_score,
        calculate_limit_up_score,
        calculate_capital_score,
        calculate_tech_score,
        calculate_global_score,
        RiskManager,
    )
    assert all(item is not None for item in imported_strategy_items)
    print("[PASS] test_strategy_import")


def test_us_mapping():
    """测试美股-A股映射表"""
    from src.collectors.us_earnings import USEarningsCollector
    mapping = USEarningsCollector.get_mapping()
    assert "NVDA" in mapping
    assert "AAPL" in mapping
    assert "TSLA" in mapping
    assert len(mapping["NVDA"]["a_share_sectors"]) > 0
    print("[PASS] test_us_mapping")


def test_risk_manager():
    """测试风控模块"""
    from src.strategy.risk_manager import RiskManager
    rm = RiskManager()

    # 止损测试
    assert rm.check_stop_loss(10.0, 9.4) is True   # -6% 触发
    assert rm.check_stop_loss(10.0, 9.6) is False   # -4% 不触发

    # 止盈测试
    assert rm.check_take_profit(10.0, 11.6) is True  # +16% 触发
    assert rm.check_take_profit(10.0, 11.0) is False  # +10% 不触发

    print("[PASS] test_risk_manager")


def test_backtest_import():
    """测试回测模块导入"""
    from src.backtest.engine import BacktestEngine, BacktestResult
    assert BacktestResult is not None
    engine = BacktestEngine()
    assert engine.initial_capital == 1_000_000
    print("[PASS] test_backtest_import")


if __name__ == "__main__":
    test_config_loader()
    test_database_init()
    test_models_import()
    test_collectors_import()
    test_analyzers_import()
    test_strategy_import()
    test_us_mapping()
    test_risk_manager()
    test_backtest_import()
    print("\n===== 所有基础测试通过 =====")
