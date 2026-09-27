from __future__ import annotations

from datetime import date

from src import scheduler as scheduler_mod
from src import trading_calendar
from src.collectors.stock_info import StockInfoCollector
from src.database import db as db_module
from src.database.db import get_db_session, init_db
from src.database.models import StockDaily, StockScore, TradeFill, TradeOrder, TradeSignal
from src.services import pipeline_service as pipeline_mod
from src.strategy import scorer as scorer_mod
from src.trading.constants import ORDER_STATUS_FILLED, ORDER_STATUS_PENDING_CONFIRM
from src.trading.execution_service import ExecutionService

TODAY = date.today().strftime("%Y-%m-%d")

# 代码 -> (名称, 收盘价, 评分评级)
SAMPLES = {
    "600519": ("强买样本", 50.0, "strong_buy"),
    "002594": ("买入样本", 20.0, "buy"),
    "300750": ("观望样本", 30.0, "hold"),
}


def _reset_db_engine():
    if db_module._engine:
        db_module._engine.dispose()
    db_module._engine = None
    db_module._SessionFactory = None


def _setup(tmp_path, monkeypatch, **trading) -> dict:
    """建库并写入评分引擎风格的信号（无 AI 研判）；屏蔽上市日期联网刷新。"""
    db_path = str(tmp_path / "trading.db")
    _reset_db_engine()
    init_db(db_path)
    monkeypatch.setattr(StockInfoCollector, "refresh_if_stale", lambda self, path, max_age_hours=24: 0)

    with get_db_session(db_path) as session:
        for code, (name, close, rec) in SAMPLES.items():
            session.add(StockDaily(code=code, name=name, trade_date=TODAY, close=close))
            session.add(StockScore(code=code, name=name, score_date=TODAY, composite_score=80.0, recommendation=rec))
            session.add(
                TradeSignal(
                    code=code,
                    name=name,
                    signal_date=TODAY,
                    signal_type="buy",
                    signal_strength=0.8,
                    composite_score=80.0,
                    reason="测试信号",
                )
            )
    return {
        "database": {"sqlite_path": db_path},
        "risk": {"blacklist_keywords": ["ST", "*ST"]},
        "strategy": {"weights": {}, "learning": {"enabled": False}},
        "trading": {"default_order_budget": 100_000, "max_orders_per_run": 10, **trading},
    }


def _orders(db_path: str) -> dict[str, str]:
    with get_db_session(db_path) as session:
        return {o.code: o.status for o in session.query(TradeOrder).all()}


def test_execute_signals_creates_pending_orders_and_skips_hold(tmp_path, monkeypatch):
    config = _setup(tmp_path, monkeypatch)
    service = ExecutionService(config)

    assert service.execute_signals(TODAY) == {"prepared": 2, "confirmed": 0}
    assert _orders(service.db_path) == {
        "600519": ORDER_STATUS_PENDING_CONFIRM,
        "002594": ORDER_STATUS_PENDING_CONFIRM,
    }
    # 幂等：重复执行不再生成
    assert service.execute_signals(TODAY) == {"prepared": 0, "confirmed": 0}
    _reset_db_engine()


def test_auto_confirm_fills_and_paper_account_survives_restart(tmp_path, monkeypatch):
    config = _setup(tmp_path, monkeypatch, auto_confirm=True)
    db_path = config["database"]["sqlite_path"]

    assert ExecutionService(config).execute_signals(TODAY) == {"prepared": 2, "confirmed": 2}
    assert set(_orders(db_path).values()) == {ORDER_STATUS_FILLED}
    with get_db_session(db_path) as session:
        assert session.query(TradeFill).count() == 2
        session.query(StockDaily).filter(StockDaily.code == "600519").update({"close": 55.0})

    # 新实例（模拟重启 / 另一个进程）从成交记录恢复账户
    snapshot = ExecutionService(config).get_trading_snapshot()
    account = snapshot["account"]
    positions = {p["code"]: p for p in snapshot["positions"]}
    # 预算 10 万 × 强度 0.8：600519 买 1600 股 @50，002594 买 4000 股 @20
    assert positions["600519"]["quantity"] == 1600
    assert positions["002594"]["quantity"] == 4000
    assert positions["600519"]["name"] == "强买样本"
    assert account["cash"] == 1_000_000 - 80_000 - 80_000
    assert positions["600519"]["unrealized_pnl"] == (55.0 - 50.0) * 1600
    assert account["total_assets"] == account["cash"] + 1600 * 55.0 + 4000 * 20.0
    _reset_db_engine()


