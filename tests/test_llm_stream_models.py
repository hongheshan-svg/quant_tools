"""LLMClient.chat_stream 与 list_models，以及设置接口的多 Key 掩码。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src import settings_store
from src.analyzers import llm_client as llm_mod
from src.analyzers.llm_client import LLMClient

# 复用 test_api 的 env fixture
from tests.test_api import env  # noqa: F401


@pytest.fixture(autouse=True)
def _reset():
    llm_mod.reset_key_state()
    yield
    llm_mod.reset_key_state()


def _chunk(text, usage=None):
    return SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=text))], usage=usage)


class FakeStreamLib:
    def __init__(self):
        self.plans: list = []          # 每次调用一项：("ok", [texts]) / ("fail",) / ("mid", [texts])
        self.calls: list[dict] = []

    def completion(self, **kwargs):
        self.calls.append(kwargs)
        plan = self.plans.pop(0) if self.plans else ("ok", ["默认"])
        kind = plan[0]
        if kind == "fail":
            raise RuntimeError("boom")

        def gen():
            for t in plan[1]:
                yield _chunk(t)
            if kind == "mid":
                raise RuntimeError("mid-stream error")
        return gen()


@pytest.fixture
def lib(monkeypatch):
    fake = FakeStreamLib()
    monkeypatch.setattr(llm_mod, "import_litellm", lambda: fake)
    monkeypatch.setattr(llm_mod.time, "sleep", lambda s: None)
    return fake


@pytest.fixture
def usage_calls(monkeypatch):
    calls = []
    monkeypatch.setattr(llm_mod, "record_usage", lambda *a, **k: calls.append((a, k)))
    return calls


BACKUP = {"provider": "anthropic", "api_key": "bk", "model": "claude-sonnet-5"}


def _client(tmp_path, backup=None) -> LLMClient:
    cfg = {"primary": {"provider": "deepseek", "api_key": "k1", "model": "deepseek-chat"},
           "cache_enabled": True, "cache_path": str(tmp_path / "c.sqlite3"), "max_retries": 0}
    if backup:
        cfg["backup"] = backup
    return LLMClient(cfg)


# ---------- chat_stream ----------

def test_stream_concatenates_and_passes_stream_flag(tmp_path, lib, usage_calls):
    lib.plans = [("ok", ["你", "好", "，世界"])]
    out = list(_client(tmp_path).chat_stream("hi", system_message="sys"))
    assert "".join(out) == "你好，世界"
    assert lib.calls[0]["stream"] is True
    assert lib.calls[0]["messages"][0] == {"role": "system", "content": "sys"}


def test_stream_skips_empty_chunks(tmp_path, lib, usage_calls):
    lib.plans = [("ok", ["a", None, "", "b"])]
    assert "".join(_client(tmp_path).chat_stream("hi")) == "ab"


def test_stream_falls_back_before_first_chunk(tmp_path, lib, usage_calls):
    lib.plans = [("fail",), ("ok", ["备", "用"])]
    out = "".join(_client(tmp_path, BACKUP).chat_stream("hi"))
    assert out == "备用"
    assert [c["model"] for c in lib.calls] == ["openai/deepseek-chat", "anthropic/claude-sonnet-5"]


def test_stream_error_after_first_chunk_propagates(tmp_path, lib, usage_calls):
    lib.plans = [("mid", ["部分"]), ("ok", ["不该用到"])]
    got = []
    with pytest.raises(Exception):
        for piece in _client(tmp_path, BACKUP).chat_stream("hi"):
            got.append(piece)
    assert got == ["部分"]
    assert len(lib.calls) == 1                       # 没有切换备用模型


def test_stream_all_fail_raises_runtime_error(tmp_path, lib, usage_calls):
    lib.plans = [("fail",), ("fail",)]
    with pytest.raises(RuntimeError):
        list(_client(tmp_path, BACKUP).chat_stream("hi"))


def test_stream_no_cache(tmp_path, lib, usage_calls):
    lib.plans = [("ok", ["一"]), ("ok", ["二"])]
    client = _client(tmp_path)
    assert "".join(client.chat_stream("same")) == "一"
    assert "".join(client.chat_stream("same")) == "二"
    assert len(lib.calls) == 2


def test_stream_records_usage_once(tmp_path, lib, usage_calls):
    lib.plans = [("ok", ["a", "b", "c"])]
    list(_client(tmp_path).chat_stream("hi"))
    assert len(usage_calls) == 1


# ---------- list_models ----------

class FakeHttpResp:
    def __init__(self, status=200, payload=None):
        self.status_code, self._payload = status, payload or {}
        self.text = "err"

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            import httpx

            raise httpx.HTTPStatusError("bad", request=httpx.Request("GET", "http://x"),
                                        response=httpx.Response(self.status_code))


@pytest.fixture
def http(monkeypatch):
    import httpx

    state = {"calls": [], "resp": FakeHttpResp()}

    def fake_get(url, **kwargs):
        state["calls"].append({"url": str(url), **kwargs})
        return state["resp"]

    monkeypatch.setattr(httpx, "get", fake_get)
    return state


def test_list_models_openai_compatible(http):
    http["resp"] = FakeHttpResp(payload={"data": [{"id": "b-model"}, {"id": "a-model"}, {"id": "b-model"}]})
    out = llm_mod.list_models({"provider": "deepseek", "api_key": "sk-abc", "base_url": "https://api.deepseek.com/v1/"})
    assert out == ["a-model", "b-model"]
    call = http["calls"][0]
    assert call["url"] == "https://api.deepseek.com/v1/models"
    assert call["headers"]["Authorization"] == "Bearer sk-abc"


def test_list_models_uses_first_of_multiple_keys(http):
    http["resp"] = FakeHttpResp(payload={"data": [{"id": "m"}]})
    llm_mod.list_models({"provider": "deepseek", "api_key": ["k1", "k2"], "base_url": "https://x.com/v1"})
    assert http["calls"][0]["headers"]["Authorization"] == "Bearer k1"


def test_list_models_anthropic(http):
    http["resp"] = FakeHttpResp(payload={"data": [{"id": "claude-b"}, {"id": "claude-a"}]})
    out = llm_mod.list_models({"provider": "anthropic", "api_key": "ak-1"})
    assert out == ["claude-a", "claude-b"]
    call = http["calls"][0]
    assert call["url"].startswith("https://api.anthropic.com/v1/models")
    assert call["headers"]["x-api-key"] == "ak-1"


def test_list_models_gemini(http):
    http["resp"] = FakeHttpResp(payload={"models": [{"name": "models/gemini-pro"}, {"name": "models/gemini-flash"}]})
    out = llm_mod.list_models({"provider": "gemini", "api_key": "gk-1"})
    assert out == ["gemini-flash", "gemini-pro"]
    call = http["calls"][0]
    assert "generativelanguage.googleapis.com" in call["url"]
    assert (call.get("params") or {}).get("key") == "gk-1" or "key=gk-1" in call["url"]


def test_list_models_ollama(http):
    http["resp"] = FakeHttpResp(payload={"models": [{"name": "qwen2:7b"}, {"name": "llama3"}]})
    out = llm_mod.list_models({"provider": "ollama", "api_key": "", "base_url": "http://localhost:11434"})
    assert out == ["llama3", "qwen2:7b"]
    assert http["calls"][0]["url"].rstrip("/").endswith("/api/tags")


def test_list_models_401_error_hides_key(http):
    http["resp"] = FakeHttpResp(status=401)
    with pytest.raises(RuntimeError) as exc:
        llm_mod.list_models({"provider": "deepseek", "api_key": "sk-supersecret", "base_url": "https://x.com/v1"})
    assert "sk-supersecret" not in str(exc.value)


# ---------- API ----------

def _llm_cfg(config, keys):
    config["llm"]["primary"]["api_key"] = keys


def test_get_settings_masks_multi_keys(env):  # noqa: F811
    client, app, config = env
    _llm_cfg(config, ["sk-aaaa1111", "sk-bbbb2222"])
    primary = client.get("/api/v1/settings/llm").json()["llm"]["primary"]
    assert primary["api_key"] == ["******1111", "******2222"]
    _llm_cfg(config, ["sk-only9999"])
    assert client.get("/api/v1/settings/llm").json()["llm"]["primary"]["api_key"] in ("******9999", ["******9999"])
    _llm_cfg(config, "sk-secret-1234")
    assert client.get("/api/v1/settings/llm").json()["llm"]["primary"]["api_key"] == "******1234"


def _put(client, monkeypatch, config, keys):
    from src import config_loader

    monkeypatch.setattr(config_loader, "reload_config", lambda: config)
    body = {"llm": {"primary": {"provider": "deepseek", "api_key": keys, "model": "deepseek-chat"}}}
    assert client.put("/api/v1/settings/llm", json=body).json() == {"ok": True}
    return settings_store.read_settings()["llm"]["primary"]["api_key"]


def test_put_restores_masked_list(env, monkeypatch):  # noqa: F811
    client, app, config = env
    _llm_cfg(config, ["sk-aaaa1111", "sk-bbbb2222"])
    assert _put(client, monkeypatch, config, ["******1111", "******2222"]) == ["sk-aaaa1111", "sk-bbbb2222"]


def test_put_mixed_new_and_masked(env, monkeypatch):  # noqa: F811
    client, app, config = env
    _llm_cfg(config, ["sk-aaaa1111", "sk-bbbb2222"])
    assert _put(client, monkeypatch, config, ["******2222", "sk-new-key-7777"]) == ["sk-bbbb2222", "sk-new-key-7777"]


def test_put_unknown_mask_dropped_and_single_becomes_string(env, monkeypatch):  # noqa: F811
    client, app, config = env
    _llm_cfg(config, ["sk-aaaa1111", "sk-bbbb2222"])
    assert _put(client, monkeypatch, config, ["******1111", "******0000"]) == "sk-aaaa1111"


def test_put_single_masked_string_kept(env, monkeypatch):  # noqa: F811
    client, app, config = env
    _llm_cfg(config, "sk-secret-1234")
    assert _put(client, monkeypatch, config, "******1234") == "sk-secret-1234"


def test_models_endpoint_restores_real_keys(env, monkeypatch):  # noqa: F811
    client, app, config = env
    _llm_cfg(config, ["sk-aaaa1111", "sk-bbbb2222"])
    seen = {}

    def fake_list(role_cfg, timeout=10):
        seen["cfg"] = role_cfg
        return ["m1", "m2"]

    monkeypatch.setattr(llm_mod, "list_models", fake_list)
    body = {"role": "primary", "config": {"provider": "deepseek", "api_key": ["******1111", "******2222"],
                                          "base_url": "https://api.deepseek.com"}}
    res = client.post("/api/v1/settings/llm/models", json=body)
    assert res.status_code == 200 and res.json() == {"models": ["m1", "m2"]}
    keys = seen["cfg"]["api_key"]
    assert (keys if isinstance(keys, list) else [keys]) == ["sk-aaaa1111", "sk-bbbb2222"]


def test_models_endpoint_error_is_400(env, monkeypatch):  # noqa: F811
    client, app, config = env

    def boom(role_cfg, timeout=10):
        raise RuntimeError("认证失败 401")

    monkeypatch.setattr(llm_mod, "list_models", boom)
    res = client.post("/api/v1/settings/llm/models", json={"role": "primary", "config": {"provider": "deepseek", "api_key": "x"}})
    assert res.status_code == 400
