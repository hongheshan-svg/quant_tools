from __future__ import annotations

import pytest

from src.analyzers.llm_client import LLMClient


def _client_returning(monkeypatch, raw: str) -> LLMClient:
    client = LLMClient({"cache_enabled": False})
    monkeypatch.setattr(client, "chat", lambda **kwargs: raw)
    return client


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('{"items": [{"code": "600519"}]}', {"items": [{"code": "600519"}]}),
        ('```json\n{"items": [{"code": "600519"}]}\n```', {"items": [{"code": "600519"}]}),
        # 尾逗号
        ('{"market_outlook": "震荡", "items": [{"code": "600519"},],}', {"market_outlook": "震荡", "items": [{"code": "600519"}]}),
        # JSON 前后夹杂说明文字
        ('好的，预测如下：{"items": [{"code": "600519"}]} 以上仅供参考', {"items": [{"code": "600519"}]}),
    ],
)
def test_chat_json_repairs_common_format_errors(monkeypatch, raw, expected):
    assert _client_returning(monkeypatch, raw).chat_json("q") == expected


def test_truncated_output_drops_incomplete_last_item(monkeypatch):
    raw = '{"items": [{"code": "600519", "name": "贵州茅台"}, {"code": "0025'
    assert _client_returning(monkeypatch, raw).chat_json("q") == {
        "items": [{"code": "600519", "name": "贵州茅台"}]
    }


def test_truncated_non_items_output_is_cut_to_last_complete_object(monkeypatch):
    raw = '{"summary": "利好", "key_events": [{"event": "降息"}, {"event": "关税'
    assert _client_returning(monkeypatch, raw).chat_json("q") == {
        "summary": "利好",
        "key_events": [{"event": "降息"}],
    }


def test_unparseable_output_returns_empty_dict(monkeypatch):
    assert _client_returning(monkeypatch, "服务繁忙，请稍后再试").chat_json("q") == {}
