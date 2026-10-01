"""LiteLLM 接入与大模型用量统计。"""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from src.analyzers import llm_client as llm_mod
from src.analyzers.llm_client import LLMClient, build_route
from src.analyzers.llm_usage import caller_feature, record_usage, usage_summary


def _resp(text: str, prompt: int = 100, completion: int = 20):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))],
                           usage=SimpleNamespace(prompt_tokens=prompt, completion_tokens=completion))


class FakeLiteLLM:
    def __init__(self, replies):
        self.replies, self.calls = list(replies), []

    def completion(self, **kwargs):
        self.calls.append(kwargs)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


@pytest.fixture
def fake(monkeypatch):
    lib = FakeLiteLLM([])
    monkeypatch.setattr(llm_mod, "import_litellm", lambda: lib)
    return lib


def _client(tmp_path, **cfg) -> LLMClient:
    base = {"primary": {"provider": "deepseek", "api_key": "sk-1", "base_url": "https://api.deepseek.com", "model": "deepseek-chat"},
            "cache_enabled": True, "cache_path": str(tmp_path / "cache.sqlite3"), "max_retries": 0}
    return LLMClient({**base, **cfg})


def test_build_route():
    assert build_route({"provider": "deepseek", "api_key": "your-key", "model": "deepseek-chat"}) is None     # 占位符
    assert build_route({"provider": "qwen", "api_key": "k", "model": ""}) is None
    route = build_route({"provider": "qwen", "api_key": "k", "model": "qwen-plus", "base_url": "https://dashscope/v1"})
    assert (route.target, route.api_base) == ("openai/qwen-plus", "https://dashscope/v1")               # OpenAI 兼容
    assert build_route({"provider": "anthropic", "api_key": "k", "model": "claude-opus-5-5"}).target == "anthropic/claude-opus-5-5"
    ollama = build_route({"provider": "ollama", "model": "qwen2.5:14b", "base_url": "http://localhost:11434"})
    assert (ollama.target, ollama.api_key) == ("ollama/qwen2.5:14b", "")                                  # 本地模型不要 Key


def test_litellm_call_records_usage_and_cache(tmp_path, fake):
    fake.replies = [_resp('{"ok": true}', 1000, 200)]
    client = _client(tmp_path)
    assert client.chat_json("问题", system_message="系统") == {"ok": True}
    call = fake.calls[0]
    assert call["model"] == "openai/deepseek-chat" and call["api_base"] == "https://api.deepseek.com"
    assert call["response_format"] == {"type": "json_object"} and call["messages"][0] == {"role": "system", "content": "系统"}
    assert client.chat_json("问题", system_message="系统") == {"ok": True} and len(fake.calls) == 1     # 第二次命中缓存

    summary = usage_summary(tmp_path / "cache.sqlite3")
    assert summary["total"]["calls"] == 2 and summary["total"]["cached"] == 1 and summary["total"]["tokens"] == 1200
    assert summary["total"]["cost_usd"] > 0                                                   # deepseek 在 LiteLLM 价格表里
    assert summary["by_feature"][0]["key"] == "其他"                                          # 项目外的调用方归为其他
    assert summary["by_model"][0]["key"] == "deepseek/deepseek-chat"


def test_backup_and_failures(tmp_path, fake):
    fake.replies = [RuntimeError("401"), _resp("备用回答")]
    client = _client(tmp_path, cache_enabled=False,
                     backup={"provider": "anthropic", "api_key": "ak", "model": "claude-sonnet-5"})
    assert client.chat("你好", response_format="json") == "备用回答"
    assert fake.calls[1]["model"] == "anthropic/claude-sonnet-5" and "response_format" not in fake.calls[1]   # Claude 不传 JSON 模式
    summary = usage_summary(tmp_path / "cache.sqlite3")
    assert summary["total"]["failed"] == 1 and summary["total"]["calls"] == 2

    fake.replies = [RuntimeError("down")]
    with pytest.raises(RuntimeError, match="所有LLM模型均调用失败"):
        _client(tmp_path, cache_enabled=False, backup={"provider": "openai", "api_key": "your-key", "model": "gpt-4o"}).chat("x")


def test_openai_backend(tmp_path, monkeypatch, fake):
    created = []

    class FakeOpenAI:
        def __init__(self, api_key, base_url):
            created.append((api_key, base_url))
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=lambda **kw: _resp("直连")))

    import openai

    monkeypatch.setattr(openai, "OpenAI", FakeOpenAI)
    client = _client(tmp_path, backend="openai", cache_enabled=False)
    assert client.chat("x") == "直连" and created == [("sk-1", "https://api.deepseek.com")] and fake.calls == []
    claude = _client(tmp_path, backend="openai", cache_enabled=False,
                     primary={"provider": "anthropic", "api_key": "k", "model": "claude-opus-5-5"})
    with pytest.raises(RuntimeError):
        claude.chat("x")                                                                     # 原生平台必须走 LiteLLM


def test_usage_summary_grouping(tmp_path):
    path = tmp_path / "u.sqlite3"
    now = time.time()
    record_usage(path, provider="deepseek", model="deepseek-chat", feature="个股诊断", prompt_tokens=100, completion_tokens=50, cost_usd=0.01)
    record_usage(path, provider="deepseek", model="deepseek-chat", feature="AI 问股", prompt_tokens=10, completion_tokens=5)
    record_usage(path, provider="qwen", model="qwen-plus", feature="个股诊断", success=False)
    summary = usage_summary(path, days=7, now=now)
    assert summary["total"] == {"calls": 3, "cached": 0, "failed": 1, "tokens": 165, "cost_usd": 0.01,
                                "prompt_tokens": 110, "completion_tokens": 55}
    assert [(r["key"], r["calls"], r["tokens"]) for r in summary["by_feature"]] == [("个股诊断", 2, 150), ("AI 问股", 1, 15)]
    assert len(summary["by_day"]) == 1
    assert usage_summary(path, days=7, now=now + 30 * 86400)["total"]["calls"] == 0
    assert usage_summary(tmp_path / "none.sqlite3")["total"]["calls"] == 0


def test_caller_feature_mapping():
    import types

    module = types.ModuleType("src.services.stock_chat")
    exec("from src.analyzers.llm_usage import caller_feature\ndef f():\n    return caller_feature()", module.__dict__)
    assert module.f() == "AI 问股"
    assert caller_feature() == "其他"


def test_no_usable_model_explains_missing_key(tmp_path, fake):
    """主模型和备用模型都只有 your- 占位 Key 时，报错要说明去哪里填，而不是笼统的「均调用失败」"""
    client = _client(tmp_path, cache_enabled=False,
                     primary={"provider": "deepseek", "api_key": "your-deepseek-api-key-here", "model": "deepseek-chat"},
                     backup={"provider": "openai", "api_key": "your-openai-api-key-here", "model": "gpt-4o"})
    with pytest.raises(RuntimeError, match="所有LLM模型均调用失败：未配置可用的大模型 API Key"):
        client.chat("x")
    with pytest.raises(RuntimeError, match="未配置可用的大模型 API Key"):
        list(client.chat_stream("x"))
    assert fake.calls == []
