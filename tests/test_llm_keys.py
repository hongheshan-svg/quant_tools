"""LLM 多 Key：parse_keys、build_route、轮换与 429/401 冷却、备用模型切换。"""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from src.analyzers import llm_client as llm_mod
from src.analyzers.llm_client import LLMClient, build_route, parse_keys


class HttpError(Exception):
    def __init__(self, status_code: int):
        super().__init__(f"HTTP {status_code}")
        self.status_code = status_code


def _resp(text: str = "ok"):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))],
                           usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1))


class FakeLiteLLM:
    """按 api_key 决定结果：behaviors[key] 为异常则抛出，否则返回 ok:<key>。"""

    def __init__(self):
        self.behaviors: dict[str, Exception] = {}
        self.calls: list[dict] = []

    def completion(self, **kwargs):
        self.calls.append(kwargs)
        err = self.behaviors.get(kwargs.get("api_key"))
        if err is not None:
            raise err
        return _resp(f"ok:{kwargs.get('api_key')}")

    @property
    def keys_used(self):
        return [c["api_key"] for c in self.calls]


@pytest.fixture(autouse=True)
def _reset():
    llm_mod.reset_key_state()
    yield
    llm_mod.reset_key_state()


@pytest.fixture
def fake(monkeypatch):
    lib = FakeLiteLLM()
    monkeypatch.setattr(llm_mod, "import_litellm", lambda: lib)
    monkeypatch.setattr(llm_mod.time, "sleep", lambda s: None)
    return lib


def _primary(keys):
    return {"provider": "deepseek", "api_key": keys, "base_url": "https://api.deepseek.com", "model": "deepseek-chat"}


def _client(tmp_path, primary=None, **cfg) -> LLMClient:
    base = {"primary": primary or _primary(["k1", "k2", "k3"]), "cache_enabled": False,
            "cache_path": str(tmp_path / "cache.sqlite3"), "max_retries": 0}
    return LLMClient({**base, **cfg})


# ---------- parse_keys / build_route ----------

def test_parse_keys_variants():
    assert parse_keys(None) == [] and parse_keys("") == [] and parse_keys([]) == []
    assert parse_keys("a, b\nc") == ["a", "b", "c"]
    assert parse_keys(" a ,a,, b\n\n a ") == ["a", "b"]
    assert parse_keys(["a", " b ", "", "a", None, "your-key-here"]) == ["a", "b"]
    assert parse_keys("your-api-key,real") == ["real"]
    assert parse_keys("your-api-key") == []


def test_build_route_multi_keys():
    route = build_route(_primary("k1,k2\nk1"))
    assert route.api_keys == ("k1", "k2") and route.api_key == "k1"
    route = build_route(_primary(["a", "your-x", "b"]))
    assert tuple(route.api_keys) == ("a", "b") and route.api_key == "a"
    assert build_route(_primary("single")).api_keys == ("single",)


def test_build_route_no_valid_key():
    assert build_route(_primary([])) is None
    assert build_route(_primary("")) is None
    assert build_route(_primary(["your-a", "your-b"])) is None
    assert build_route({"provider": "anthropic", "model": "claude", "api_key": ""}) is None
    assert build_route({"provider": "ollama", "model": "qwen", "api_key": ""}) is not None


# ---------- 轮换 ----------

def test_start_key_rotates_across_calls(tmp_path, fake):
    client = _client(tmp_path)
    for i in range(6):
        client.chat(f"问题{i}")
    assert set(fake.keys_used[:3]) == {"k1", "k2", "k3"}      # 连续三次起始 Key 各不相同
    assert len(set(fake.keys_used)) == 3 and len(fake.calls) == 6


def test_single_key_always_used(tmp_path, fake):
    client = _client(tmp_path, _primary("only"))
    for i in range(3):
        client.chat(f"q{i}")
    assert set(fake.keys_used) == {"only"}


def test_429_switches_to_next_key_in_same_call(tmp_path, fake):
    fake.behaviors = {"k1": HttpError(429)}
    client = _client(tmp_path, _primary(["k1", "k2"]))
    outs = [client.chat(f"q{i}") for i in range(3)]
    assert all(o == "ok:k2" for o in outs)
    # k1 至多被尝试一次（之后进入冷却）
    assert fake.keys_used.count("k1") <= 1


