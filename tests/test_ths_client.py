from __future__ import annotations

from src.collectors import ths_client as ths_mod


class _DummyBrowser:
    def __init__(self, json_responses=None, cookie_responses=None):
        self._json_responses = list(json_responses or [])
        self._cookie_responses = list(cookie_responses or [])
        self.fetch_json_calls = 0
        self.fetch_cookie_calls = 0

    def fetch_json(self, **kwargs):
        self.fetch_json_calls += 1
        if self._json_responses:
            return self._json_responses.pop(0)
        return None

    def fetch_with_cookies(self, **kwargs):
        self.fetch_cookie_calls += 1
        if self._cookie_responses:
            return self._cookie_responses.pop(0)
        return None


def test_request_json_cookie_fallback(monkeypatch):
    browser = _DummyBrowser(
        json_responses=[None, None],
        cookie_responses=[{"data": {"ok": True}}],
    )
    monkeypatch.setattr(ths_mod, "get_browser_client", lambda: browser)
    client = ths_mod.TongHuaShunClient()

    data = client.request_json("/demo", params={"k": "v"}, retries=2)

    assert isinstance(data, dict)
    assert data["data"]["ok"] is True
    assert browser.fetch_json_calls == 2
    assert browser.fetch_cookie_calls == 1


def test_request_json_cache_stale_fallback(monkeypatch):
    browser_1 = _DummyBrowser(
        json_responses=[{"data": {"stock_list": [{"name": "示例"}]}}],
    )
    monkeypatch.setattr(ths_mod, "get_browser_client", lambda: browser_1)
    client = ths_mod.TongHuaShunClient()

    first = client.request_json("/demo", params={"a": 1}, retries=1)
    assert first is not None

    browser_2 = _DummyBrowser(json_responses=[None], cookie_responses=[None])
    monkeypatch.setattr(ths_mod, "get_browser_client", lambda: browser_2)
    client.CACHE_TTL_SECONDS = 0
    client.CACHE_MAX_STALE_SECONDS = 60

    second = client.request_json("/demo", params={"a": 1}, retries=1)
    assert second == first


def test_fetch_hot_stocks_param_fallback():
    client = ths_mod.TongHuaShunClient()
    called_types = []

    def _fake_request_json(path, params=None, timeout=12000, retries=2):
        called_types.append(params.get("type"))
        if params.get("type") == "day":
            return {"data": {"stock_list": [{"name": "测试股", "code": "000001"}]}}
        return {"data": {"stock_list": []}}

    client.request_json = _fake_request_json  # type: ignore[method-assign]
    data = client.fetch_hot_stocks(stock_type="a", hot_type="hour", list_type="normal")

    assert data
    assert called_types[:2] == ["hour", "day"]


def test_fetch_hot_concepts_from_stock_tags_fallback():
    client = ths_mod.TongHuaShunClient()
    client.request_json = lambda *args, **kwargs: None  # type: ignore[method-assign]
    client.fetch_hot_stocks = lambda **kwargs: [  # type: ignore[method-assign]
        {"tag": {"concept_tag": ["算力", "AI"]}},
        {"tag": "算力/机器人"},
        {"tag": ["机器人", "AI"]},
    ]

    concepts = client.fetch_hot_concepts()
    rank = {item["name"]: item["hot_count"] for item in concepts}

    assert rank["算力"] == 2
    assert rank["AI"] == 2
    assert rank["机器人"] == 2
