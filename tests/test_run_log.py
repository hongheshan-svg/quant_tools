"""WS21：诊断运行日志 RunLog、诊断落库、评分趋势查询与接口。"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest

from src.collectors import fundamentals as fundamentals_mod
from src.collectors import stock_news as stock_news_mod
from src.database.db import get_db_session
from src.database.models import FundDaily, StockDaily, StockDiagnosis
from src.services.data_query_service import DataQueryService
from src.services.run_log import RunLog
from src.services.stock_diagnosis import StockDiagnosisService
from tests.test_api import env  # noqa: F401
from tests.test_stock_diagnosis import GOOD_REPLY, _service, db_path, offline_fundamentals  # noqa: F401


# ---------- RunLog 单元 ----------

def test_step_records_ok_and_ms():
    log = RunLog()
    with log.step("行情") as st:
        st.detail = "25 根"
    steps = log.to_dict()["steps"]
    assert len(steps) == 1
    s = steps[0]
    assert s["name"] == "行情" and s["ok"] is True and s["ms"] >= 0
    assert s["detail"] == "25 根" and s["kind"] == "data"


def test_step_exception_propagates_and_marks_failed():
    log = RunLog()
    with pytest.raises(ValueError):
        with log.step("筹码"):
            raise ValueError("x" * 500)
    s = log.to_dict()["steps"][0]
    assert s["ok"] is False and "x" in s["detail"] and len(s["detail"]) <= 200


def test_llm_note_add_and_to_dict():
    log = RunLog()
    log.llm("决策", "gpt-x", True, 1200)
    log.note("护栏：降级为观望")
    log.add("手动", True, 5, "d")
    d = log.to_dict()
    kinds = [s["kind"] for s in d["steps"]]
    assert kinds[:2] == ["llm", "note"] and "data" in kinds[2:]
    assert d["model"] == "gpt-x"
    assert d["total_ms"] >= 0
    assert set(d) >= {"steps", "total_ms", "model"}
    assert any("护栏" in str(s.get("name", "")) + str(s.get("detail", "")) for s in d["steps"] if s["kind"] == "note")


def test_max_60_steps():
    log = RunLog()
    for i in range(100):
        log.note(f"n{i}")
    assert len(log.to_dict()["steps"]) == 60


# ---------- 诊断集成 ----------

def _steps(result):
    return result["run_log"]["steps"]


def test_diagnose_has_run_log_and_persists(db_path):
    service, _ = _service(db_path, GOOD_REPLY)
    result = service.diagnose("002594", force=True)
    steps = _steps(result)
    data_steps = [s for s in steps if s["kind"] == "data"]
    assert len(data_steps) >= 3
    decision = [s for s in steps if s["name"] == "决策"]
    assert decision and decision[0]["kind"] == "llm"
    assert result["run_log"]["total_ms"] >= 0
    with get_db_session(db_path) as session:
        row = session.query(StockDiagnosis).one()
        assert row.run_log
        stored = json.loads(row.run_log)
    assert any(s["name"] == "决策" for s in stored["steps"])


def test_build_context_text_unchanged_by_run_log(db_path):
    service, _ = _service(db_path, {})
    plain = service.build_context("002594")["text"]
    logged = service.build_context("002594", RunLog())["text"] if _accepts_run_log(service) else None
    if logged is None:
        pytest.fail("build_context 不接受 run_log 参数")
    assert plain == logged


def _accepts_run_log(service):
    import inspect
    return "run_log" in inspect.signature(service.build_context).parameters


def test_protected_step_failure_does_not_break_diagnosis(db_path, monkeypatch):
    def boom(cls, code):
        raise RuntimeError("业绩接口挂了")
    monkeypatch.setattr(fundamentals_mod.EarningsCache, "get", classmethod(boom))
    result = _service(db_path, GOOD_REPLY)[0].diagnose("002594", force=True)
    assert not result.get("error")
    failed = [s for s in _steps(result) if s["ok"] is False]
    assert failed and any("业绩" in s["name"] for s in failed)

    def boom_news(code, refresh=False, now=None):
        raise RuntimeError("新闻挂了")
    monkeypatch.setattr(stock_news_mod, "get_stock_news", boom_news)
    result = _service(db_path, GOOD_REPLY)[0].diagnose("002594", force=True)
    assert not result.get("error")
    assert any(s["ok"] is False for s in _steps(result))


def test_guardrail_note_step(db_path):
    result = _service(db_path, {**GOOD_REPLY, "score": 45, "action": "买入"})[0].diagnose("002594", force=True)
    assert result["action"] == "watch"
    notes = [s for s in _steps(result) if s["kind"] == "note"]
    assert notes


# ---------- 查询 / 接口 ----------

def _add(config, code, created, score=60, action="hold", run_log=None, result=None, trade_date=None):
    with get_db_session(config["database"]["sqlite_path"]) as s:
        row = StockDiagnosis(code=code, name="名", trade_date=trade_date or created.strftime("%Y-%m-%d"),
                             action=action, score=score,
                             result_json=json.dumps(result or {"action": action, "score": score}, ensure_ascii=False),
                             created_at=created)
        if run_log is not None:
            row.run_log = json.dumps(run_log, ensure_ascii=False)
        s.add(row)
        s.flush()
        return row.id


def _daily(config, code, day, close, model=StockDaily):
    with get_db_session(config["database"]["sqlite_path"]) as s:
        kw = dict(code=code, trade_date=day, close=close, open=close, change_pct=1.0)
        if model is StockDaily:
            kw.update(volume=1, amount=1, turnover=1)
        s.add(model(**kw))


def test_get_diagnosis_run_log(env):
    _, app, config = env
    log = {"steps": [{"name": "资金流", "kind": "data", "ok": True, "ms": 12, "detail": ""}], "total_ms": 12, "model": "m"}
    a = _add(config, "600519", datetime.now(), run_log=log)
    b = _add(config, "600519", datetime.now())
    p = app.state.pipeline
    assert p.get_diagnosis(a)["run_log"] == log
    assert p.get_diagnosis(b)["run_log"] is None


def test_api_detail_has_run_log(env):
    client, _, config = env
    log = {"steps": [], "total_ms": 0, "model": ""}
    i = _add(config, "600519", datetime.now(), run_log=log)
    body = client.get(f"/api/v1/stocks/diagnoses/{i}").json()
    assert "run_log" in body and body["run_log"] == log


def test_diagnosis_trend_order_close_and_days(env):
    _, app, config = env
    now = datetime.now().replace(hour=15, minute=0, second=0, microsecond=0)
    d1, d2, d3, old = now - timedelta(days=10), now - timedelta(days=3), now - timedelta(days=1), now - timedelta(days=400)
    ids = [_add(config, "600519", d, score=sc) for d, sc in ((d2, 70), (d1, 60), (d3, 80), (old, 10))]
    _daily(config, "600519", d1.strftime("%Y-%m-%d"), 100.5)
    _daily(config, "600519", d3.strftime("%Y-%m-%d"), 103.0)
    rows = DataQueryService(config["database"]["sqlite_path"]).diagnosis_trend("600519", days=180)
    assert [r["score"] for r in rows] == [60, 70, 80]
    assert ids[3] not in [r["id"] for r in rows]
    for k in ("id", "created_at", "trade_date", "score", "action", "close"):
        assert k in rows[0]
    assert rows[0]["close"] == 100.5 and rows[1]["close"] is None and rows[2]["close"] == 103.0
    assert app.state.pipeline.diagnosis_trend("600519", 180) == rows if hasattr(app.state.pipeline, "diagnosis_trend") else True


def test_diagnosis_trend_index_uses_fund_daily(env):
    client, _, config = env
    now = datetime.now()
    _add(config, "sh000300", now, score=66)
    _daily(config, "sh000300", now.strftime("%Y-%m-%d"), 4567.8, model=FundDaily)
    rows = DataQueryService(config["database"]["sqlite_path"]).diagnosis_trend("sh000300", days=30)
    assert len(rows) == 1 and rows[0]["close"] == 4567.8


def test_api_trend_and_validation(env):
    client, _, config = env
    _add(config, "600519", datetime.now() - timedelta(days=1), score=55)
    _add(config, "600519", datetime.now(), score=65)
    r = client.get("/api/v1/stocks/600519/diagnosis-trend", params={"days": 180})
    assert r.status_code == 200
    assert [x["score"] for x in r.json()] == [55, 65]
    assert client.get("/api/v1/stocks/600519/diagnosis-trend").status_code == 200
    assert client.get("/api/v1/stocks/600519/diagnosis-trend", params={"days": 0}).status_code == 422
    assert client.get("/api/v1/stocks/600519/diagnosis-trend", params={"days": 731}).status_code == 422
