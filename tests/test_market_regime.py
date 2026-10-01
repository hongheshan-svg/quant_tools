from __future__ import annotations

from src.analyzers import market_regime as regime_mod
from src.analyzers.market_regime import MarketRegime, MarketRegimeAnalyzer
from src.collectors.stock_info import StockInfoCollector
from src.database import db as db_module
from src.database.db import get_db_session, init_db
from src.database.models import LimitUpStock, StockDaily, TradeSignal
from src.trading.execution_service import ExecutionService

PREV, TODAY = "2026-09-24", "2026-09-25"
INDEX_UP = {"sh_index": "3300", "sh_change_pct": 1.2, "sz_change_pct": 1.5, "cy_change_pct": 1.8}


def _reset_db_engine():
    if db_module._engine:
        db_module._engine.dispose()
    db_module._engine = None
    db_module._SessionFactory = None


def _init(tmp_path) -> str:
    db_path = str(tmp_path / "regime.db")
    _reset_db_engine()
    init_db(db_path)
    return db_path


def _seed_market(db_path: str, *, up: int, down: int, today_amount: float, prev_limit_up_change: float,
                 limit_ups: list[tuple[int, int]], limit_downs: int = 0) -> None:
    """400 只主板股票：前 60 只是昨日涨停股；limit_ups=[(连板, 开板次数)] 为今日涨停。"""
    with get_db_session(db_path) as session:
        for i in range(up + down):
            code = f"{600000 + i}"
            if i < 60:
                chg = prev_limit_up_change
            elif i < up:
                chg = 2.0
            elif i < up + limit_downs:
                chg = -10.0
            else:
                chg = -2.0
            session.add(StockDaily(code=code, name=f"S{i}", trade_date=PREV, change_pct=1.0, amount=1e9))
            session.add(StockDaily(code=code, name=f"S{i}", trade_date=TODAY, change_pct=chg, amount=today_amount))
        for i in range(60):
            session.add(LimitUpStock(code=f"{600000 + i}", name=f"S{i}", trade_date=PREV, continuous_days=1))
        for j, (days, opens) in enumerate(limit_ups):
            session.add(LimitUpStock(code=f"{600000 + j}", name=f"S{j}", trade_date=TODAY, continuous_days=days, open_count=opens))


def test_strong_market_is_attack(tmp_path):
    db_path = _init(tmp_path)
    limit_ups = [(5, 0), (3, 0), (2, 0), (2, 1)] + [(1, 0)] * 76 + [(1, 1)] * 10  # 90 只涨停，其中 4 只来自昨日涨停
    _seed_market(db_path, up=300, down=100, today_amount=1.15e9, prev_limit_up_change=4.0, limit_ups=limit_ups)

    regime = MarketRegimeAnalyzer({"database": {"sqlite_path": db_path}}).analyze(overview=INDEX_UP)
    m = regime.metrics
    assert regime.trade_date == TODAY
    assert (m["up"], m["down"], m["limit_up"], m["max_height"], m["multi_board"]) == (300, 100, 90, 5, 4)
    assert m["profit_effect"] == 4.0 and m["promotion_rate"] == 100.0  # 昨日 60 只涨停股今天全部再度涨停
    assert m["amount_change_pct"] == 15.0
    assert m["first_seal_rate"] == 87.8
    assert (regime.regime, regime.position_factor) == ("进攻", 1.0)
    assert regime.emotion_cycle == "升温"
    assert "市场环境：进攻" in regime.summary() and "最高5板" in regime.summary()
    _reset_db_engine()


def test_weak_market_is_freeze_with_board_specific_limit_down(tmp_path):
    db_path = _init(tmp_path)
    _seed_market(db_path, up=80, down=320, today_amount=0.7e9, prev_limit_up_change=-6.0,
                 limit_ups=[(1, 2)] * 10, limit_downs=35)
    with get_db_session(db_path) as session:
        # 创业板跌 12% 不算跌停，跌 19.8% 才算
        session.add(StockDaily(code="300001", name="创业A", trade_date=TODAY, change_pct=-12.0))
        session.add(StockDaily(code="300002", name="创业B", trade_date=TODAY, change_pct=-19.8))

    regime = MarketRegimeAnalyzer({"database": {"sqlite_path": db_path}}).analyze(overview={})
    assert regime.metrics["limit_down"] == 36
    assert (regime.regime, regime.position_factor, regime.emotion_cycle) == ("冰点", 0.0, "冰点")
    _reset_db_engine()


