"""研究闭环离线回归：不可变后验、来源范围、持久化事件和热更新。"""

import json
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest
from apscheduler.schedulers.background import BackgroundScheduler

from api.tasks import TaskManager
from scripts.merge_release_assets import merge_metadata
from scripts.pytest_shard import shard_files
from src.collectors.quarterly_fundamentals import QuarterlyFundamentals, dividend_events, financial_reports
from src.database.db import get_db_session
from src.database.models import ResearchCache, SkillOpinion, StockDaily, TaskRun
from src.scheduler import build_scheduler, refresh_scheduler
from src.services.alert_service import AlertEvent, AlertService
from src.services.intelligence import IntelligenceService
from src.services.outcome_engine import OutcomeEngine
from src.services.skill_consult import SkillOpinionService, consensus
from src.services.strategy_synthesis import synthesize
from src.strategy.screening_rules import load_rules, matches
from tests.test_api import _wait, env  # noqa: F401


def test_scoped_source_fetch_dedupes_and_records_error(env, monkeypatch):
    _, _, config = env
    service = IntelligenceService(config)
    source = service.save_source({"name": "测试资讯", "url": "https://example.invalid/feed", "symbol": "SH600519", "sector": "白酒"})
    from src.collectors.rss import RSSCollector
    monkeypatch.setattr(RSSCollector, "_fetch_feed", lambda *args: ("feed", [{"title": "季度公告", "summary": "摘要", "url": "https://example.invalid/one", "published": datetime.now()}]))
    assert service.fetch_source(source["id"])["added"] == 1
    assert service.fetch_source(source["id"])["added"] == 0
    assert len(service.items(symbol="600519", sector="白酒")) == 1
    assert service.items(symbol="000001") == []
    def fail(*args):
        raise ValueError("api_key=FAKE_FEED_SECRET")
    monkeypatch.setattr(RSSCollector, "_fetch_feed", fail)
    service.fetch_source(source["id"])
    assert "FAKE_FEED_SECRET" not in json.dumps(service.sources())
    assert len(service.items(symbol="600519")) == 1


def test_task_result_survives_restart_and_interrupted_task_is_explicit(env):
    _, _, config = env
    path = config["database"]["sqlite_path"]
    tasks = TaskManager(workers=1, db_path=path)
    task = tasks.submit("sample", lambda: {"ok": True})
    tasks._pool.shutdown(wait=True)
    with get_db_session(path) as session:
        session.add(TaskRun(id="interrupted", payload_json=json.dumps({"id": "interrupted", "kind": "sample", "status": "running", "revision": 2})))
    restored = TaskManager(workers=1, db_path=path)
    try:
        assert restored.get(task["id"])["status"] == "done"
        assert restored.get("interrupted")["status"] == "error"
        assert "重启" in restored.get("interrupted")["error"]
    finally:
        restored.shutdown()


def test_terminal_task_events_include_trace_and_revision(env):
    client, _, _ = env
    # 直接使用任务管理器，避免真实模型调用。
    task = client.app.state.tasks.submit("sample", lambda: {"ok": True})
    done = _wait(client, task)
    response = client.get(f"/api/v1/tasks/{task['id']}/events")
    assert response.status_code == 200
    assert '"status": "done"' in response.text
    assert done["trace_id"] in response.text
    assert done["revision"] > 1


def test_hot_update_preserves_paused_job_and_unrelated_job():
    scheduler = build_scheduler({}, BackgroundScheduler())
    scheduler.start(paused=True)
    try:
        scheduler.pause_job("daily_analysis")
        scheduler.add_job(lambda: None, "interval", minutes=60, id="unrelated")
        before = scheduler.get_job("hot_search").next_run_time
        refresh_scheduler(scheduler, {"scheduler": {"daily_analysis_time": "09:45"}})
        assert scheduler.get_job("daily_analysis").next_run_time is None
        assert "hour='9'" in str(scheduler.get_job("daily_analysis").trigger)
        assert scheduler.get_job("hot_search").next_run_time == before
        assert scheduler.get_job("unrelated") is not None
    finally:
        scheduler.shutdown(wait=False)


