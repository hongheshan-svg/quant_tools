"""第二次深查的隔离复现；仅使用临时 SQLite、合成行情和假模型。

从仓库根目录运行：.venv/bin/python docs/audits/dsa-deep-followup-2026-10-07/offline_probe.py
结果写入 /tmp/quant-dsa-deep-followup-results.json；断言记录当前缺陷，不是业务验收测试。
"""

import json
import socket
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path.cwd()))

from loguru import logger
from pytest import MonkeyPatch

from src import trading_calendar
from src.database import db
from src.database.db import get_db_session, init_db
from src.database.models import DecisionSignal, ResearchOutcome, SkillOpinion, StockDaily, StockDiagnosis
from src.services.data_query_service import DataQueryService
from src.services.decision_signals import DecisionSignalService
from src.services.outcome_engine import OutcomeEngine
from src.services.run_log import RunLog, execution_trace
from src.services.skill_consult import SkillOpinionService
from src.services.stock_diagnosis import StockDiagnosisService
from src.services.strategy_synthesis import synthesize

logger.remove()
logger.add(sys.stderr, level="WARNING")
RESULTS = {}


def reset():
    if db._engine is not None:
        db._engine.dispose()
    db._engine = db._SessionFactory = None


def no_network(*args, **kwargs):
    raise AssertionError("离线审计禁止联网")


def bars(path, rows):
    with get_db_session(path) as session:
        for day, close, basis in rows:
            session.add(StockDaily(
                code="600519", name="合成行情", trade_date=day,
                open=close, high=close, low=close, close=close,
                source="audit_fixture", price_adjustment=basis,
                updated_at=datetime.fromisoformat(day + "T16:00:00"),
            ))


def add_signal(path, target=None):
    with get_db_session(path) as session:
        row = DecisionSignal(code="600519", trade_date="2026-09-14", action="buy",
                             status="active", horizon_days=5, expires_on="2026-09-21",
                             target_price=target)
        session.add(row)
        session.flush()
        return row.id


def output(row):
    return {"status": row.status, "reason": row.reason, "return_pct": row.return_pct,
            "end_date": row.end_date, "hit": row.hit}


def opinion(skill="volume_breakout", stance="看多", score=80, confidence="高"):
    return {"skill": skill, "display_name": skill, "stance": stance, "score": score,
            "confidence": confidence, "reason": "离线合成证据", "weight": 1.0}


