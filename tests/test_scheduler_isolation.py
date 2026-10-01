"""每日任务在独立子进程中运行：超时终止整个进程树、按退出码推送系统错误、--run-job 入口。"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from src import scheduler as sched


@pytest.fixture
def reports(monkeypatch):
    calls = []
    monkeypatch.setattr(sched, "_report_error", lambda config, source, error: calls.append((source, error)))
    return calls


def _py(code: str) -> list[str]:
    return [sys.executable, "-c", code]


def test_run_isolated_exit_codes(reports):
    config = {"scheduler": {"job_timeout_minutes": 1}}
    assert sched.run_isolated("daily_analysis", config, _py("import sys; sys.exit(0)")) is True
    assert sched.run_isolated("daily_analysis", config, _py(f"import sys; sys.exit({sched.JOB_FAILED_EXIT})")) is False
    assert reports == []                                             # 任务自己出错时子进程已推送，不重复推送
    assert sched.run_isolated("daily_report", config, _py("import sys; sys.exit(3)")) is False
    assert reports[0][0] == "大盘复盘与日报" and "退出码 3" in str(reports[0][1])


@pytest.mark.skipif(sys.platform == "win32", reason="进程组终止用 POSIX 信号验证")
def test_run_isolated_timeout_kills_process_tree(tmp_path, reports):
    pid_file = tmp_path / "grandchild.pid"
    code = ("import subprocess, sys, time; "
            "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
            f"open(r'{pid_file}', 'w').write(str(p.pid)); time.sleep(60)")
    started = time.monotonic()
    assert sched.run_isolated("watchlist_report", {"scheduler": {"job_timeout_minutes": 0.03}}, _py(code)) is False
    assert time.monotonic() - started < 30
    assert isinstance(reports[0][1], TimeoutError) and "已终止" in str(reports[0][1])
    grandchild = int(pid_file.read_text())
    for _ in range(50):                                              # 孙进程（如 Chromium）也要被终止
        try:
            os.kill(grandchild, 0)
        except ProcessLookupError:
            break
        time.sleep(0.1)
    else:
        os.kill(grandchild, 9)
        pytest.fail("孙进程没有被终止")


def test_run_job_routes(monkeypatch):
    isolated, direct = [], []
    monkeypatch.setattr(sched, "run_isolated", lambda job_id, config, command=None: isolated.append(job_id) or True)
    monkeypatch.setitem(sched.JOBS, "daily_analysis", ("每日综合分析", lambda cfg: direct.append("daily_analysis")))
    monkeypatch.setitem(sched.JOBS, "hot_search", ("热搜数据采集", lambda cfg: direct.append("hot_search")))
    sched.run_job("daily_analysis", {})                                           # 默认独立进程
    sched.run_job("hot_search", {})                                               # 间隔采集在本进程
    sched.run_job("daily_analysis", {"scheduler": {"isolate_daily_jobs": False}})
    assert isolated == ["daily_analysis"] and direct == ["hot_search", "daily_analysis"]


def test_run_job_entry(monkeypatch, reports):
    import main
    from src import config_loader

    monkeypatch.setattr(main, "setup_logging", lambda config: None)
    monkeypatch.setattr(config_loader, "load_config", lambda *a, **k: {})
    seen = []
    monkeypatch.setitem(sched.JOBS, "signal_lifecycle", ("决策信号评估", lambda cfg: seen.append(cfg)))
    assert sched.run_job_entry("signal_lifecycle") == 0 and seen == [{}]

    def boom(cfg):
        raise RuntimeError("接口超时")
    monkeypatch.setitem(sched.JOBS, "self_learning", ("每日自学习", boom))
    assert sched.run_job_entry("self_learning") == sched.JOB_FAILED_EXIT
    assert reports[-1][0] == "每日自学习" and "接口超时" in str(reports[-1][1])
    assert sched.run_job_entry("nope") == sched.JOB_FAILED_EXIT


def test_daily_jobs_registered_through_run_job():
    from apscheduler.schedulers.background import BackgroundScheduler

    scheduler = sched.build_scheduler({"alerts": {"daily_digest": True}}, BackgroundScheduler())
    jobs = {j.id: j for j in scheduler.get_jobs()}
    for job_id in sched.ISOLATED_JOBS:
        assert jobs[job_id].func is sched.run_job and jobs[job_id].args[0] == job_id, job_id
    assert jobs["stock_data"].func is sched._run_stock_data_collection


def test_server_run_job_cli(tmp_path):
    """真实启动 server.py --run-job：未知任务按失败退出，日志写到指定的数据目录"""
    root = Path(__file__).resolve().parents[1]
    proc = subprocess.run([sys.executable, str(root / "server.py"), "--run-job", "nope", "--workdir", str(tmp_path)],
                          cwd=root, capture_output=True, text=True, timeout=120)
    assert proc.returncode == sched.JOB_FAILED_EXIT
    assert (tmp_path / "config").is_dir()
