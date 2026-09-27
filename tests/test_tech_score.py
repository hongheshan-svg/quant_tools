from __future__ import annotations

from src.database import db as db_module
from src.database.db import get_db_session, init_db
from src.database.models import StockDaily
from src.strategy.tech_score import _rsi, analyze_series, analyze_technical, calculate_tech_score


def _changes(closes: list[float]) -> list[float]:
    return [0.0] + [(b - a) / a * 100 for a, b in zip(closes[:-1], closes[1:])]


def test_insufficient_data_is_neutral():
    result = analyze_series([10.0, 10.1, 10.2], [1, 1, 1], [0, 1, 1])
    assert result.score == 50.0
    assert result.brief() == ""


def _zigzag(start: float, up: float, down: float, n: int = 60) -> list[float]:
    """每 3 天两涨一跌（或两跌一涨）的序列，比单边直线更接近真实走势。"""
    closes, price = [], start
    for i in range(n):
        price *= up if i % 3 else down
        closes.append(price)
    return closes


def test_uptrend_scores_high_and_flags_overheated_rsi():
    closes = _zigzag(10.0, 1.02, 0.99)
    result = analyze_series(closes, [1000.0 + 10 * i for i in range(60)], _changes(closes))

    assert (result.trend, result.macd) == ("多头排列", "MACD多头")
    assert "均线多头排列" in result.reasons
    assert any("偏热" in r for r in result.risks)  # RSI6≈84，打板策略下只提示偏热
    assert result.score > 70


def test_downtrend_scores_low_with_risks():
    closes = _zigzag(20.0, 0.98, 1.01)
    result = analyze_series(closes, [1000.0] * 60, _changes(closes))

    assert result.trend == "空头排列"
    assert "均线空头排列" in result.risks
    assert result.score < 40


def test_golden_cross_after_pullback():
    closes = [10 * 0.995 ** i for i in range(45)]
    closes.append(closes[-1] * 1.03)
    result = analyze_series(closes, [1000.0] * len(closes), _changes(closes))
    assert result.macd == "MACD金叉"
    assert "MACD金叉" in result.reasons


def test_limit_up_far_above_ma5_is_penalised_but_not_disqualified():
    base = [10.0] * 55
    closes = base + [11.0, 12.1, 13.31, 14.64, 16.1]  # 连续涨停
    far = analyze_series(closes, [1000.0] * 60, _changes(closes))

    assert far.bias_ma5 > 15
    assert any("高位追涨风险大" in r for r in far.risks)
    assert far.score > 40  # 只扣分，不一票否决


def test_rsi_matches_wilder_reference():
    closes = [44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42, 45.84, 46.08, 45.89, 46.03, 45.61, 46.28, 46.28]
    assert round(_rsi(closes, 14), 2) == 70.46  # Wilder 原书示例


def test_reads_history_from_db_and_accepts_prefixed_codes(tmp_path):
    db_path = str(tmp_path / "tech.db")
    if db_module._engine:
        db_module._engine.dispose()
    db_module._engine = None
    db_module._SessionFactory = None
    init_db(db_path)
    closes = [10 * 1.006 ** i for i in range(60)]
    with get_db_session(db_path) as session:
        for i, c in enumerate(closes):
            trade_date = f"2026-{7 + i // 28:02d}-{i % 28 + 1:02d}"
            session.add(StockDaily(code="sh600519", name="样本", trade_date=trade_date, close=c, volume=1000.0, change_pct=0.6))

    assert analyze_technical("600519", db_path).trend == "多头排列"
    assert calculate_tech_score("600519", db_path) == analyze_series(closes, [1000.0] * 60, [0.6] * 60).score
    assert calculate_tech_score("000000", db_path) == 50.0
    db_module._engine.dispose()
    db_module._engine = None
    db_module._SessionFactory = None
