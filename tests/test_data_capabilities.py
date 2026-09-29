"""数据能力总览：服务与接口。"""

from __future__ import annotations

import pytest

from src.collectors import source_chain as sc
from src.collectors.source_chain import source_health
from src.services.data_capabilities import capabilities
from tests.test_api import env  # noqa: F401  复用 API 测试环境


@pytest.fixture(autouse=True)
def _fresh_state():
    sc._breakers.clear(); sc._last_good.clear(); source_health.reset()
    yield
    sc._breakers.clear(); sc._last_good.clear(); source_health.reset()


def _find(result, keyword):
    hits = [d for d in result if keyword in d["dataset"] or keyword in d["label"]]
    assert hits, f"没有找到数据集 {keyword}: {[(d['dataset'], d['label']) for d in result]}"
    return hits[0]


def _src(dataset, keyword):
    hits = [s for s in dataset["sources"] if keyword in s["name"].lower() or keyword in s["label"].lower()]
    assert hits, [(s["name"], s["label"]) for s in dataset["sources"]]
    return hits[0]


def test_structure():
    result = capabilities({})
    assert isinstance(result, list) and result
    for d in result:
        assert {"dataset", "label", "sources"} <= set(d)
        for s in d["sources"]:
            assert {"name", "label", "configured", "note", "health"} <= set(s)
            assert "status" in s["health"]


def test_realtime_order_follows_config():
    cfg = {"data_sources": {"realtime": ["pytdx", "tencent", "sina"]}}
    ds = _find(capabilities(cfg), "实时行情")
    assert [s["name"] for s in ds["sources"]] == ["pytdx", "tencent", "sina"]


def test_tushare_needs_token():
    off = _src(_find(capabilities({"data_sources": {}}), "个股日线"), "tushare")
    assert off["configured"] is False
    on = _src(_find(capabilities({"data_sources": {"tushare_token": "abc"}}), "个股日线"), "tushare")
    assert on["configured"] is True


def test_search_lists_only_configured_providers():
    cfg = {"search": {"enabled": True, "providers": ["bocha", "tavily"],
                      "bocha": {"api_keys": ["k1"]}, "tavily": {"api_keys": []}}}
    ds = _find(capabilities(cfg), "联网搜索")
    names = [s["name"] for s in ds["sources"]]
    assert "bocha" in names and "tavily" not in names


def test_search_disabled_has_note():
    cfg = {"search": {"enabled": False, "providers": ["bocha"], "bocha": {"api_keys": ["k1"]}}}
    ds = _find(capabilities(cfg), "联网搜索")
    text = " ".join(str(s.get("note", "")) for s in ds["sources"]) + str(ds.get("note", ""))
    assert text.strip()


def test_health_unknown_then_failing():
    result = capabilities({})
    for d in result:
        for s in d["sources"]:
            assert s["health"]["status"] == "unknown"
    source_health.record("实时行情", "腾讯财经(HTTP)", False, "timeout")
    ds = _find(capabilities({}), "实时行情")
    assert _src(ds, "tencent")["health"]["status"] == "failing"


def test_api_endpoint(env):  # noqa: F811
    client, app, config = env
    r = client.get("/api/v1/system/capabilities")
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list) and data and "sources" in data[0]
