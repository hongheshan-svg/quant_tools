"""独立进程的硬超时、取消及流关闭测试，不调用模型和数据源。"""

import os
import threading
import time
import subprocess
import sys
from pathlib import Path

import pytest

from src.services import analysis_process as mod
from src.services.execution_budget import BudgetExpired, ExecutionBudget


def _blocked_worker(connection, operation, config, payload, deadline):
    if os.name != "nt":
        os.setsid()
    Path(config["pid_path"]).write_text(str(os.getpid()))
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    Path(config["child_pid_path"]).write_text(str(child.pid))
    connection.send(("event", {"type": "status", "text": "blocked"}))
    while True:
        time.sleep(1)


def _assert_terminated(pid_path):
    if os.name != "nt":
        with pytest.raises(ProcessLookupError):
            os.kill(int(pid_path.read_text()), 0)


def _assert_descendant_stopped(pid_path):
    if os.name == "nt":
        return
    pid = int(pid_path.read_text())
    for _ in range(100):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        # Linux 的已停止孙进程可能等待容器 init 回收，僵尸不再执行代码。
        state = Path(f"/proc/{pid}/stat")
        if sys.platform == "linux":
            try:
                if state.read_text().split()[2] == "Z":
                    return
            except FileNotFoundError:
                return
        time.sleep(0.01)
    pytest.fail("分析进程的子进程仍在运行")


@pytest.mark.parametrize("reason", ["cancel", "deadline", "consumer_close"])
def test_blocked_worker_is_terminated(reason, tmp_path, monkeypatch):
    pid_path = tmp_path / "child.pid"
    child_pid_path = tmp_path / "descendant.pid"
    budget = ExecutionBudget(10)
    monkeypatch.setattr(mod, "_worker", _blocked_worker)
    monkeypatch.setattr(ExecutionBudget, "from_config", classmethod(lambda cls, cfg: budget))
    cancel = threading.Event()
    events = mod.isolated_events("fake", {"pid_path": str(pid_path), "child_pid_path": str(child_pid_path)}, {}, cancel)
    assert next(events)[1]["text"] == "blocked"
    if reason == "consumer_close":
        events.close()
    elif reason == "cancel":
        cancel.set()
        with pytest.raises(InterruptedError):
            next(events)
    else:
        budget.deadline = time.monotonic() - 1
        with pytest.raises(BudgetExpired):
            next(events)
    _assert_terminated(pid_path)
    _assert_descendant_stopped(child_pid_path)


def test_optional_analysis_budget_keeps_completed_results():
    release = threading.Event()
    def call(value):
        if value == "blocked":
            release.wait(5)
        return value
    try:
        start = time.monotonic()
        result = ExecutionBudget(1).parallel(call, ["ready", "blocked"], seconds=0.03)
        assert result == ["ready"] and time.monotonic() - start < 0.5
    finally:
        release.set()


def test_concurrent_worker_events_do_not_overlap_pipe_writes(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from src.services import stock_diagnosis
    from src.services.run_log import EVENT_SINK, TRACE_ID

    class Connection:
        def __init__(self):
            self.active, self.overlaps, self.sent = 0, 0, []
        def send(self, value):
            self.overlaps += self.active > 0
            self.active += 1
            time.sleep(0.001)  # 让另一个线程有机会写入同一个 IPC 对象
            self.sent.append(value)
            self.active -= 1
        def close(self):
            pass

    class Service:
        def __init__(self, config):
            pass
        def diagnose(self, **kwargs):
            sink = EVENT_SINK.get()
            with ThreadPoolExecutor(max_workers=8) as executor:
                list(executor.map(lambda index: sink({"type": "step", "index": index}), range(32)))
            return {"ok": True}

    monkeypatch.setattr(mod.os, "setsid", lambda: None, raising=False)
    monkeypatch.setattr(stock_diagnosis, "StockDiagnosisService", Service)
    connection = Connection()
    previous_sink, previous_trace = EVENT_SINK.get(), TRACE_ID.get()
    try:
        mod._worker(connection, "diagnosis", {}, {}, time.monotonic() + 10)
    finally:
        EVENT_SINK.set(previous_sink)
        TRACE_ID.set(previous_trace)
    assert connection.overlaps == 0 and len(connection.sent) == 33
    assert connection.sent[-1] == ("result", {"ok": True})
