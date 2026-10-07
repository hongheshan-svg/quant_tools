"""耗时分析在独立进程中执行；取消/超时/消费者关闭时终止进程及其子进程。

配置通过 Pipe 传递，不写入命令行或临时文件；研究、诊断及既有定时采集任务使用此边界。
"""

from __future__ import annotations

import multiprocessing
import os
import signal
import threading
from typing import Any, Iterator

from src.services.execution_budget import BudgetExpired, ExecutionBudget
from src.utils.redaction import redact, redact_text


def _worker(connection, operation: str, config: dict, payload: dict, deadline: float) -> None:
    if os.name != "nt":
        os.setsid()
    send_lock = threading.RLock()

    def send(value):
        # 并发观点会同时发送步骤；Pipe 不支持多个线程并发写，必须共享同一锁。
        with send_lock:
            connection.send(value)

    from src.services.run_log import TRACE_ID, EVENT_SINK
    TRACE_ID.set(config.get("_trace_id", ""))
    EVENT_SINK.set(lambda event: send(("event", event)))
    config = {**config, "_execution_deadline": deadline}
    try:
        if operation == "collect":
            from src.services.collector_orchestrator import CollectorOrchestrator
            send(("result", redact(CollectorOrchestrator(config).collect_all())))
        elif operation == "collection_job":
            from src.scheduler import JOBS
            job_id = payload["job_id"]
            if job_id not in {"hot_search", "cailianshe", "rss", "stock_data", "global_data"}:
                raise ValueError("不支持的采集任务")
            result = JOBS[job_id][1]({**config, "_isolated_collection": True})
            send(("result", result or {"status": "available"}))
        elif operation in ("diagnosis", "fund_diagnosis"):
            if operation == "fund_diagnosis":
                from src.services.fund_diagnosis import FundDiagnosisService as Service
            else:
                from src.services.stock_diagnosis import StockDiagnosisService as Service
            service = Service(config)
            service._isolated_worker = True
            send(("result", redact(service.diagnose(**payload))))
        else:
            from dataclasses import asdict
            from src.services.stock_chat import ChatTurn, StockChatSession
            chat = StockChatSession(config)
            chat._isolated_worker = True
            chat.turns = [ChatTurn(**t) for t in payload.pop("turns", [])]
            if operation == "chat_stream":
                for event in chat.ask_stream(**payload):
                    if event.get("type") == "done" and chat.turns:
                        event = {**event, "_internal_turn": asdict(chat.turns[-1])}
                    send(("event", redact(event)))
            else:
                turn = chat.ask(**payload, progress=lambda text: send(("event", {"type": "status", "text": text})))
                send(("result", redact(asdict(turn))))
    except Exception as error:
        send(("error", redact_text(error, 500) or type(error).__name__))
    finally:
        connection.close()


def isolated_events(operation: str, config: dict, payload: dict,
                    cancel: threading.Event | None = None) -> Iterator[tuple[str, Any]]:
    from src.services.run_log import TRACE_ID
    config = {**config, "_trace_id": TRACE_ID.get()}
    budget = ExecutionBudget.from_config(config)
    ctx = multiprocessing.get_context("spawn")
    reader, writer = ctx.Pipe(duplex=False)
    process = ctx.Process(target=_worker, args=(writer, operation, config, payload, budget.deadline), daemon=False)
    try:
        process.start()
    except BaseException:
        reader.close()
        writer.close()
        process.close()
        raise
    writer.close()
    try:
        while True:
            if cancel is not None and cancel.is_set():
                raise InterruptedError("已取消")
            budget.check()
            if reader.poll(min(0.1, budget.remaining())):
                try:
                    kind, value = reader.recv()
                except EOFError:
                    break
                if kind == "error":
                    raise RuntimeError(value)
                if kind == "event" and value.get("type") == "source_health":
                    from src.collectors.source_chain import source_health
                    source_health.record(value["dataset"], value["source"], value["ok"], value.get("error", ""), value.get("elapsed"), emit=False)
                    continue
                yield kind, value
                if kind == "result" or (kind == "event" and value.get("type") == "done"):
                    return
            elif not process.is_alive():
                break
        raise RuntimeError("分析进程退出，未返回完整结果")
    finally:
        reader.close()
        process.join(timeout=0.1)
        if process.is_alive():
            if os.name != "nt":
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            else:
                import subprocess
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True, timeout=5)
            # 进程可能尚未完成 setsid；确保父进程仍可以终止它。
            if process.is_alive():
                process.kill()
            process.join(timeout=1)
        process.close()


def isolated_result(operation: str, config: dict, payload: dict) -> dict:
    from src.services.run_log import EVENT_SINK
    for kind, value in isolated_events(operation, config, payload):
        if kind == "event" and EVENT_SINK.get():
            EVENT_SINK.get()(value)
        if kind == "result":
            return value
    raise RuntimeError("分析未返回结果")
