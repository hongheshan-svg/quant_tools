"""LLMClient.chat_vision：多模态消息、路由顺序、去重、不走缓存。"""

from __future__ import annotations

import base64
from types import SimpleNamespace

import pytest

from src.analyzers import llm_client as llm_mod
from src.analyzers.llm_client import LLMClient


def _resp(text: str):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))],
                           usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5))


class FakeLiteLLM:
    def __init__(self, replies=None):
        self.replies, self.calls = list(replies or []), []

    def completion(self, **kwargs):
        self.calls.append(kwargs)
        reply = self.replies.pop(0) if self.replies else _resp("ok")
        if isinstance(reply, Exception):
            raise reply
        return reply


@pytest.fixture
def fake(monkeypatch):
    lib = FakeLiteLLM()
    monkeypatch.setattr(llm_mod, "import_litellm", lambda: lib)
    return lib


PRIMARY = {"provider": "deepseek", "api_key": "sk-1", "base_url": "https://api.deepseek.com", "model": "deepseek-chat"}
VISION = {"provider": "qwen", "api_key": "sk-v", "base_url": "https://dashscope/v1", "model": "qwen-vl-max"}


def _client(tmp_path, **cfg) -> LLMClient:
    base = {"primary": PRIMARY, "cache_enabled": True, "cache_path": str(tmp_path / "cache.sqlite3"), "max_retries": 0}
    return LLMClient({**base, **cfg})


def test_message_format_and_base64(tmp_path, fake):
    fake.replies = [_resp("识别结果")]
    img1, img2 = b"\x89PNG\r\n\x1a\n\x00\x01\xff", b"\xff\xd8\xff\xe0jpeg-bytes"
    out = _client(tmp_path).chat_vision("请识别", [(img1, "image/png"), (img2, "image/jpeg")], system_message="系统")
    assert out == "识别结果"
    messages = fake.calls[0]["messages"]
    assert messages[0] == {"role": "system", "content": "系统"}
    content = messages[-1]["content"]
    assert messages[-1]["role"] == "user" and isinstance(content, list) and len(content) == 3
    assert content[0] == {"type": "text", "text": "请识别"}
    for part, raw, mime in ((content[1], img1, "image/png"), (content[2], img2, "image/jpeg")):
        assert part["type"] == "image_url"
        url = part["image_url"]["url"]
        prefix = f"data:{mime};base64,"
        assert url.startswith(prefix)
        assert base64.b64decode(url[len(prefix):]) == raw


def test_no_system_message(tmp_path, fake):
    _client(tmp_path).chat_vision("p", [(b"x", "image/png")])
    messages = fake.calls[0]["messages"]
    assert [m["role"] for m in messages] == ["user"]


def test_vision_route_first_then_primary_then_backup(tmp_path, fake):
    fake.replies = [RuntimeError("v"), RuntimeError("p"), _resp("备用")]
    client = _client(tmp_path, vision=VISION, backup={"provider": "anthropic", "api_key": "ak", "model": "claude-sonnet-5"})
    assert client.chat_vision("p", [(b"x", "image/png")]) == "备用"
    assert [c["model"] for c in fake.calls] == ["openai/qwen-vl-max", "openai/deepseek-chat", "anthropic/claude-sonnet-5"]


def test_vision_unconfigured_uses_primary(tmp_path, fake):
    for vision in (None, {}, {"provider": "", "model": "", "api_key": "", "base_url": ""}):
        fake.calls.clear()
        cfg = {} if vision is None else {"vision": vision}
        _client(tmp_path, **cfg).chat_vision("p", [(b"x", "image/png")])
        assert [c["model"] for c in fake.calls] == ["openai/deepseek-chat"]


def test_same_route_tried_once(tmp_path, fake):
    fake.replies = [RuntimeError("down")]
    same = dict(PRIMARY)
    client = _client(tmp_path, vision=same, backup=dict(PRIMARY))
    with pytest.raises(RuntimeError, match="图片识别失败"):
        client.chat_vision("p", [(b"x", "image/png")])
    assert len(fake.calls) == 1


def test_placeholder_backup_skipped(tmp_path, fake):
    fake.replies = [RuntimeError("down")]
    client = _client(tmp_path, backup={"provider": "openai", "api_key": "your-key", "model": "gpt-4o"})
    with pytest.raises(RuntimeError, match="图片识别失败"):
        client.chat_vision("p", [(b"x", "image/png")])
    assert len(fake.calls) == 1


def test_all_failed_raises(tmp_path, fake):
    fake.replies = [RuntimeError("a"), RuntimeError("b")]
    client = _client(tmp_path, vision=VISION)
    with pytest.raises(RuntimeError, match="图片识别失败"):
        client.chat_vision("p", [(b"x", "image/png")])
    assert len(fake.calls) == 2


def test_no_cache(tmp_path, fake):
    client = _client(tmp_path, cache_enabled=True)
    args = ("同样的提示", [(b"same", "image/png")])
    client.chat_vision(*args)
    client.chat_vision(*args)
    assert len(fake.calls) == 2


def test_max_tokens_passed(tmp_path, fake):
    _client(tmp_path).chat_vision("p", [(b"x", "image/png")], max_tokens=777)
    assert fake.calls[0]["max_tokens"] == 777
