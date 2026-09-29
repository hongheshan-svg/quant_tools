"""main.py --once：按定时任务顺序执行一遍"""

import pytest

from src import scheduler


@pytest.fixture
def calls(monkeypatch):
    called = []
    for key, (label, jobs) in list(scheduler.ONCE_STEPS.items()):
        fakes = tuple((lambda config, name=f"{key}{i}": called.append(name)) for i in range(len(jobs)))
        monkeypatch.setitem(scheduler.ONCE_STEPS, key, (label, fakes))
    return called


def test_run_once_runs_all_steps_in_schedule_order(calls):
    results = scheduler.run_once({})
    assert calls == ["collect0", "collect1", "collect2", "collect3", "collect4", "analysis0", "signals0", "report0", "learn0", "learn1", "watchlist0"]
    assert [r["step"] for r in results] == ["collect", "analysis", "signals", "report", "learn", "watchlist"]
    assert all(r["seconds"] >= 0 for r in results)


def test_run_once_selected_steps_keep_fixed_order(calls):
    results = scheduler.run_once({}, ["report", "analysis"])
    assert calls == ["analysis0", "report0"]
    assert [r["label"] for r in results] == ["每日综合分析", "大盘复盘与日报"]


def test_run_once_rejects_unknown_steps(calls):
    with pytest.raises(ValueError, match="未知步骤: nope"):
        scheduler.run_once({}, ["collect", "nope"])
    assert calls == []
