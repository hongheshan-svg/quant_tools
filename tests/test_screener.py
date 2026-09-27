from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from src.database import db as db_module
from src.database.db import get_db_session, init_db
from src.database.models import LimitUpStock, StockDaily, StrategyPick
from src.services import premarket_predictor as predictor_mod
from src.strategy import screener as screener_mod
from src.strategy.screener import StrategyScreener, compute_features


def _reset_db_engine():
    if db_module._engine:
        db_module._engine.dispose()
    db_module._engine = None
    db_module._SessionFactory = None


def _trade_days(start: date, count: int) -> list[str]:
    days, d = [], start
    while len(days) < count:
        if d.weekday() < 5:
            days.append(d.strftime("%Y-%m-%d"))
        d += timedelta(days=1)
    return days


# 2026-06-29 ~ 2026-09-21 共 61 个交易日（期间没有节假日），最后一天为选股日
DAYS = _trade_days(date(2026, 6, 29), 61)
TRADE_DATE = DAYS[-1]
NEXT_DAY = "2026-09-22"


def _bar(close, change=0.0, volume=1e6, amount=1e8, high=None, low=None, turnover=3.0):
    return {"close": close, "change_pct": change, "volume": volume, "amount": amount, "turnover": turnover,
            "open": close, "high": high or round(close * 1.01, 2), "low": low or round(close * 0.99, 2)}


def _breakout(last_change=6.86, last_close=10.9):
    bars = [_bar(10.0 if i % 2 == 0 else 10.2) for i in range(60)]
    bars.append(_bar(last_close, last_change, volume=3e6, amount=3e8, high=10.95, low=10.25, turnover=8.0))
    return bars


def _dragon():
    closes = [10.0] * 45 + [11.0, 12.1, 12.5] + [12.3, 12.1, 11.9, 11.8, 11.7, 11.6, 11.5, 11.4, 11.3, 11.2, 11.1, 11.1]
    changes = [0.0] * 45 + [10.0, 10.0, 3.3] + [-1.0] * 12
    bars = [_bar(c, ch) for c, ch in zip(closes, changes)]
    bars[47]["high"] = 12.8
    bars.append(_bar(11.0, -0.9, volume=5e5, amount=5e7, high=11.1, low=10.9))  # 缩量：成交额为前 5 日的一半
    return bars


def _oversold():
    closes = [20.0] * 40 + [20 - 0.3 * i for i in range(20)]
    bars = [_bar(c, -1.5 if i >= 40 else 0.0) for i, c in enumerate(closes)]
    bars.append(_bar(14.7, 5.0, volume=2.5e6, amount=2.5e8, high=14.75, low=14.0))
    return bars


def _trend():
    closes = [round(10 * 1.005 ** i, 3) for i in range(60)]
    bars = [_bar(c, 0.5, amount=1.5e8) for c in closes]
    bars.append(_bar(round(closes[-1] * 0.998, 3), -0.2, volume=6e5, amount=9e7))
    return bars


def _theme():
    closes = [10.0] * 50 + [11.0] + [10.9] * 9
    bars = [_bar(c, 10.0 if i == 50 else 0.0) for i, c in enumerate(closes)]
    bars.append(_bar(11.34, 4.0, volume=1.2e6, amount=1.5e8))
    return bars


STOCKS = {
    "600001": ("突破股", _breakout()),
    "600002": ("*ST突破", _breakout()),                  # ST：股票池排除
    "600003": ("龙回头", _dragon()),
    "000004": ("超跌股", _oversold()),
    "000005": ("趋势股", _trend()),
    "600006": ("补涨股", _theme()),
    "600007": ("高价股", [{**b, "close": b["close"] * 15} for b in _breakout()]),  # 价格超出股票池范围
    "600008": ("涨停股", _breakout(last_change=10.0, last_close=11.22)),         # 已涨停：不进突破/强势未板
}


@pytest.fixture
def config(tmp_path):
    path = str(tmp_path / "screen.db")
    _reset_db_engine()
    init_db(path)
    with get_db_session(path) as session:
        for code, (name, bars) in STOCKS.items():
            db_code = f"sh{code}" if code == "600001" else code  # 混合代码格式
            for day, bar in zip(DAYS, bars):
                session.add(StockDaily(code=db_code, name=name, trade_date=day, **bar))
        # 主线：今天 4 家「海峡两岸」涨停；补涨股 10 个交易日前因该题材涨停
        for i in range(4):
            session.add(LimitUpStock(code=f"60010{i}", name=f"题材{i}", trade_date=TRADE_DATE, concepts="海峡两岸", continuous_days=1))
        session.add(LimitUpStock(code="600006", name="补涨股", trade_date=DAYS[50], concepts="海峡两岸+工程机械", sector="水泥建材"))
    yield {"database": {"sqlite_path": path}}
    _reset_db_engine()


def test_compute_features_basics():
    bars = [SimpleNamespace(**_bar(10.0, volume=None, amount=1e8)) for _ in range(20)]
    bars.append(SimpleNamespace(**{**_bar(10.5, 5.0, volume=None, amount=2e8), "high": None, "low": None}))
    f = compute_features("300001", "测试", bars)
    assert f.limit_pct == 20.0 and not f.is_limit_up        # 创业板涨停 20%
    assert f.vol_ratio == pytest.approx(2.0)                # 成交额比（各数据源成交量单位不一致）
    assert f.close_pos is None and f.ma20 == pytest.approx(10.025)
    assert f.high_20 == pytest.approx(10.1) and f.ret_20 == pytest.approx(5.0)
    assert compute_features("300001", "测试", bars[1:]).high_20 is None  # 前 20 日不足 20 根
    assert compute_features("600000", "", [SimpleNamespace(**_bar(0))]) is None