def test_scheduler_schema_and_invalid_time_are_safe(env):
    client, _, _ = env
    assert client.put("/api/v1/settings/scheduler", json={"daily_analysis_time": "24:01"}).status_code == 422
    schema = client.get("/api/v1/settings/schema").json()
    assert "sk-secret-1234" not in json.dumps(schema)
    assert any(row["path"] == "diagnosis.timeout_seconds" for row in schema["fields"])


def test_alert_cooldown_survives_new_service_and_channel_attempts(env):
    _, _, config = env
    event = AlertEvent("600519", "贵州茅台", "big_drop", "warning", "下跌", rule_id="rule-one")
    now = datetime(2026, 9, 28, 10)
    service = AlertService(config)
    assert service._claim_event(event, now, timedelta(minutes=30)) == ""
    service._save([(event, "")], True, now, {"email": False, "wechat": True})
    assert AlertService(config)._claim_event(event, now + timedelta(minutes=1), timedelta(minutes=30)) == "冷却中"
    assert service.recent()[0]["channels"] == {"email": False, "wechat": True}
    assert AlertService(config)._claim_event(event, now + timedelta(minutes=31), timedelta(minutes=30)) == ""


def test_financial_metric_dates_units_and_cash_dividend():
    frame = pd.DataFrame({"指标": ["营业总收入", "归母净利润", "净资产收益率", "经营活动产生的现金流量净额"],
                          "20250630": ["2亿", "3,000万", "15%", "--"], "20990630": [1, 2, 3, 4]})
    reports = financial_reports(frame, date(2026, 9, 28))
    assert len(reports) == 1 and reports[0]["revenue"] == 2e8 and reports[0]["net_profit"] == 3e7
    assert reports[0]["roe"] == 15 and reports[0]["operating_cash_flow"] is None
    dividends = dividend_events(pd.DataFrame([{"除权除息日": "2026-06-01", "派息": 10, "进度": "实施"},
                                             {"除权除息日": "2026-07-01", "派息": 20, "进度": "预案"},
                                             {"除权除息日": "2027-06-01", "派息": 30, "进度": "实施"}]), date(2026, 9, 28))
    assert dividends["ttm_cash_per_share"] == 1 and len(dividends["events"]) == 1


def test_fundamental_failure_keeps_previous_snapshot(env, monkeypatch):
    _, _, config = env
    previous = {"reports": [{"report_date": "2025-12-31", "roe": 16}], "dividend": {"events": []}, "source": "fixture"}
    with get_db_session(config["database"]["sqlite_path"]) as session:
        session.add(ResearchCache(key="fundamentals:600519", payload_json=json.dumps(previous), updated_at=datetime.now() - timedelta(days=2)))
    import akshare as ak
    def fail(**kwargs):
        raise RuntimeError("offline")
    monkeypatch.setattr(ak, "stock_financial_abstract", fail)
    monkeypatch.setattr(ak, "stock_financial_analysis_indicator", fail)
    monkeypatch.setattr(ak, "stock_history_dividend_detail", fail)
    result = QuarterlyFundamentals(config).get("600519")
    assert result["status"] == "stale" and result["reports"] == previous["reports"]


def test_outcome_neutral_band_versions_and_completed_results_immutable(env):
    _, _, config = env
    engine = OutcomeEngine(config)
    with get_db_session(config["database"]["sqlite_path"]) as session:
        session.add(StockDaily(code="600519", trade_date="2026-09-28", close=10.03))
    with get_db_session(config["database"]["sqlite_path"]) as session:
        result = engine.evaluate(session, "diagnosis", 101, "600519", "2026-09-25", 0, now=datetime(2026, 9, 28), horizons=(1,))[0]
        assert result.status == "evaluated" and result.hit is True
        session.query(StockDaily).filter_by(code="600519", trade_date="2026-09-28").update({"close": 20})
    with get_db_session(config["database"]["sqlite_path"]) as session:
        original = engine.evaluate(session, "diagnosis", 101, "600519", "2026-09-25", 0, horizons=(1,))[0]
        assert original.return_pct == pytest.approx(0.3)
        changed = OutcomeEngine({**config, "evaluation": {"neutral_band_pct": 0.1}}).evaluate(session, "diagnosis", 101, "600519", "2026-09-25", 0, horizons=(1,))[0]
        assert changed.engine_version != original.engine_version and changed.hit is False