def test_manual_confirm_uses_restored_cash(tmp_path, monkeypatch):
    config = _setup(tmp_path, monkeypatch, paper_initial_cash=100_000)
    db_path = config["database"]["sqlite_path"]
    ExecutionService(config).execute_signals(TODAY)
    with get_db_session(db_path) as session:
        order_ids = {o.code: o.id for o in session.query(TradeOrder).all()}

    assert ExecutionService(config).confirm_and_send(order_ids["600519"])["ok"] is True
    # 另一实例看到的剩余资金只有 2 万，8 万的订单应因资金不足被拒
    result = ExecutionService(config).confirm_and_send(order_ids["002594"])
    assert result == {"ok": False, "error": "insufficient cash"}
    _reset_db_engine()


def test_scheduler_signal_job_prepares_orders(tmp_path, monkeypatch):
    class _DummyScorer:
        def __init__(self, config):
            pass

        def generate_signals(self):
            return []

    monkeypatch.setattr(scorer_mod, "CompositeScorer", _DummyScorer)
    monkeypatch.setattr(trading_calendar, "load", lambda db_path, refresh=True: True)
    monkeypatch.setattr(trading_calendar, "is_trade_day", lambda d=None: True)

    config = _setup(tmp_path, monkeypatch, execution_enabled=False)
    scheduler_mod._run_signal_generation(config)
    assert _orders(config["database"]["sqlite_path"]) == {}

    config["trading"]["execution_enabled"] = True
    scheduler_mod._run_signal_generation(config)
    assert len(_orders(config["database"]["sqlite_path"])) == 2
    _reset_db_engine()


def test_scheduler_skips_signal_job_on_non_trade_day(tmp_path, monkeypatch):
    calls = []

    class _DummyScorer:
        def __init__(self, config):
            calls.append("init")

        def generate_signals(self):
            calls.append("generate")

    monkeypatch.setattr(scorer_mod, "CompositeScorer", _DummyScorer)
    monkeypatch.setattr(trading_calendar, "load", lambda db_path, refresh=True: True)
    monkeypatch.setattr(trading_calendar, "is_trade_day", lambda d=None: False)

    config = _setup(tmp_path, monkeypatch)
    scheduler_mod._run_signal_generation(config)
    assert calls == []
    assert _orders(config["database"]["sqlite_path"]) == {}
    _reset_db_engine()


def test_pipeline_premarket_predict_prepares_orders(tmp_path, monkeypatch):
    config = _setup(tmp_path, monkeypatch)
    db_path = config["database"]["sqlite_path"]

    class _DummyPredictor:
        def __init__(self, config):
            pass

        def predict(self):
            with get_db_session(db_path) as session:
                session.add(StockDaily(code="000858", name="预测样本", trade_date=TODAY, close=10.0))
                session.add(
                    TradeSignal(
                        code="000858",
                        name="预测样本",
                        signal_date=TODAY,
                        signal_type="premarket",
                        signal_strength=0.9,
                        composite_score=90.0,
                        ai_verdict="买入",
                    )
                )
            return [{"code": "000858"}]

    monkeypatch.setattr(pipeline_mod, "CollectorOrchestrator", lambda config: None)
    monkeypatch.setattr(pipeline_mod, "SelfLearningService", lambda config: None)
    monkeypatch.setattr("src.services.premarket_predictor.LimitUpPredictor", _DummyPredictor)

    pipeline = pipeline_mod.PipelineService(config)
    result = pipeline.premarket_predict()
    assert result == {"prediction_count": 1, "orders": {"prepared": 3, "confirmed": 0}}

    snapshot = pipeline.trading_snapshot()
    pending = [o for o in snapshot["orders"] if o["code"] == "000858"]
    assert pipeline.confirm_order(pending[0]["id"])["ok"] is True
    assert {p["code"] for p in pipeline.trading_snapshot()["positions"]} == {"000858"}
    _reset_db_engine()