def test_screen_strategies_and_regime_order(config, monkeypatch):
    monkeypatch.setattr(StrategyScreener, "_regime", lambda self, d, point_in_time=False: "防守")
    result = StrategyScreener(config).run(TRADE_DATE)

    picks = {p.code: p for p in result.picks}
    assert set(picks) == {"600001", "600003", "000004", "000005", "600006"}
    assert picks["600001"].labels == ["放量突破", "强势未板"]
    assert picks["600001"].score == pytest.approx(90.0)     # 85 + 多策略加分 5
    assert picks["600003"].labels == ["龙回头"] and "近 15 日 2 次涨停" in picks["600003"].reasons[0]
    assert picks["000004"].labels == ["超跌反弹"]
    assert picks["000005"].labels == ["缩量回踩"]
    assert picks["600006"].labels == ["主线补涨"] and "海峡两岸" in picks["600006"].reasons[0]

    # 防守环境：缩量回踩、超跌反弹适配，排在前面
    assert {p.code for p in result.picks[:2]} == {"000004", "000005"}
    assert all(p.fits_regime for p in result.picks[:2]) and not any(p.fits_regime for p in result.picks[2:])
    assert result.stats["universe"] == 8 and result.stats["pool"] == 6 and result.stats["with_history"] == 6
    assert any("不是全市场" in n for n in result.notes)
    assert "超跌反弹" in result.prompt_lines()[0] or "缩量回踩" in result.prompt_lines()[0]
    assert "与当前大盘环境不匹配" in result.prompt_lines()[-1]

    with get_db_session(config["database"]["sqlite_path"]) as session:
        assert session.query(StrategyPick).filter(StrategyPick.code == "600001").count() == 2
    StrategyScreener(config).run(TRADE_DATE)  # 重新选股覆盖当天结果
    with get_db_session(config["database"]["sqlite_path"]) as session:
        assert session.query(StrategyPick).count() == 6


def test_strategy_overrides(config, monkeypatch):
    monkeypatch.setattr(StrategyScreener, "_regime", lambda self, d, point_in_time=False: "")
    config["screening"] = {"strategies": {"volume_breakout": {"vol_ratio_min": 5}, "oversold_rebound": {"enabled": False}}}
    result = StrategyScreener(config).run(TRADE_DATE, save=False)
    picks = {p.code: p for p in result.picks}
    assert picks["600001"].labels == ["强势未板"] and "000004" not in picks
    assert all(p.fits_regime for p in result.picks)          # 大盘环境未知时都按适配处理
    assert "大盘环境未知，所有策略按适配处理" in result.notes


def test_latest_and_next_day_performance(config, monkeypatch):
    monkeypatch.setattr(StrategyScreener, "_regime", lambda self, d, point_in_time=False: "均衡")
    screener = StrategyScreener(config)
    screener.run(TRADE_DATE)
    assert all(p["next_change_pct"] is None for p in screener.latest())

    with get_db_session(config["database"]["sqlite_path"]) as session:
        session.add(StockDaily(code="600001", name="突破股", trade_date=NEXT_DAY, close=11.99, change_pct=10.0))
        session.add(StockDaily(code="000005", name="趋势股", trade_date=NEXT_DAY, close=11.0, change_pct=-1.5))
    latest = {p["code"]: p for p in screener.latest()}
    assert latest["600001"]["next_change_pct"] == 10.0 and latest["600001"]["labels"] == ["放量突破", "强势未板"]
    assert latest["600001"]["score"] == pytest.approx(90.0)  # 与选股时的合并得分一致
    assert latest["000005"]["fits_regime"] is True

    perf = {r["strategy"]: r for r in screener.performance(lookback_days=3650)}
    assert perf["volume_breakout"] == {"strategy": "volume_breakout", "label": "放量突破", "regimes": "进攻/均衡", "picks": 1,
                                       "evaluated": 1, "avg_next_pct": 10.0, "win_rate": 100.0, "limit_up_rate": 100.0}
    assert perf["trend_pullback"]["avg_next_pct"] == -1.5 and perf["trend_pullback"]["win_rate"] == 0.0
    assert perf["dragon_pullback"]["picks"] == 1 and perf["dragon_pullback"]["evaluated"] == 0


def test_empty_db(tmp_path):
    _reset_db_engine()
    path = str(tmp_path / "empty.db")
    init_db(path)
    result = StrategyScreener({"database": {"sqlite_path": path}}).run()
    assert result.picks == [] and result.notes == ["没有行情数据，请先采集"]
    assert StrategyScreener({"database": {"sqlite_path": path}}).latest() == []
    _reset_db_engine()


def test_predictor_uses_screener_only_after_close(monkeypatch):
    monkeypatch.setattr(screener_mod.StrategyScreener, "__init__", lambda self, config=None: None)
    monkeypatch.setattr(screener_mod.StrategyScreener, "run", lambda self: "result")
    predictor = SimpleNamespace(config={})
    get = predictor_mod.LimitUpPredictor._get_strategy_picks
    assert get(predictor, "aftermarket") == "result" and get(predictor, "premarket") == "result"
    assert get(predictor, "morning") is None                  # 盘中成交量不完整
    assert get(SimpleNamespace(config={"screening": {"enabled": False}}), "aftermarket") is None
