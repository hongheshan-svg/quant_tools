"""OpenClaw / Grok Bot 共用的异步分析客户端，不隐式重提任务或发送通知。"""
from __future__ import annotations
import argparse
import json
import os
import time
from urllib.parse import quote
import httpx


class ResearchClient:
    def __init__(self, base_url: str, token: str = "", client=None):
        self.client = client or httpx.Client(base_url=base_url.rstrip("/") + "/api/v1/", timeout=30,
            headers={"Authorization": "Bearer " + token} if token else {}, follow_redirects=False)

    def request(self, method: str, path: str, **kwargs):
        response = self.client.request(method, path, **kwargs)
        response.raise_for_status()
        return response.json()

    def analyze(self, code: str) -> dict:
        return self.request("POST", "stocks/" + quote(code, safe="") + "/diagnosis")

    def wait(self, task_id: str, timeout: float = 300) -> dict:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            task = self.request("GET", "tasks/" + quote(task_id, safe=""))
            if task["status"] == "error":
                raise RuntimeError(task.get("error") or "分析失败")
            if task["status"] == "done":
                result = task.get("result") or {}
                if result.get("error"):
                    raise RuntimeError(result["error"])
                return result
            time.sleep(1)
        raise TimeoutError(f"等待超时，任务 {task_id} 可能仍在运行；请继续查询该任务，不要重新发起分析")

    def signals(self, code: str) -> dict:
        return self.request("GET", "signals", params={"code": code, "valid_only": True, "status": "active"})

    def close(self):
        self.client.close()


def main():
    parser = argparse.ArgumentParser(description="外部助手证券研究客户端")
    parser.add_argument("code")
    parser.add_argument("--signals", action="store_true")
    parser.add_argument("--task", help="继续等待已有任务，不重复分析")
    args = parser.parse_args()
    client = ResearchClient(os.environ.get("QUANT_BASE_URL", "http://127.0.0.1:8000"), os.environ.get("QUANT_API_TOKEN", ""))
    try:
        if args.signals:
            result = client.signals(args.code)
        else:
            task_id = args.task or client.analyze(args.code)["id"]
            result = client.wait(task_id)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        client.close()


if __name__ == "__main__":
    main()
