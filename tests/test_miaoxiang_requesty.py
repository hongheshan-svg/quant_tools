"""供应商只进入实际支持的补充链；严格人民币金额、缓存和共享请求准入。"""

import httpx
import pytest

from src.collectors.miaoxiang import MiaoxiangClient, number, _cache
from src.analyzers.llm_client import build_route


@pytest.mark.parametrize("raw,expected", [("-93.64万元", -936400), ("1.2亿", 120000000), ("0元", 0), ("5%", None), ("20美元", None), ("HKD30万", None), ("NaN", None), ("1.2亿港元", None), (True, None)])
def test_money_only_accepts_finite_cny(raw, expected):
    assert number(raw, money=True) == expected


def test_supplement_only_capability_and_cache(monkeypatch):
    _cache.clear()
    calls = []
    table = {"nameMap": {"1": "主力净流入资金"}, "table": {"headName": ["2025-10-02", "2025-10-01"], "1": ["0元", "-1.2万"]}}
    def post(url, **kwargs):
        calls.append(kwargs)
        return httpx.Response(200, json={"status": 0, "data": {"dataTableDTOList": [table]}}, request=httpx.Request("POST", url))
    monkeypatch.setattr(httpx, "post", post)
    client = MiaoxiangClient({"data_sources": {"miaoxiang_api_key": "fixture"}})
    assert client.query("510300", "flow") is None
    assert client.query("600519", "daily") is None
    result = client.query("600519", "flow")
    assert result["net_inflow"] == 0 and result["inflow_5d"] is None
    assert result["provider_timestamp"] is None
    assert client.query("600519", "flow") == result and len(calls) == 1
    result["net_inflow"] = 99
    assert client.query("600519", "flow")["net_inflow"] == 0


def test_requesty_vendor_model_prefix_preserved():
    route = build_route({"provider": "requesty", "model": "anthropic/claude-sonnet-4-6", "api_key": "fixture"})
    assert route.target == "openai/anthropic/claude-sonnet-4-6"
    assert route.api_base == "https://router.requesty.ai/v1"