with tempfile.TemporaryDirectory(prefix="quant-dsa-deep-") as folder, MonkeyPatch.context() as patch:
    patch.setattr(socket.socket, "connect", no_network)
    patch.setattr(socket, "create_connection", no_network)
    patch.setattr(trading_calendar, "load", lambda *a, **kw: True)
    # 只使用 9 月 14～22 日，合成日历完整覆盖该区间。
    start = datetime(2026, 9, 1)
    trading_calendar._set_days({(start + timedelta(days=i)).strftime("%Y-%m-%d")
                               for i in range(24) if (start + timedelta(days=i)).weekday() < 5})

    def fresh(name):
        reset()
        path = str(Path(folder) / (name + ".db"))
        init_db(path)
        return path, {"database": {"sqlite_path": path}, "risk": {}, "trading": {}}

    for case, end_day, end_price, end_basis, now, target in [
        ("signal_reads_future_bar", "2026-09-15", 12, "none", datetime(2026, 9, 14, 16), 11),
        ("signal_compresses_missing_day", "2026-09-16", 12, "none", datetime(2026, 9, 16, 16), None),
        ("signal_ignores_price_basis", "2026-09-15", 5, "forward", datetime(2026, 9, 15, 16), None),
    ]:
        path, config = fresh(case)
        bars(path, [("2026-09-14", 10, "none"), (end_day, end_price, end_basis)])
        sid = add_signal(path, target)
        service = DecisionSignalService(config)
        service.evaluate(now=now)
        legacy = service.get(sid)
        shared = next(row for row in OutcomeEngine(config).list("signal", sid) if row["horizon"] == 1)
        RESULTS[case] = {
            "as_of": now.isoformat(),
            "legacy": {key: legacy[key] for key in ("status", "status_reason", "ret_1d")},
            "legacy_is_hit": service.is_hit(legacy), "shared_1d": shared,
            "statistics_hit_rate": service.stats()["hit_rate"],
        }
    assert RESULTS["signal_reads_future_bar"]["legacy"]["status"] == "hit_target"
    assert RESULTS["signal_reads_future_bar"]["shared_1d"]["status"] == "pending"
    assert RESULTS["signal_compresses_missing_day"]["legacy"]["ret_1d"] == 20
    assert RESULTS["signal_compresses_missing_day"]["statistics_hit_rate"] == 100
    assert RESULTS["signal_compresses_missing_day"]["shared_1d"]["reason"] == "missing_trading_day"
    assert RESULTS["signal_ignores_price_basis"]["legacy"]["ret_1d"] == -50
    assert RESULTS["signal_ignores_price_basis"]["shared_1d"]["reason"] == "incomparable_price_basis"

    path, config = fresh("intraday_outcome")
    bars(path, [("2026-09-14", 10, "none"), ("2026-09-15", 12, "none")])
    engine = OutcomeEngine(config)
    with get_db_session(path) as session:
        partial = session.query(StockDaily).filter_by(trade_date="2026-09-15").one()
        partial.updated_at = datetime(2026, 9, 15, 11)
        session.flush()
        first = output(engine.evaluate(session, "skill", 1, "600519", "2026-09-14", 1,
                                       now=datetime(2026, 9, 15, 11), horizons=(1,))[0])
    with get_db_session(path) as session:
        final = session.query(StockDaily).filter_by(trade_date="2026-09-15").one()
        final.close = final.high = final.low = 9
        final.updated_at = datetime(2026, 9, 15, 16)
        session.flush()
        second = output(engine.evaluate(session, "skill", 1, "600519", "2026-09-14", 1,
                                        now=datetime(2026, 9, 15, 16), horizons=(1,))[0])
    RESULTS["intraday_outcome_frozen"] = {"intraday": first, "after_close": second,
                                          "final_close": 9, "expected_final_return_pct": -10}
    assert first["status"] == "evaluated" and second["return_pct"] == 20

    for deletion in ("single", "batch"):
        path, config = fresh("delete_" + deletion)
        days = ["2026-09-14", "2026-09-15", "2026-09-16", "2026-09-17", "2026-09-18", "2026-09-21"]
        bars(path, [(day, 10 + i, "none") for i, day in enumerate(days)])
        with get_db_session(path) as session:
            report = StockDiagnosis(code="600519", trade_date=days[0], action="buy")
            session.add(report)
            session.flush()
            did = report.id
        skills = SkillOpinionService(config)
        skills.record(did, "600519", "合成样本", days[0], [opinion()])
        skills.evaluate(now=datetime(2026, 9, 22, 16))
        query = DataQueryService(path)
        deleted = query.delete_diagnosis(did) if deletion == "single" else query.delete_diagnoses(ids=[did])
        with get_db_session(path) as session:
            counts = {"reports": session.query(StockDiagnosis).count(),
                      "samples": session.query(SkillOpinion).count(),
                      "outcomes": session.query(ResearchOutcome).count()}
        statistics = skills._stats(30)
        late_write = skills.record(did, "600519", "已删除的父报告", days[0], [opinion("bull_trend")])
        RESULTS["orphan_samples_" + deletion] = {"deleted": deleted, "after_delete": counts,
                                                 "statistics": statistics, "late_insert_count": late_write}
        assert counts == {"reports": 0, "samples": 1, "outcomes": 4}
        assert statistics["volume_breakout"]["samples"] == 1 and late_write == 1

    original = [opinion("volume_breakout", "看多", 80, "中"), opinion("bull_trend", "看空", 20, "中")]

    class AggressiveMediator:
        def chat_json(self, *args, **kwargs):
            return {"opinions": [opinion(o["skill"], "看多", 95, "高") for o in original]}

    baseline = synthesize(original)
    revised = synthesize(original, llm=AggressiveMediator(), config={"enabled": True, "max_rounds": 1})
    RESULTS["mediator_reversal_accepted"] = {
        "baseline_consensus": baseline["consensus"], "baseline_cap": baseline["confidence_cap"],
        "after_consensus": revised["consensus"], "after_cap": revised["confidence_cap"],
        "original": original, "revised": revised["revised_opinions"], "status": revised["deliberation"]["status"],
    }
    assert revised["consensus"]["score"] == 95 and revised["confidence_cap"] is None

    path, config = fresh("run_log_history")
    config["diagnosis"] = {"calibration": False, "mode": "single"}

    class FakeDecision:
        def chat_json(self, *args, **kwargs):
            return {"score": 50, "action": "watch", "confidence": "低"}

    service = StockDiagnosisService(config, llm=FakeDecision())
    # 保留实际 diagnose 编排、_save 和历史查询；隔离取数、护栏、信号副作用。
    patch.setattr(service, "build_context", lambda *a, **k: {
        "code": "600519", "name": "合成报告", "text": "离线审计", "quote": {"close": 10, "trade_date": "2026-09-14"}})
    patch.setattr(service, "_consult_skills", lambda *a, **k: ([], {}, ""))
    patch.setattr(service, "_record_opinions", lambda *a, **k: None)
    patch.setattr(service, "_record_signal", lambda *a, **k: None)
    patch.setattr(service, "_apply_guardrails", lambda raw, *a, **k: {
        **raw, "code": "600519", "name": "合成报告", "trade_date": "2026-09-14"})
    live = service.diagnose("600519", force=True)
    history = DataQueryService(path).get_diagnosis(live["diagnosis_id"])
    RESULTS["history_run_log_loses_save"] = {
        "live": [s["name"] for s in live["run_log"]["steps"]],
        "history_top_level": [s["name"] for s in history["run_log"]["steps"]],
        "history_result_json": [s["name"] for s in history["result"]["run_log"]["steps"]],
    }
    assert "报告保存" in RESULTS["history_run_log_loses_save"]["live"]
    assert "报告保存" not in RESULTS["history_run_log_loses_save"]["history_top_level"]

    def broken_sink(event):
        raise RuntimeError("audit sink unavailable")

    try:
        with execution_trace("offline-audit", broken_sink):
            RunLog().add("成功完成的步骤", True)
    except RuntimeError as error:
        RESULTS["run_log_sink_is_not_fail_open"] = {"propagated_error": str(error)}
    assert "run_log_sink_is_not_fail_open" in RESULTS
    reset()
    trading_calendar._set_days(set())

output_path = Path("/tmp/quant-dsa-deep-followup-results.json")
output_path.write_text(json.dumps(RESULTS, ensure_ascii=False, indent=2) + "\n")
print(json.dumps({"scenarios": len(RESULTS), "results_file": str(output_path), "results": RESULTS}, ensure_ascii=False, indent=2))
