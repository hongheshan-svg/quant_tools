"""每日任务的失败、冻结配置、跨进程计划认领和人工补跑契约。"""

from concurrent.futures import ProcessPoolExecutor
from datetime import datetime

import pytest

from src import scheduler
from src.services.scheduled_job_claim import claim


def _claim(args):
    return claim(*args)[0]


def test_claim_is_atomic_across_processes_and_freezes_config(tmp_path):
    cfg = {"database": {"sqlite_path": str(tmp_path / "claims.db")}, "strategy": {"minimum_score": 60}}
    args = ("daily_analysis", cfg, datetime(2026, 10, 7, 15, 30))
    with ProcessPoolExecutor(max_workers=2) as pool:
        assert sum(pool.map(_claim, [args, args])) == 1
    cfg["strategy"]["minimum_score"] = 65
    acquired, frozen = claim(*args)
    assert acquired
    cfg["strategy"]["minimum_score"] = 70
    assert frozen["strategy"]["minimum_score"] == 65
    assert claim("daily_report", frozen, args[2])[0]


def test_scheduled_failure_retains_claim_manual_retry_is_allowed(tmp_path, monkeypatch):
    cfg = {"database": {"sqlite_path": str(tmp_path / "claims.db")}, "scheduler": {"isolate_daily_jobs": False}}
    calls = []
    def fail(config):
        calls.append(config)
        raise RuntimeError("故障")
    monkeypatch.setitem(scheduler.JOBS, "daily_analysis", ("每日分析", fail))
    with pytest.raises(RuntimeError, match="故障"):
        scheduler.run_scheduled_job("daily_analysis", cfg, datetime(2026, 10, 7, 15, 30))
    assert scheduler.run_scheduled_job("daily_analysis", cfg, datetime(2026, 10, 7, 15, 30))["status"] == "skipped"
    with pytest.raises(RuntimeError):
        scheduler.run_job("daily_analysis", cfg)
    assert len(calls) == 2


def test_once_daily_steps_use_unified_isolation(monkeypatch):
    calls = []
    monkeypatch.setattr(scheduler, "run_isolated", lambda job, cfg: calls.append(job) or True)
    scheduler.run_once({}, ["analysis", "signals", "report", "learn", "watchlist"])
    assert calls == ["daily_analysis", "signal_generation", "daily_report", "self_learning", "signal_lifecycle", "watchlist_report"]


def test_isolated_failure_is_an_error_not_completed(monkeypatch):
    monkeypatch.setattr(scheduler, "run_isolated", lambda *args: False)
    with pytest.raises(RuntimeError, match="失败或超时"):
        scheduler.run_job("daily_analysis", {})


def test_entry_reports_business_failure_and_preserves_skip(monkeypatch):
    import main
    import src.config_loader
    monkeypatch.setattr(main, "setup_logging", lambda cfg: None)
    monkeypatch.setattr(src.config_loader, "load_config", lambda: {})
    monkeypatch.setattr(scheduler, "_report_error", lambda *args: None)
    monkeypatch.setitem(scheduler.JOBS, "daily_analysis", ("分析", lambda cfg: {"error": "没有可用行情"}))
    assert scheduler.run_job_entry("daily_analysis") == scheduler.JOB_FAILED_EXIT
    def skip(cfg):
        scheduler._job_skipped.set(True)
    monkeypatch.setitem(scheduler.JOBS, "daily_analysis", ("分析", skip))
    assert scheduler.run_job_entry("daily_analysis") == scheduler.JOB_SKIPPED_EXIT