@pytest.mark.parametrize("status", [401, 403, 429])
def test_bad_key_status_triggers_next_key(tmp_path, fake, status):
    fake.behaviors = {"k1": HttpError(status), "k2": HttpError(status)}
    client = _client(tmp_path, _primary(["k1", "k2", "k3"]))
    assert client.chat("q") == "ok:k3"


def test_cooldown_and_recovery(tmp_path, fake, monkeypatch):
    now = {"t": 1000.0}
    monkeypatch.setattr(llm_mod.time, "monotonic", lambda: now["t"])
    monkeypatch.setattr(llm_mod.time, "time", lambda: now["t"])   # 实现用哪个时钟都行
    fake.behaviors = {"k1": HttpError(429)}
    client = _client(tmp_path, _primary(["k1", "k2"]))
    assert client.chat("a") == "ok:k2"
    fake.calls.clear()
    now["t"] += 300                                    # 冷却期内（10 分钟）
    for i in range(4):
        assert client.chat(f"b{i}") == "ok:k2"
    assert "k1" not in fake.keys_used
    fake.behaviors.clear()
    fake.calls.clear()
    now["t"] += 400                                    # 累计 700 秒，冷却到期
    for i in range(4):
        client.chat(f"c{i}")
    assert "k1" in fake.keys_used


def test_all_keys_failing_falls_back_to_backup(tmp_path, fake):
    fake.behaviors = {"k1": HttpError(401), "k2": HttpError(429)}
    backup = {"provider": "anthropic", "api_key": "bk", "model": "claude-sonnet-5"}
    client = _client(tmp_path, _primary(["k1", "k2"]), backup=backup)
    assert client.chat("q") == "ok:bk"
    assert fake.calls[-1]["model"] == "anthropic/claude-sonnet-5"


def test_all_keys_failing_without_backup_raises(tmp_path, fake):
    fake.behaviors = {"k1": HttpError(401), "k2": HttpError(429)}
    with pytest.raises(RuntimeError):
        _client(tmp_path, _primary(["k1", "k2"])).chat("q")


def test_all_keys_in_cooldown_still_falls_back(tmp_path, fake):
    fake.behaviors = {"k1": HttpError(429), "k2": HttpError(429)}
    backup = {"provider": "anthropic", "api_key": "bk", "model": "claude-sonnet-5"}
    client = _client(tmp_path, _primary(["k1", "k2"]), backup=backup)
    assert client.chat("q1") == "ok:bk"
    assert client.chat("q2") == "ok:bk"                # 主模型全在冷却，仍应走备用而不是崩溃


def test_generic_error_retries_without_cooldown(tmp_path, fake):
    """超时等普通异常按 max_retries 重试，不冷却 Key。"""
    calls = {"n": 0}

    def completion(**kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise TimeoutError("timeout")
        return _resp("ok")

    fake.completion = completion
    client = _client(tmp_path, _primary(["k1"]), max_retries=2)
    assert client.chat("q") == "ok"
    assert calls["n"] == 2
    # 未冷却：下一次仍可用 k1
    assert client.chat("q2") == "ok"


def test_generic_error_exhausts_retries(tmp_path, fake):
    n = {"c": 0}

    def completion(**kwargs):
        n["c"] += 1
        raise TimeoutError("timeout")

    fake.completion = completion
    with pytest.raises(RuntimeError):
        _client(tmp_path, _primary(["k1"]), max_retries=2).chat("q")
    assert n["c"] == 3


def test_litellm_ratelimit_error_recognised(tmp_path, monkeypatch):
    litellm = pytest.importorskip("litellm")
    monkeypatch.setattr(llm_mod.time, "sleep", lambda s: None)
    lib = FakeLiteLLM()
    lib.behaviors = {"k1": litellm.RateLimitError("slow down", llm_provider="openai", model="m"),
                     "k2": litellm.AuthenticationError("bad", llm_provider="openai", model="m")}
    monkeypatch.setattr(llm_mod, "import_litellm", lambda: lib)
    assert _client(tmp_path).chat("q") == "ok:k3"
