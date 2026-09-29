"""联网搜索设置接口：掩码、按后 4 位还原、测试接口。"""

from __future__ import annotations

from src import config_loader, settings_store
from src.collectors import news_search
from tests.test_api import _wait, env  # noqa: F401


def _body(keys, searxng="https://sx.example.com"):
    """keys: {provider: Key 或 Key 列表}；配置形状 search.<provider>.api_keys。"""
    search = {"enabled": True, "providers": ["bocha", "tavily", "searxng"], "max_results": 6, "days": 5, "cache_minutes": 10,
              "searxng": {"base_urls": [searxng]}}
    for p, v in keys.items():
        search[p] = {"api_keys": v if isinstance(v, list) else [k.strip() for k in v.split(",") if k.strip()]}
    return {"search": search}


def _keys(saved, provider):
    v = saved[provider]["api_keys"]
    return [k.strip() for k in v.split(",")] if isinstance(v, str) else list(v)


def _patch_reload(monkeypatch, config):
    monkeypatch.setattr(config_loader, "reload_config", lambda: {**config, "search": settings_store.read_settings().get("search", {})})


def test_get_masks_keys(env, monkeypatch):  # noqa: F811
    client, app, config = env
    settings_store.save_section("search", _body({"bocha": "sk-bocha-abcd", "tavily": ["t-key-1111", "t-key-2222"]})["search"])
    monkeypatch.setattr(config_loader, "reload_config", lambda: {**config, "search": settings_store.read_settings()["search"]})
    from api.app import apply_config
    apply_config(app, config_loader.reload_config())

    data = client.get("/api/v1/settings/search").json()
    assert set(data) >= {"search", "providers"}
    assert data["providers"]["bocha"] == "博查" and data["providers"]["searxng"] == "SearXNG"
    text = str([data["search"]["bocha"], data["search"]["tavily"]])
    assert "sk-bocha-abcd" not in text and "t-key-1111" not in text and "t-key-2222" not in text
    assert "abcd" in text and "1111" in text and "2222" in text and "******" in text
    assert "https://sx.example.com" in str(data["search"]["searxng"])


def test_get_default_without_config(env):  # noqa: F811
    client, _, _ = env
    data = client.get("/api/v1/settings/search").json()
    assert "search" in data and "bocha" in data["providers"]


def test_put_restores_masked_and_keeps_new_keys(env, monkeypatch):  # noqa: F811
    client, app, config = env
    _patch_reload(monkeypatch, config)
    assert client.put("/api/v1/settings/search", json=_body({"bocha": "sk-bocha-abcd", "tavily": "tk-old-9999"})).status_code == 200
    assert _keys(settings_store.read_settings()["search"], "bocha") == ["sk-bocha-abcd"]

    body = _body({"bocha": "******abcd", "tavily": "tk-new-0000"})
    assert client.put("/api/v1/settings/search", json=body).status_code == 200
    saved = settings_store.read_settings()["search"]
    assert _keys(saved, "bocha") == ["sk-bocha-abcd"]          # 掩码按后 4 位还原
    assert _keys(saved, "tavily") == ["tk-new-0000"]            # 新 Key 原样保存
    assert saved["max_results"] == 6 and saved["days"] == 5 and saved["enabled"] is True
    assert "https://sx.example.com" in str(saved["searxng"]["base_urls"])
    assert _keys(app.state.pipeline.config["search"], "bocha") == ["sk-bocha-abcd"]


def test_put_restores_masked_keys_in_list(env, monkeypatch):  # noqa: F811
    client, _, config = env
    _patch_reload(monkeypatch, config)
    client.put("/api/v1/settings/search", json=_body({"bocha": ["key-aaaa-1111", "key-bbbb-2222"]}))
    client.put("/api/v1/settings/search", json=_body({"bocha": ["******1111", "******2222", "key-new-3333"]}))
    keys = _keys(settings_store.read_settings()["search"], "bocha")
    assert keys == ["key-aaaa-1111", "key-bbbb-2222", "key-new-3333"]


def test_search_test_endpoint(env, monkeypatch):  # noqa: F811
    client, _, _ = env
    seen = {}

    def fake(config, query):
        seen["config"], seen["query"] = config, query
        return [{"provider": "bocha", "label": "博查", "ok": True, "count": 2, "error": "", "samples": ["标题1"]},
                {"provider": "tavily", "label": "Tavily", "ok": False, "count": 0, "error": "HTTP 401", "samples": []}]
    monkeypatch.setattr(news_search, "test_providers", fake)
    resp = client.post("/api/v1/settings/search/test", json={"search": _body({"bocha": "k"})["search"], "query": "比亚迪"})
    assert resp.status_code == 200
    body = resp.json()
    if "id" in body and "results" not in body:       # 若实现走后台任务，轮询到结束
        body = _wait(client, body)["result"]
    assert body["results"][0]["provider"] == "bocha" and body["results"][1]["error"] == "HTTP 401"
    assert seen["query"] == "比亚迪" and seen["config"]["search"]["providers"][0] == "bocha"


def test_search_test_endpoint_uses_saved_key_when_masked(env, monkeypatch):  # noqa: F811
    client, _, config = env
    _patch_reload(monkeypatch, config)
    client.put("/api/v1/settings/search", json=_body({"bocha": "sk-bocha-abcd"}))
    seen = {}
    monkeypatch.setattr(news_search, "test_providers", lambda config, query: seen.update(config=config) or [])
    client.post("/api/v1/settings/search/test", json={"search": _body({"bocha": "******abcd"})["search"], "query": "x"})
    assert "sk-bocha-abcd" in str(seen["config"]["search"]["bocha"])
