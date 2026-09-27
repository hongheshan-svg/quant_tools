from __future__ import annotations

import threading

from src.collectors import em_client as em_mod


def test_requests_from_any_thread_run_on_browser_thread(monkeypatch):
    """Playwright 同步对象只能在创建它的线程使用：所有请求都必须在同一个专用线程执行。"""
    monkeypatch.setattr(em_mod.EastMoneyClient, "_instance", None)
    client = em_mod.EastMoneyClient()
    seen = []

    def fake_request(self, url, params, timeout, referer):
        seen.append((threading.current_thread().name, url, referer))
        return {"data": {"url": url}}

    monkeypatch.setattr(em_mod.EastMoneyClient, "_request_in_browser_thread", fake_request)

    results = {}
    threads = [
        threading.Thread(target=lambda i=i: results.__setitem__(i, client.request_json(f"https://x/{i}", referer="https://ref/")))
        for i in range(4)
    ]
    [t.start() for t in threads]
    [t.join() for t in threads]

    assert {i: r["data"]["url"] for i, r in results.items()} == {i: f"https://x/{i}" for i in range(4)}
    assert len({name for name, _, _ in seen}) == 1 and seen[0][0].startswith("em-playwright")
    assert all(referer == "https://ref/" for _, _, referer in seen)
    client._executor.shutdown(wait=False)
    monkeypatch.setattr(em_mod.EastMoneyClient, "_instance", None)


def test_request_failure_returns_none(monkeypatch):
    monkeypatch.setattr(em_mod.EastMoneyClient, "_instance", None)
    client = em_mod.EastMoneyClient()

    def boom(self, url, params, timeout, referer):
        raise RuntimeError("socket hang up")

    monkeypatch.setattr(em_mod.EastMoneyClient, "_request_in_browser_thread", boom)
    assert client.request_json("https://x/") is None
    client._executor.shutdown(wait=False)
    monkeypatch.setattr(em_mod.EastMoneyClient, "_instance", None)