def test_repeated_opinions_are_one_independent_weight_sample(env):
    _, _, config = env
    with get_db_session(config["database"]["sqlite_path"]) as session:
        for _ in range(40):
            session.add(SkillOpinion(code="600519", skill="volume_breakout", stance="看多", score=70, trade_date="2026-09-25", hit=True, ret_5d=2))
    row = SkillOpinionService(config).performance()[0]
    assert row["samples"] == 1 and row["weight"] == 1


def test_synthesis_uses_direction_and_preserves_minority():
    neutral = {"skill": "neutral", "stance": "中性", "score": 95, "confidence": "高"}
    assert consensus([neutral, {**neutral, "skill": "two"}])["stance"] == "中性"
    opinions = [{"skill": "bull", "stance": "看多", "score": 90, "confidence": "高"},
                {"skill": "bear", "stance": "看空", "score": 20, "confidence": "高"}]
    result = synthesize(opinions)
    assert result["conflicts"] and result["confidence_cap"] == "低"
    assert result["deliberation"]["resolution_status"] == "unresolved"
    assert result["deliberation"]["responses"][1]["revised_stance"] == "看空"


def test_yaml_rules_reject_unknown_fields_and_missing_financials(tmp_path):
    from types import SimpleNamespace
    valid = load_rules("config/screening_rules.yaml.example")
    quality = next(row for row in valid if row["name"] == "value_quality")
    assert matches(SimpleNamespace(pe=15, pb=2, roe=16), quality["conditions"])
    assert not matches(SimpleNamespace(pe=15, pb=2, roe=None), quality["conditions"])
    file = tmp_path / "rules.yaml"
    file.write_text("rules:\n  - name: invalid\n    conditions: [{field: unsafe_expression, op: gt, value: 1}]\n")
    with pytest.raises(ValueError, match="未知字段"):
        load_rules(str(file))


def test_release_metadata_keeps_both_architectures_and_shards_cover_all_files():
    files = [Path(f"tests/test_{index}.py") for index in range(35)]
    shards = [shard_files(files, index, 3) for index in range(3)]
    assert sorted(file for shard in shards for file in shard) == sorted(files)
    merged = merge_metadata([{"version": "1.0.0", "files": [{"url": "app-x64.zip", "sha512": "a"}]},
                             {"version": "1.0.0", "files": [{"url": "app-arm64.zip", "sha512": "b"}]}])
    assert len(merged["files"]) == 2
    with pytest.raises(ValueError, match="版本"):
        merge_metadata([{"version": "1"}, {"version": "2"}])


def test_research_artifact_formats_evidence_and_accepts_old_invalid_fields():
    from src.services.research_artifact import build_research_artifact
    result = {"code": "600519", "score": "NaN", "risks": "旧报告风险", "battle_plan": {"stop_loss": "未知"},
              "context_pack": {"blocks": {
                  "news": {"status": "available", "items": {"evidence": {"value": [{"title": "公告标题", "summary": "公告摘要", "source": "公告源", "published_at": "2026-09-25"}]}}},
                  "notices": {"status": "missing", "items": {"evidence": {"value": []}}}}}}
    report = build_research_artifact(result, 42)
    assert report["source_report_id"] == 42 and report["thesis"]["score"] is None
    assert report["thesis"]["risks"] == ["旧报告风险"]
    assert len(report["evidence"]) == 1
    assert report["evidence"][0]["title"] == "公告标题" and report["evidence"][0]["summary"] == "公告摘要"
    assert report["evidence"][0]["as_of"] == "2026-09-25"


