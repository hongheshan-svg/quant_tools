from __future__ import annotations

from datetime import date

import pytest

from src.analyzers.decision import is_bullish, normalize_action, score_to_action
from src.database import db as db_module
from src.database.db import get_db_session, init_db
from src.database.models import StockDaily, StockInfo

TODAY = date.today().strftime("%Y-%m-%d")


@pytest.mark.parametrize(
    ("text", "action"),
    [
        ("买入", "buy"), ("强烈买入", "buy"), ("强烈推荐", "buy"), ("BUY", "buy"), ("加仓", "add"),
        ("持有观察", "hold"), ("观望，回踩再低吸", "watch"), ("等待确认", "watch"),
        ("不建议买入", "avoid"), ("暂不追高", "avoid"), ("回避", "avoid"), ("do not buy", "avoid"),
        ("减仓", "reduce"), ("卖出", "sell"), ("止损离场", "sell"), ("风险预警：高位放量", "alert"),
        ("", ""), ("今日看好", ""),
    ],
)
def test_normalize_action(text, action):
    assert normalize_action(text) == action


def test_bullish_and_score_scale():
    assert is_bullish("买入") and is_bullish("加仓") and not is_bullish("不建议买入") and not is_bullish("看好")
    assert [score_to_action(s) for s in (85, 60, 59, 40, 39, 19)] == ["buy", "buy", "watch", "watch", "reduce", "sell"]


def _reset_db_engine():
    if db_module._engine:
        db_module._engine.dispose()
    db_module._engine = None
    db_module._SessionFactory = None


def _predictor(db_path: str):
    from src.services.premarket_predictor import LimitUpPredictor

    return LimitUpPredictor({"database": {"sqlite_path": db_path}, "llm": {"cache_enabled": False}})


def test_predictions_drop_fabricated_st_and_duplicate_codes(tmp_path):
    db_path = str(tmp_path / "validate.db")
    _reset_db_engine()
    init_db(db_path)
    with get_db_session(db_path) as session:
        session.add(StockDaily(code="sh600519", name="贵州茅台", trade_date=TODAY, close=1500))
        session.add(StockDaily(code="000002", name="*ST样本", trade_date=TODAY, close=3))
        session.add(StockInfo(code="300750", name="宁德时代", exchange="sz"))

    result = _predictor(db_path)._validate_predictions([
        {"code": "600519", "name": "茅台", "confidence": 9},       # 名称以库里为准
        {"code": "300750.SZ", "name": "宁德时代", "confidence": 8},  # 带后缀的代码，库里只有股票列表
        {"code": "688999", "name": "编造股份", "confidence": 8},    # 不存在
        {"code": "000002", "name": "样本", "confidence": 7},        # ST
        {"code": "600519", "name": "贵州茅台", "confidence": 6},     # 重复
        "not a dict",
    ])
    assert [(p["code"], p["name"], p["confidence"]) for p in result] == [("600519", "贵州茅台", 9), ("300750", "宁德时代", 8)]
    _reset_db_engine()


def test_predictions_kept_when_database_is_empty(tmp_path):
    db_path = str(tmp_path / "empty.db")
    _reset_db_engine()
    init_db(db_path)
    predictions = [{"code": "600519", "name": "贵州茅台"}]
    assert _predictor(db_path)._validate_predictions(predictions) == predictions
    _reset_db_engine()
