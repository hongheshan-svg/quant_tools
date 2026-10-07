"""v1.2.0 对齐复核探针：临时库、合成日历、禁网，不修改业务数据。

从仓库根目录执行：PYTHONPATH=. .venv/bin/python docs/audits/dsa-v1.2.0-review-2026-10-07/offline_probe.py
输出记录的是审计基线的实际行为，不是修复后应当保持的行为。
"""
import json
import socket
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from src import trading_calendar
from src.database import db as db_module
from src.database.db import get_db_session, init_db
from src.database.models import DecisionSignal, FundDaily, ResearchOutcome, SkillOpinion, StockDaily
from src.services import market_phase
from src.services.data_freshness import daily_quality
from src.services.decision_signals import DecisionSignalService
from src.services.outcome_engine import OutcomeEngine
from src.services.real_portfolio import RealPortfolioService
from src.services.research_artifact import local_context_pack
from src.services.skill_consult import SkillOpinionService
from src.services.stock_profile import StockProfileService
from src.services.stock_search import StockSearch
from src.services.web_intent import resolve


def blocked(*args, **kwargs):
    raise AssertionError("本探针禁止访问网络")


def main():
    results = {}
    with tempfile.TemporaryDirectory(prefix="quant-v120-audit-") as folder, pytest.MonkeyPatch.context() as patch:
        patch.setattr(socket.socket, "connect", blocked)
        patch.setattr(socket, "create_connection", blocked)
        if db_module._engine:
            db_module._engine.dispose()
        db_module._engine = db_module._SessionFactory = None
        path = str(Path(folder) / "audit.db")
        init_db(path)
        config = {"database": {"sqlite_path": path}, "risk": {}, "alerts": {"rules": []}}
        days = {(datetime(2026, 9, 1) + timedelta(days=i)).date().isoformat() for i in range(61)
                if (datetime(2026, 9, 1) + timedelta(days=i)).weekday() < 5}
        trading_calendar._set_days(days)
        original_current_phase = market_phase.current_phase
        phase = original_current_phase(datetime(2026, 9, 30, 16))
        patch.setattr(market_phase, "current_phase", lambda: phase)
        names = [('600519', '贵州茅台', (), 'stock'), ('000001', '平安银行', (), 'stock'),
                 ('601318', '中国平安', (), 'stock')]
        patch.setattr(StockSearch, "_ensure_index", lambda self: names)
        with get_db_session(path) as session:
            session.add(StockDaily(code="600519", trade_date="2026-09-25", close=10,
                                   source="fixture", price_adjustment="none", updated_at=datetime(2026, 9, 25, 16)))
            session.add(FundDaily(code="510300", name="测试ETF", trade_date="2026-09-30", close=4,
                                  source="fixture", price_adjustment="none", updated_at=datetime(2026, 9, 30, 16)))
        pack = local_context_pack("600519", path)
        results["shared_context_freshness"] = {
            "expected_quality": daily_quality("2026-09-25", datetime(2026, 9, 25, 16), phase=phase),
            "pack_phase": pack["phase"], "quote_status": pack["blocks"]["quote"]["status"],
            "daily_status": pack["blocks"]["daily"]["status"],
        }

        plan = resolve("分析贵州茅台和平安", path)
        confirmed = resolve("000001", path, plan["state"])
        results["mixed_ambiguity_confirmation"] = {
            "before_targets": plan["tasks"][0]["targets"],
            "candidates": plan["state"]["pending"]["candidates"],
            "after_tasks": confirmed["tasks"], "requires_confirmation": confirmed["requires_confirmation"],
        }

        real = RealPortfolioService(config)
        assert real.add_trade("2026-09-25", "510300", "buy", 3.5, 100, name="测试ETF")["ok"]
        positions = real.positions()
        profile = StockProfileService(SimpleNamespace(config=config, db_path=path))
        results["etf_profile_portfolio"] = {
            "ledger_position": [{k: p[k] for k in ("code", "quantity", "market_price", "price_quality")} for p in positions],
            "profile_portfolio": profile._portfolio("510300", "etf"),
        }
        with get_db_session(path) as session:
            session.add(DecisionSignal(code="600519", action="buy", status="active", trade_date="2026-09-01",
                                       expires_on="2026-09-10"))
        results["expired_signal_profile"] = {
            "profile": profile._signals("600519"),
            "risk_summary": DecisionSignalService(config).active_summary(["600519"], datetime(2026, 9, 30, 16)),
        }

        with get_db_session(path) as session:
            for i in range(30):
                session.add(SkillOpinion(code="600519", skill="volume_breakout", stance="看多", score=80,
                    confidence="高", trade_date=(datetime(2026, 8, 1) + timedelta(days=i)).date().isoformat(),
                    hit=True, ret_5d=5, skill_version=None))
        results["legacy_skill_weights"] = {
            "performance": SkillOpinionService(config).performance(),
            "weights": SkillOpinionService(config).weights(),
        }
        with get_db_session(path) as session:
            results["legacy_skill_weights"]["current_outcome_count"] = session.query(ResearchOutcome).count()

        # 20 条已结束信号仅 3 条可评估，复盘应明确揭示样本覆盖不足。
        current_version = OutcomeEngine(config).version
        with get_db_session(path) as session:
            for index in range(20):
                signal = DecisionSignal(code="000001", action="buy", status="expired",
                    trade_date="2026-09-01", expires_on="2026-09-10",
                    evaluation_version=current_version, ret_5d=5 if index < 3 else None)
                session.add(signal)
                session.flush()
                session.add(ResearchOutcome(owner_type="signal", owner_id=signal.id, horizon=5,
                    engine_version=current_version, status="evaluated" if index < 3 else "unable",
                    hit=True if index < 3 else None, return_pct=5 if index < 3 else None,
                    reason="" if index < 3 else "missing_trading_day"))
        results["review_memory_coverage"] = {"ended_signals": 20, "unjudgeable_signals": 17,
            "review": DecisionSignalService(config).review("000001")}

        # 合成日历后来明确 9/28 为非交易日；这里不声称这是真实交易所日历。
        with get_db_session(path) as session:
            for day, close in [("2026-09-28", 11), ("2026-09-29", 12)]:
                session.add(StockDaily(code="600519", trade_date=day, close=close,
                    source="fixture", price_adjustment="none", updated_at=datetime.fromisoformat(day + "T16:00:00")))
        trading_calendar._set_days(set())
        engine = OutcomeEngine(config)
        with get_db_session(path) as session:
            outcome = engine.evaluate(session, "diagnosis", 123, "600519", "2026-09-25", 1,
                                      now=datetime(2026, 9, 30, 16), horizons=(1,))[0]
            before = {k: getattr(outcome, k) for k in ("status", "end_date", "return_pct", "data_quality_json")}
        trading_calendar._set_days({"2026-09-25", "2026-09-29", "2026-09-30"})
        with get_db_session(path) as session:
            outcome = engine.evaluate(session, "diagnosis", 123, "600519", "2026-09-25", 1,
                                      now=datetime(2026, 9, 30, 16), horizons=(1,))[0]
            results["unverified_calendar_freezes_outcome"] = {"before": before,
                "after_verified_calendar": {k: getattr(outcome, k) for k in ("status", "end_date", "return_pct")}}

        # 调用原函数，比较同一瞬间的 UTC 与上海时区输入；全程保持禁网。
        instant = datetime(2026, 9, 30, 8, tzinfo=timezone.utc)
        results["market_timezone"] = {"utc": original_current_phase(instant),
            "shanghai": original_current_phase(instant.astimezone(ZoneInfo("Asia/Shanghai")))}
        db_module._engine.dispose()
        db_module._engine = db_module._SessionFactory = None
    output = Path("/tmp/quant-v1.2.0-audit-results.json")
    output.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(output.read_text())


if __name__ == "__main__":
    main()
