"""并发线程要继承调用方的上下文：后台任务的 trace_id、事件接收，以及诊断的运行记录。"""
from src.services import collector_orchestrator as orchestrator_mod
from src.services import diagnosis_agents, run_log, skill_consult
from src.services.strategy_skills import load_skills
from tests.test_all_sources_stability import _full_news_counts, _make_orchestrator, _ok_market


def test_retried_collect_groups_keep_task_trace(monkeypatch):
    orchestrator = _make_orchestrator(monkeypatch, collect_max_attempts=3)
    seen = []

    def fake_collect_news():
        seen.append((run_log.TRACE_ID.get(), run_log.EVENT_SINK.get() is not None))
        news = _full_news_counts()
        if len(seen) == 1:
            news["weibo"] = 0   # 首轮缺一组，触发重试
        return news

    monkeypatch.setattr(orchestrator, "collect_news_parallel", fake_collect_news)
    monkeypatch.setattr(orchestrator, "collect_market_parallel", lambda: _ok_market())
    monkeypatch.setattr(orchestrator, "collect_market_overview", lambda: {})
    monkeypatch.setattr(orchestrator, "_collect_global_news", lambda: 4)
    monkeypatch.setattr(orchestrator, "_collect_us_earnings", lambda: 6)
    with run_log.execution_trace("task-trace-1", lambda event: None):
        result = orchestrator.collect_all()
    assert result["collect_attempts"] == 2
    assert seen == [("task-trace-1", True), ("task-trace-1", True)]


class _RecordingLLM:
    def __init__(self):
        self.logs = []

    def chat_json(self, user_message, system_message=""):
        self.logs.append(run_log.ACTIVE_LOG.get())
        return {"view": "中性", "score": 50, "confidence": "中", "summary": "ok", "stance": "中性", "reason": "ok"}


def test_analysts_without_budget_record_into_active_run_log():
    llm = _RecordingLLM()
    log = run_log.RunLog()
    try:
        opinions = diagnosis_agents.run_analysts(llm, "【行情】\n收盘 10", "standard", budget=None)
    finally:
        run_log.ACTIVE_LOG.set(None)
    assert opinions and len(llm.logs) == len(opinions)
    assert all(item is log for item in llm.logs)


def test_skill_consult_without_budget_records_into_active_run_log():
    llm = _RecordingLLM()
    skills = load_skills()[:2]
    log = run_log.RunLog()
    try:
        skill_consult.consult(llm, "【行情】\n收盘 10", skills, budget=None)
    finally:
        run_log.ACTIVE_LOG.set(None)
    assert len(llm.logs) == len(skills) and all(item is log for item in llm.logs)