def test_corrupt_task_history_does_not_prevent_restart(env):
    _, _, config = env
    path = config["database"]["sqlite_path"]
    with get_db_session(path) as session:
        session.add(TaskRun(id="corrupt", payload_json="not-json"))
    restored = TaskManager(workers=1, db_path=path)
    try:
        assert restored.get("corrupt") is None
        task = restored.submit("sample", lambda: True)
        restored._pool.shutdown(wait=True)
        assert restored.get(task["id"])["status"] == "done"
    finally:
        restored.shutdown()


def test_stock_context_checks_named_targets_and_reuses_alias_calls():
    from src.services.stock_chat import ChatTurn, StockChatSession
    from tests.test_chat_stream import FakeTools, JsonOnlyLLM
    tools = FakeTools()
    tools._resolve_kind = lambda args: ("000001" if args.get("query") == "平安银行" else "600519", "", "stock")
    chat = StockChatSession({"diagnosis": {}}, llm=JsonOnlyLLM([]), tools=tools)
    turn = ChatTurn(question="分析", stock_context={"code": "600519"})
    assert chat._call_tool("quote", {"query": "平安银行"}, turn).startswith("工具被拒绝")
    assert tools.calls == []
    args = {"code": "SH600519"}
    result = chat._call_tool("quote", args, turn)
    turn.tools.append({"name": "quote", "args": args, "result": result})
    assert chat._call_tool("quote", {"code": "600519.SH"}, turn) == result
    assert len(tools.calls) == 1


def test_large_history_keeps_current_question_and_scope():
    from src.services.stock_chat import ChatTurn, StockChatSession
    from tests.test_chat_stream import FakeTools, JsonOnlyLLM
    chat = StockChatSession({"diagnosis": {"chat_context_chars": 2000}}, llm=JsonOnlyLLM([]), tools=FakeTools())
    chat.turns = [ChatTurn(question="旧问题" * 500, answer="旧回答" * 2000) for _ in range(20)]
    turn = ChatTurn(question="当前问题必须保留", stock_context={"code": "600519"})
    for final in (False, True):
        prompt = chat._user_message(turn, final)
        assert len(prompt) <= 2000 and "当前问题必须保留" in prompt and "600519" in prompt


def test_chat_scope_and_skills_survive_api_session_restore(env):
    client, app, _ = env
    from src.services.stock_chat import StockChatSession
    from tests.test_chat_stream import FakeTools, JsonOnlyLLM
    app.state.chat_store._factory = lambda cfg: StockChatSession(cfg, llm=JsonOnlyLLM([{"answer": "范围内答复"}]), tools=FakeTools())
    session_id = client.post("/api/v1/chat/sessions", json={}).json()["id"]
    url = f"/api/v1/chat/sessions/{session_id}/ask"
    assert client.post(url, json={"question": "分析", "skills": ["unknown-skill"]}).status_code == 422
    task = client.post(url, json={"question": "分析", "stock_context": {"code": "SH600519"}, "skills": ["volume_breakout"]}).json()
    assert _wait(client, task)["status"] == "done"
    record = client.get(f"/api/v1/chat/sessions/{session_id}").json()["turns"][0]
    assert record["stock_context"] == {"code": "600519"} and record["skills"] == ["volume_breakout"]


def test_scoped_intelligence_filters_other_explicit_scope_and_unsafe_links(env):
    _, _, config = env
    service = IntelligenceService(config)
    one = service.save_source({"name": "源一", "url": "https://example.invalid/one", "symbol": "600519"})
    other = service.save_source({"name": "源二", "url": "https://example.invalid/two", "symbol": "000001"})
    service.ingest([{"title": "600519 资讯", "url": "javascript:alert(1)"}], one)
    service.ingest([{"title": "600519 与另一标的对照"}], other)
    records = service.items(symbol="600519")
    assert len(records) == 1 and records[0]["url"] == ""


