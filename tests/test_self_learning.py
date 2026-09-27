import os
import sys
import tempfile
from pathlib import Path

# 确保项目根目录在路径中，支持直接运行 `python tests/test_self_learning.py`
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.database import db as db_module
from src.database.db import get_db_session, init_db
from src.database.models import (
    LearningSnapshot,
    LimitUpStock,
    SignalOutcome,
    StockDaily,
    StockScore,
    TradeSignal,
)
from src.services.self_learning import SelfLearningService
from src.strategy.scorer import CompositeScorer


def _reset_db_engine():
    if db_module._engine:
        db_module._engine.dispose()
    db_module._engine = None
    db_module._SessionFactory = None


def test_yizi_filter_no_crash(tmp_path):
    db_path = tmp_path / "unit_yizi.db"
    _reset_db_engine()
    init_db(str(db_path))

    trade_date = "2026-02-20"
    with get_db_session(str(db_path)) as session:
        session.add(
            LimitUpStock(
                code="000001",
                name="一字板样本",
                trade_date=trade_date,
                first_limit_time="09:25",
                open_count=0,
                continuous_days=1,
            )
        )
        session.add(
            LimitUpStock(
                code="000002",
                name="正常样本",
                trade_date=trade_date,
                first_limit_time="10:15",
                open_count=2,
                continuous_days=1,
            )
        )
        session.add(
            StockDaily(
                code="000001",
                name="一字板样本",
                trade_date=trade_date,
                open=10.0,
                high=10.0,
                low=10.0,
                close=10.0,
                change_pct=10.0,
            )
        )
        session.add(
            StockDaily(
                code="000002",
                name="正常样本",
                trade_date=trade_date,
                open=10.0,
                high=10.8,
                low=9.8,
                close=10.5,
                change_pct=6.0,
            )
        )

    scorer = CompositeScorer(
        {
            "database": {"sqlite_path": str(db_path)},
            "risk": {"blacklist_keywords": ["ST", "*ST"]},
            "strategy": {"weights": {}, "learning": {"enabled": False}},
        }
    )
    candidates = scorer._get_candidates(trade_date)
    codes = {row["code"] for row in candidates}

    assert "000001" not in codes
    assert "000002" in codes


def test_self_learning_updates_weights_and_confidence(tmp_path):
    db_path = tmp_path / "unit_learning.db"
    _reset_db_engine()
    init_db(str(db_path))

    d1, d2, d3 = "2026-02-18", "2026-02-19", "2026-02-20"
    base_weights = {
        "sentiment_score": 0.25,
        "limit_up_score": 0.15,
        "seal_strength": 0.15,
        "sector_effect": 0.12,
        "capital_flow": 0.10,
        "technical": 0.08,
        "market_emotion": 0.05,
        "global_score": 0.10,
    }

    with get_db_session(str(db_path)) as session:
        # 两个评分日：构造“舆情越高，次日涨幅越高；技术分反向”的样本
        for i in range(1, 6):
            code = f"{i:06d}"
            sentiment = 40 + i * 10      # 50..90
            technical = 100 - sentiment   # 50..10
            session.add(
                StockScore(
                    code=code,
                    name=f"样本{i}",
                    score_date=d1,
                    sentiment_score=sentiment,
                    limit_up_score=55,
                    capital_flow_score=50,
                    technical_score=technical,
                    global_score=50,
                    composite_score=sentiment,
                    recommendation="buy",
                )
            )
            session.add(
                StockScore(
                    code=code,
                    name=f"样本{i}",
                    score_date=d2,
                    sentiment_score=sentiment,
                    limit_up_score=55,
                    capital_flow_score=50,
                    technical_score=technical,
                    global_score=50,
                    composite_score=sentiment,
                    recommendation="buy",
                )
            )

            # d2: 用于评估 d1 的次日收益
            session.add(
                StockDaily(
                    code=code,
                    name=f"样本{i}",
                    trade_date=d2,
                    close=10 + i,
                    change_pct=-4 + i * 2.5,  # -1.5 .. 8.5
                )
            )
            # d3: 用于评估 d2 的次日收益
            session.add(
                StockDaily(
                    code=code,
                    name=f"样本{i}",
                    trade_date=d3,
                    close=11 + i,
                    change_pct=-3 + i * 2.0,  # -1 .. 7
                )
            )

        # 来源置信度样本：热点驱动表现好，全市场表现差
        session.add(
            TradeSignal(
                code="000005",
                name="样本5",
                signal_date=d2,
                signal_type="premarket",
                signal_strength=0.9,
                reason="##TYPE:热点龙头##TIME:明日开盘##SRC:热点驱动##样本",
                composite_score=90,
            )
        )
        session.add(
            TradeSignal(
                code="000001",
                name="样本1",
                signal_date=d2,
                signal_type="premarket",
                signal_strength=0.7,
                reason="##TYPE:弱势观察##TIME:明日观察##SRC:全市场##样本",
                composite_score=70,
            )
        )
        session.add(
            LimitUpStock(
                code="000005",
                name="样本5",
                trade_date=d2,
                continuous_days=1,
            )
        )

    service = SelfLearningService(
        {
            "_config_path": str(tmp_path / "settings.yaml"),
            "database": {"sqlite_path": str(db_path)},
            "strategy": {
                "weights": base_weights,
                "learning": {
                    "enabled": True,
                    "lookback_days": 10,
                    "min_samples": 3,
                    "source_min_samples": 1,
                    "persist_to_yaml": False,
                },
            },
        }
    )
    result = service.run_daily_learning(as_of_date=d3)

    assert result["status"] == "ok"
    assert result["adaptive_weights"]["sentiment_score"] > result["adaptive_weights"]["technical"]
    assert result["source_confidence"]["热点驱动"] >= result["source_confidence"]["全市场"]

    with get_db_session(str(db_path)) as session:
        assert session.query(SignalOutcome).count() >= 2
        assert session.query(LearningSnapshot).filter(LearningSnapshot.snapshot_date == d3).count() == 1

    _reset_db_engine()
    if os.path.exists(db_path):
        os.remove(db_path)


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as tmp:
        temp_path = Path(tmp)
        test_yizi_filter_no_crash(temp_path)
        print("[PASS] test_yizi_filter_no_crash")
        test_self_learning_updates_weights_and_confidence(temp_path)
        print("[PASS] test_self_learning_updates_weights_and_confidence")
        print("\n===== self_learning 测试通过 =====")
