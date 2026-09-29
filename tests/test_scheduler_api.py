"""定时任务面板：JOBS 注册表、describe_jobs 与 /system/scheduler 接口。"""

from __future__ import annotations

import threading

from apscheduler.schedulers.background import BackgroundScheduler

from src import scheduler as sched
from tests.test_api import _wait, env  # noqa: F401  (env 是 pytest fixture)


def test_jobs_cover_registered_ids():
    built = sched.build_scheduler({}, BackgroundScheduler())
    ids = {j.id for j in built.get_jobs()}
    assert ids <= set(sched.JOBS)
    assert set(sched.JOBS) - ids == {"rss", "alert_digest"}     # 没有启用资讯源、盘中提醒日报时不注册 rss / alert_digest
    for job in built.get_jobs():
        assert job.name == sched.JOBS[job.id][0]
        assert callable(sched.JOBS[job.id][1])


def test_describe_jobs_with_scheduler():
    built = sched.build_scheduler({}, BackgroundScheduler())
    rows = {r["id"]: r for r in sched.describe_jobs(built)}
    assert set(rows) == set(sched.JOBS) - {"rss", "alert_digest"}
    for key in ("hot_search", "cailianshe", "stock_data", "global_data"):
        assert "分钟" in rows[key]["trigger"]
    for key in ("daily_analysis", "signal_generation", "daily_report", "watchlist_report", "self_learning"):
        assert "工作日" in rows[key]["trigger"]
    assert "15:30" in rows["daily_analysis"]["trigger"]
    assert "16:00" in rows["signal_generation"]["trigger"]
    for row in rows.values():
        assert {"id", "name", "trigger", "next_run_time", "paused"} <= set(row)
        assert row["name"] == sched.JOBS[row["id"]][0]
        assert row["paused"] is False


RSS_CONFIG = {"intelligence": {"enabled": True, "sources": [
    {"name": "x", "url": "https://e.com/f.xml", "enabled": True}]}}


def test_rss_job_registered_with_enabled_source():
    built = sched.build_scheduler(RSS_CONFIG, BackgroundScheduler())
    assert {j.id for j in built.get_jobs()} == set(sched.JOBS) - {"alert_digest"}
    rows = {r["id"]: r for r in sched.describe_jobs(built)}
    assert rows["rss"]["name"] == sched.JOBS["rss"][0]
    assert "分钟" in rows["rss"]["trigger"]


def test_describe_jobs_uses_config_intervals():
    built = sched.build_scheduler({"scheduler": {"hot_search_interval": 7, "daily_analysis_time": "09:45"}},
                                  BackgroundScheduler())
    rows = {r["id"]: r for r in sched.describe_jobs(built)}
    assert "7" in rows["hot_search"]["trigger"]
    assert "09:45" in rows["daily_analysis"]["trigger"]


def test_describe_jobs_without_scheduler():
    rows = sched.describe_jobs(None)
    assert {r["id"] for r in rows} == set(sched.JOBS)
    assert all(r["trigger"] == "" and r["next_run_time"] is None for r in rows)


def test_scheduler_status_when_not_running(env):
    client, app, config = env
    assert app.state.scheduler is None
    data = client.get("/api/v1/system/scheduler").json()
    assert data["running"] is False
    assert data["message"]
    assert {j["id"] for j in data["jobs"]} == set(sched.JOBS)


def test_run_unknown_job_404(env):
    client, _, _ = env
    assert client.post("/api/v1/system/scheduler/nope/run").status_code == 404


def test_run_job_now_calls_function_with_config(env, monkeypatch):
    client, _, config = env
    received = []
    name = sched.JOBS["hot_search"][0]
    monkeypatch.setitem(sched.JOBS, "hot_search", (name, lambda cfg: received.append(cfg)))
    task = client.post("/api/v1/system/scheduler/hot_search/run")
    assert task.status_code == 200
    done = _wait(client, task.json())
    assert done["status"] == "done"
    assert len(received) == 1 and isinstance(received[0], dict)


def test_run_job_dedupes_while_running(env, monkeypatch):
    client, _, _ = env
    gate = threading.Event()
    calls = []

    def slow(cfg):
        calls.append(1)
        gate.wait(5)

    monkeypatch.setitem(sched.JOBS, "cailianshe", (sched.JOBS["cailianshe"][0], slow))
    first = client.post("/api/v1/system/scheduler/cailianshe/run").json()
    second = client.post("/api/v1/system/scheduler/cailianshe/run").json()
    try:
        assert first["id"] == second["id"]
    finally:
        gate.set()
    assert _wait(client, first)["status"] == "done"
    assert len(calls) == 1