def test_partial_screening_keeps_last_good_and_labels_yaml_performance(env, monkeypatch):
    from src.database.models import StrategyPick
    from src.strategy.screener import StrategyScreener
    _, _, config = env
    with get_db_session(config["database"]["sqlite_path"]) as session:
        session.add(StrategyPick(code="600519", name="贵州茅台", trade_date="2026-09-25", strategy="value_quality", score=70))
    screener = StrategyScreener({**config, "screening": {"minimum_universe": 3000, "rules_file": "config/screening_rules.yaml.example"}})
    monkeypatch.setattr(screener, "strategy_weights", lambda: {})
    monkeypatch.setattr(screener, "_main_lines", lambda *args: {})
    monkeypatch.setattr(screener, "_regime", lambda *args: "均衡")
    result = screener.run("2026-09-25")
    assert "保留上次成功选股结果" in "".join(result.notes)
    with get_db_session(config["database"]["sqlite_path"]) as session:
        assert session.query(StrategyPick).filter_by(strategy="value_quality").count() == 1
    row = next(row for row in screener.performance(3650) if row["strategy"] == "value_quality")
    assert row["label"] == "价值质量" and row["picks"] == 1


def test_trajectory_failure_scope_and_budget_are_visible():
    from src.services.trajectory_eval import evaluate_trajectory
    expected = {"required_tools": ["quote"], "max_ms": 1000, "max_failures": 2}
    refused = {"answer": "结果", "tools": [{"name": "quote", "args": {}, "result": "工具被拒绝：超出当前股票范围"}], "run_log": {"total_ms": 10}}
    result = evaluate_trajectory(refused, expected)
    assert result["scope_violations"] == 1 and not result["passed"]
    clean = {**refused, "tools": [{"name": "quote", "args": {}, "result": "行情"}]}
    assert evaluate_trajectory(clean, expected)["passed"]
    assert not evaluate_trajectory({**clean, "run_log": {}}, expected)["passed"]


def test_ci_changes_route_relevant_paths():
    from scripts.ci_changes import affected_jobs
    assert affected_jobs(["README.md"]) == {"backend": False, "web": False, "desktop": False}
    assert affected_jobs(["api/v1/chat.py"]) == {"backend": True, "web": True, "desktop": False}
    assert affected_jobs([".github/workflows/release.yml"]) == dict.fromkeys(("backend", "web", "desktop"), True)


def test_versioned_skill_weights_do_not_reuse_different_evaluation_version(env):
    _, _, config = env
    from src.database.models import ResearchOutcome
    service = SkillOpinionService(config)
    service.record(1, "600519", "贵州茅台", "2026-09-25", [{"skill": "volume_breakout", "stance": "看多", "score": 70, "confidence": "高"}])
    with get_db_session(config["database"]["sqlite_path"]) as session:
        opinion = session.query(SkillOpinion).first()
        opinion.hit, opinion.ret_5d = True, 2
        session.add(ResearchOutcome(owner_type="skill", owner_id=opinion.id, horizon=5, engine_version=OutcomeEngine(config).version,
                                    status="evaluated", hit=True, return_pct=2))
    assert service.performance()[0]["samples"] == 1
    assert SkillOpinionService({**config, "evaluation": {"neutral_band_pct": 2}}).performance() == []


def test_chat_trajectory_has_runtime_and_trace_for_sync_and_stream():
    from src.services.stock_chat import StockChatSession
    from tests.test_chat_stream import FakeTools, JsonOnlyLLM
    for stream in (False, True):
        chat = StockChatSession({"diagnosis": {}}, llm=JsonOnlyLLM([{"answer": "答复"}]), tools=FakeTools())
        if stream:
            list(chat.ask_stream("问题"))
        else:
            chat.ask("问题")
        log = chat.turns[-1].run_log
        assert log["trace_id"] and log["total_ms"] >= 0 and log["steps"]