def test_too_few_stocks_is_unknown_and_factors_are_configurable(tmp_path):
    db_path = _init(tmp_path)
    with get_db_session(db_path) as session:
        for i in range(50):
            session.add(StockDaily(code=f"{600000 + i}", trade_date=TODAY, change_pct=1.0))
    config = {"database": {"sqlite_path": db_path}, "risk": {"market_regime_position": {"均衡": 0.5}}}
    analyzer = MarketRegimeAnalyzer(config)
    assert analyzer.analyze().regime == "未知"
    assert analyzer.position_factors["均衡"] == 0.5 and analyzer.position_factors["进攻"] == 1.0
    _reset_db_engine()


def test_order_budget_follows_regime(tmp_path, monkeypatch):
    db_path = _init(tmp_path)
    monkeypatch.setattr(StockInfoCollector, "refresh_if_stale", lambda self, path, max_age_hours=24: 0)
    with get_db_session(db_path) as session:
        session.add(StockDaily(code="600519", name="样本", trade_date=TODAY, close=50.0))
        session.add(TradeSignal(code="600519", name="样本", signal_date=TODAY, signal_type="premarket",
                                ai_verdict="买入", signal_strength=1.0, composite_score=90))
    config = {"database": {"sqlite_path": db_path}, "risk": {}, "trading": {"default_order_budget": 100_000}}

    monkeypatch.setattr(regime_mod.MarketRegimeAnalyzer, "analyze",
                        lambda self, **kw: MarketRegime(trade_date=TODAY, regime="冰点", position_factor=0.0))
    assert ExecutionService(config).prepare_orders(TODAY) == []  # 冰点暂停新开仓

    monkeypatch.setattr(regime_mod.MarketRegimeAnalyzer, "analyze",
                        lambda self, **kw: MarketRegime(trade_date=TODAY, regime="防守", position_factor=0.3))
    service = ExecutionService(config)
    order_id = service.prepare_orders(TODAY)[0]
    order = next(o for o in service.list_orders() if o["id"] == order_id)
    assert order["quantity"] == 600  # 10 万 × 0.3 / 50 元
    assert "大盘防守，仓位×0.3" in order["risk_note"]
    _reset_db_engine()


def test_emotion_cycle_rules():
    from src.analyzers.market_regime import DayStats

    cycle = MarketRegimeAnalyzer._emotion_cycle
    prev = DayStats(limit_up=50, max_height=5)
    assert cycle(DayStats(limit_up=120, max_height=6), prev, 3.5) == "高潮"
    assert cycle(DayStats(limit_up=60, max_height=5, limit_down=5), prev, 1.5) == "升温"
    assert cycle(DayStats(limit_up=52, max_height=5, limit_down=21), prev, 0.55) == "震荡"  # 普跌日，涨停略多不算升温
    assert cycle(DayStats(limit_up=40, max_height=3), prev, -1.0) == "退潮"
    assert cycle(DayStats(limit_up=25, max_height=2), prev, 0.5) == "冰点"


def test_partial_previous_day_skips_amount_change(tmp_path):
    """前一天只有个股页按需补齐的少量日线时不比较成交额（曾得出「成交额 +44004%」），涨停相关指标照常计算"""
    db_path = _init(tmp_path)
    with get_db_session(db_path) as session:
        for i in range(400):
            session.add(StockDaily(code=f"{600000 + i}", name=f"S{i}", trade_date=TODAY, change_pct=2.0 if i < 300 else -2.0, amount=1e9))
        for i in range(20):
            session.add(StockDaily(code=f"{600000 + i}", name=f"S{i}", trade_date=PREV, change_pct=1.0, amount=1e9))
            session.add(LimitUpStock(code=f"{600000 + i}", name=f"S{i}", trade_date=PREV, continuous_days=1))
        session.add(LimitUpStock(code="600000", name="S0", trade_date=TODAY, continuous_days=2))
    m = MarketRegimeAnalyzer({"database": {"sqlite_path": db_path}}).analyze(overview=INDEX_UP).metrics
    assert m["amount_change_pct"] is None
    assert m["promotion_rate"] == 5.0
    _reset_db_engine()
