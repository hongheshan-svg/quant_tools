"""组合风险与提醒增强：技术指标提醒、接近止损、大盘转弱、组合风险报告。"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pandas as pd
import pytest

from src import trading_calendar
from src.analyzers import indicators
from src.analyzers import market_regime as regime_mod
from src.collectors import daily_history as daily_history_mod
from src.database import db as db_module
from src.database.db import get_db_session, init_db
from src.database.models import LimitUpStock, StockDaily, TradeFill, TradeOrder
from src.services import alert_service as alert_mod
from src.services.alert_service import AlertService
from src.services.portfolio_risk import PortfolioRiskService, drawdowns, replay_nav

TODAY = date.today()
TODAY_STR = TODAY.strftime("%Y-%m-%d")
# 截至今天的 60 个工作日（最后一天是今天，作为实时行）
DAYS = [d.strftime("%Y-%m-%d") for d in pd.bdate_range(end=TODAY, periods=60)]
if DAYS[-1] != TODAY_STR:  # 今天是周末时也把今天当作最后一根
    DAYS = DAYS[1:] + [TODAY_STR]


def _reset_db_engine():
    if db_module._engine:
        db_module._engine.dispose()
    db_module._engine = None
    db_module._SessionFactory = None


def _declining_then_jump(start: float = 20.0, jump: float = 1.08) -> list[float]:
    closes = [start - 0.15 * i for i in range(len(DAYS) - 1)]
    return closes + [closes[-1] * jump]


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = str(tmp_path / "pa.db")
    _reset_db_engine()
    init_db(path)
    alert_mod.reset_state()
    monkeypatch.setattr(trading_calendar, "_trade_days", set())            # 按周一至周五判断
    monkeypatch.setattr(trading_calendar, "in_trade_session", lambda now=None: True)
    monkeypatch.setattr(daily_history_mod, "ensure_daily_history", lambda *a, **k: 0)
    monkeypatch.setattr(AlertService, "_positions", lambda self: [])
    yield path
    alert_mod.reset_state()
    _reset_db_engine()


def _add_series(path: str, code: str, closes: list[float], name: str = ""):
    with get_db_session(path) as session:
        prev = None
        for day, c in zip(DAYS[-len(closes):], closes):
            change = (c / prev - 1) * 100 if prev else 0.0
            session.add(StockDaily(code=code, name=name or code, trade_date=day, close=c, high=c * 1.005, low=c * 0.995, change_pct=change))
            prev = c


def _service(path: str, rules: list[dict], **alerts) -> AlertService:
    return AlertService({"database": {"sqlite_path": path}, "alerts": {"rules": rules, "market_regime": False, **alerts}, "notifier": {}})


# ---------- 技术指标提醒 ----------

def test_indicator_rules(db_path):
    closes = _declining_then_jump()
    _add_series(db_path, "600001", closes, "指标股")
    dif, dea = indicators.macd(closes)
    assert dif[-2] <= dea[-2] and dif[-1] > dea[-1]                         # 测试数据本身在最后一天金叉
    k, d, _ = indicators.kdj([c * 1.005 for c in closes], [c * 0.995 for c in closes], closes)
    assert k[-2] <= d[-2] and k[-1] > d[-1]

    rules = [
        {"code": "600001", "type": "ma_cross", "period": 5, "direction": "above"},
        {"code": "600001", "type": "macd_cross", "direction": "golden"},
        {"code": "600001", "type": "kdj_cross"},
        {"code": "600001", "type": "macd_cross", "direction": "dead"},        # 不触发
        {"code": "600001", "type": "rsi", "period": 6, "direction": "below", "value": 20},  # 最后一天大涨，不会下穿
    ]
    from src.services.alert_service import validate_rule
    events = [e for e in _service(db_path, rules).evaluate() if e.rule_id]
    assert [(e.alert_type, e.rule_id) for e in events] == [(rule["type"], validate_rule(rule)["id"]) for rule in rules[:3]]
    assert "价格上穿 MA5" in events[0].message and "MACD 金叉" in events[1].message and "KDJ 金叉" in events[2].message


def test_rsi_cross_and_insufficient_bars(db_path, monkeypatch):
    closes = [10.0 + (0.1 if i % 2 else -0.1) for i in range(len(DAYS) - 1)]
    closes.append(closes[-1] * 1.09)
    _add_series(db_path, "000002", closes)
    now = indicators.rsi(closes, 6)
    assert indicators.rsi(closes[:-1], 6) <= 70 < now
    rule = [{"code": "000002", "type": "rsi", "period": 6, "value": 70}]
    assert [e.alert_type for e in _service(db_path, rule).evaluate()] == ["rsi"]

    # 日线不足：先尝试补齐，补不到就跳过
    calls = []
    monkeypatch.setattr(daily_history_mod, "ensure_daily_history", lambda code, db_path, **k: calls.append(code) or 0)
    _add_series(db_path, "300003", [10.0, 10.5])
    assert _service(db_path, [{"code": "300003", "type": "macd_cross"}]).evaluate() == [] and calls == ["300003"]


def test_indicator_alert_once_per_day(db_path):
    _add_series(db_path, "600001", _declining_then_jump(), "指标股")
    service = _service(db_path, [{"code": "600001", "type": "ma_cross", "period": 5}], cooldown_minutes=30)
    t = datetime.combine(TODAY, datetime.min.time()).replace(hour=10)
    assert service.run(t)["alerts"] == 1
    assert service.run(t + timedelta(hours=2))["alerts"] == 0   # 超过 30 分钟冷却也不重复，指标交叉每天一次


# ---------- 接近止损 ----------

def test_near_stop_warning(db_path, monkeypatch):
    _add_series(db_path, "600001", [10.0, 9.75], "持仓股")
    monkeypatch.setattr(AlertService, "_positions", lambda self: [{"code": "600001", "name": "持仓股", "stop_loss": 9.6, "target_price": 12.0}])
    events = _service(db_path, [], near_stop_pct=2).evaluate()
    assert [(e.alert_type, e.severity) for e in events] == [("near_stop", "warning")]
    assert "还差 1.6%" in events[0].message
    assert _service(db_path, [], near_stop_pct=1).evaluate() == []


# ---------- 大盘转弱 ----------

def _regime(name: str, score: float, trade_date: str, factor: float = 0.3):
    return SimpleNamespace(regime=name, score=score, trade_date=trade_date, position_factor=factor)


def test_market_regime_alerts(db_path, monkeypatch):
    yesterday = (TODAY - timedelta(days=1)).strftime("%Y-%m-%d")
    state = {"now": _regime("防守", 35, TODAY_STR), "prev": _regime("均衡", 55, yesterday)}
    calls = []

    def analyze(self, trade_date=None, overview=None):
        calls.append((trade_date, overview))
        return state["prev"] if trade_date else state["now"]

    monkeypatch.setattr(regime_mod.MarketRegimeAnalyzer, "analyze", analyze)
    service = _service(db_path, [], market_regime=True)
    events = service.evaluate()
    assert [(e.code, e.alert_type, e.severity) for e in events] == [("market", "regime_down", "warning")]
    assert "由「均衡」转为「防守」（55→35分）" in events[0].message
    assert calls == [(None, {}), (yesterday, {})]                      # 两天同口径（不用实时指数缓存）

    state["now"] = _regime("冰点", 10, TODAY_STR, 0.0)
    assert service.evaluate()[0].severity == "critical"
    state["now"], state["prev"] = _regime("均衡", 45, TODAY_STR), _regime("均衡", 62, yesterday)
    assert [e.alert_type for e in service.evaluate()] == ["regime_score_drop"]
    state["now"] = _regime("均衡", 55, TODAY_STR)
    assert service.evaluate() == []
    state["now"] = _regime("防守", 35, yesterday)                       # 今天还没有行情：不比较
    assert service.evaluate() == []
    assert _service(db_path, [], market_regime=False).evaluate() == []


# ---------- 组合风险 ----------

def test_replay_nav_and_drawdown():
    fills = [("600001", "buy", 10.0, 1000, datetime(2026, 9, 1, 10)), ("600001", "sell", 12.0, 1000, datetime(2026, 9, 3, 10))]
    closes = {"600001": {"2026-09-01": 11.0, "2026-09-02": 9.0}}
    nav = replay_nav(fills, closes, ["2026-09-01", "2026-09-02", "2026-09-03"], 100_000)
    assert nav == [("2026-09-01", 101_000.0), ("2026-09-02", 99_000.0), ("2026-09-03", 102_000.0)]
    assert drawdowns(nav) == {"max_drawdown": pytest.approx(-1.98, abs=0.01), "max_drawdown_date": "2026-09-02", "current_drawdown": 0.0}


class _FakeExecution:
    broker = SimpleNamespace(name="paper", _initial_cash=100_000.0)

    def get_trading_snapshot(self, order_limit=1):
        return {
            "account": {"cash": 30_000.0, "market_value": 70_000.0, "total_assets": 100_000.0},
            "positions": [
                {"code": "600001", "name": "重仓股", "quantity": 4500, "avg_cost": 10.0, "market_price": 10.0,
                 "market_value": 45_000.0, "unrealized_pnl": 0.0, "stop_loss": 9.8, "target_price": 12.0},
                {"code": "000002", "name": "破位股", "quantity": 2500, "avg_cost": 11.0, "market_price": 10.0,
                 "market_value": 25_000.0, "unrealized_pnl": -2500.0, "stop_loss": 10.2, "target_price": 13.0},
            ],
        }


def test_portfolio_risk_report(db_path, monkeypatch):
    with get_db_session(db_path) as session:
        session.add(LimitUpStock(code="600001", name="重仓股", trade_date=DAYS[-5], sector="半导体"))
        session.add(TradeOrder(id="o1", signal_date=DAYS[-3], code="600001", name="重仓股", side="buy", order_type="limit",
                               price=10.0, quantity=4500, amount=45_000, status="filled", broker="paper", idempotency_key="k1"))
        session.add(TradeFill(order_id="o1", code="600001", side="buy", price=10.0, quantity=4500,
                              filled_at=datetime.strptime(DAYS[-3], "%Y-%m-%d").replace(hour=10)))
    _add_series(db_path, "600001", [10.0, 11.0, 10.0])
    monkeypatch.setattr(PortfolioRiskService, "_regime_limit", lambda self: ("防守", 30.0))

    report = PortfolioRiskService({"database": {"sqlite_path": db_path}}, execution=_FakeExecution()).report()
    assert report["exposure"] == 70.0 and report["suggested_exposure"] == 30.0
    positions = {p["code"]: p for p in report["positions"]}
    assert positions["600001"]["sector"] == "半导体" and positions["600001"]["weight"] == 45.0
    assert positions["600001"]["status"] == "接近止损" and positions["600001"]["stop_gap"] == pytest.approx(2.04, abs=0.01)
    assert positions["000002"]["status"] == "已跌破止损" and positions["000002"]["pnl_pct"] == pytest.approx(-9.09, abs=0.01)
    assert report["sectors"] == [{"sector": "半导体", "weight": 45.0}, {"sector": "未知", "weight": 25.0}]
    assert report["warnings"] == [
        "总仓位 70% 高于大盘「防守」环境建议的 30%",
        "重仓股 距止损价仅 2.0%",
        "重仓股 占总资产 45%，超过单只上限 30%",
        "破位股 已跌破止损价 10.20",
    ]
    # 净值：10.0 买入后 100000 → 104500 → 100000，最大回撤 -4.3% 发生在最后一天
    assert report["drawdown"]["days"] == 3 and report["drawdown"]["max_drawdown"] == pytest.approx(-4.3, abs=0.01)
