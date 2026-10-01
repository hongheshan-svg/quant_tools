"""LLM 错误分类与参数自动恢复：classify_llm_error、_PARAM_FIXES、last_error、/settings/llm/test。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.analyzers import llm_client as llm_mod
from src.analyzers.llm_client import LLMClient, classify_llm_error
from tests.test_api import env  # noqa: F401  复用 API 测试的临时库 + TestClient fixture


# ---------- 构造异常 ----------

class HttpError(Exception):
    def __init__(self, status_code: int, text: str = ""):
        super().__init__(text or f"HTTP {status_code}")
        self.status_code = status_code


class RespError(Exception):
    """状态码在 exc.response.status_code 上。"""

    def __init__(self, status_code: int, text: str = ""):
        super().__init__(text or f"HTTP {status_code}")
        self.response = SimpleNamespace(status_code=status_code)


class AuthenticationError(Exception):
    pass


class RateLimitError(Exception):
    pass


class Timeout(Exception):
    pass


class APIConnectionError(Exception):
    pass


def _resp(text: str = "ok"):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))],
                           usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1))


class FakeLiteLLM:
    """按调用顺序执行脚本：Exception 抛出，字符串返回；脚本用完后返回 ok。
    也可给 fn(kwargs) 按参数决定结果。"""

    def __init__(self, script=None, fn=None):
        self.script = list(script or [])
        self.fn = fn
        self.calls: list[dict] = []

    def completion(self, **kwargs):
        self.calls.append(dict(kwargs))
        if self.fn is not None:
            out = self.fn(kwargs)
        elif self.script:
            out = self.script.pop(0)
        else:
            out = "ok"
        if isinstance(out, BaseException):
            raise out
        return _resp(out)


@pytest.fixture(autouse=True)
def _reset():
    llm_mod.reset_key_state()
    yield
    llm_mod.reset_key_state()


@pytest.fixture
def sleeps(monkeypatch):
    calls: list[float] = []
    monkeypatch.setattr(llm_mod.time, "sleep", lambda s: calls.append(s))
    return calls


def _install(monkeypatch, lib):
    monkeypatch.setattr(llm_mod, "import_litellm", lambda: lib)
    return lib


def _client(tmp_path, backup=None, **cfg) -> LLMClient:
    base = {"primary": {"provider": "deepseek", "api_key": "k1", "base_url": "https://api.a.com", "model": "m1"},
            "cache_enabled": False, "cache_path": str(tmp_path / "cache.sqlite3"), "max_retries": 2}
    if backup:
        base["backup"] = backup
    return LLMClient({**base, **cfg})


BACKUP = {"provider": "deepseek", "api_key": "k2", "base_url": "https://api.b.com", "model": "m2"}


# ---------- 分类 ----------

CLASSIFY_CASES = [
    # (异常, kind, retryable)
    (HttpError(401, "bad"), "auth", False),
    (HttpError(403, "forbidden"), "auth", False),
    (RespError(401), "auth", False),
    (AuthenticationError("Incorrect API key provided"), "auth", False),
    (Exception("Invalid API Key"), "auth", False),
    (Exception("permission denied for this model"), "auth", False),
    (HttpError(402, "payment"), "quota", False),
    (Exception("You exceeded your current quota, please check your plan"), "quota", False),
    (Exception("Error code: insufficient_quota"), "quota", False),
    (Exception("账户余额不足"), "quota", False),
    (Exception("account billing hard limit reached"), "quota", False),
    (Exception("账户已欠费"), "quota", False),
    (HttpError(429, "slow down"), "rate_limit", True),
    (RespError(429), "rate_limit", True),
    (RateLimitError("x"), "rate_limit", True),
    (Exception("Rate limit reached for requests"), "rate_limit", True),
    (Exception("Too Many Requests"), "rate_limit", True),
    (HttpError(404, "The model `foo` does not exist"), "model_not_found", False),
    (Exception("Error: model_not_found"), "model_not_found", False),
    (Exception("Unknown model: abc"), "model_not_found", False),
    (Exception("模型不存在"), "model_not_found", False),
    (HttpError(400, "context_length_exceeded"), "context_length", False),
    (Exception("This model's maximum context length is 8192 tokens"), "context_length", False),
    (Exception("too many tokens in request"), "context_length", False),
    (Exception("超出上下文长度限制"), "context_length", False),
    (Exception("content_filter triggered"), "content_filter", False),
    (Exception("Your request was rejected by the content policy"), "content_filter", False),
    (Exception("内容包含敏感信息"), "content_filter", False),
    (Exception("未通过内容审核"), "content_filter", False),
    (Timeout("Request timed out"), "timeout", True),
    (Exception("Connection timed out after 60s"), "timeout", True),
    (APIConnectionError("Connection error."), "network", True),
    (ConnectionError("boom"), "network", True),
    (Exception("Connection refused"), "network", True),
    (Exception("Connection reset by peer"), "network", True),
    (Exception("Name or service not known"), "network", True),
    (Exception("SSL: CERTIFICATE_VERIFY_FAILED"), "network", True),
    (HttpError(500, "internal"), "server", True),
    (HttpError(502, "bad gateway"), "server", True),
    (RespError(503), "server", True),
    (Exception("The server is overloaded"), "server", True),
    (Exception("Service Unavailable"), "server", True),
    (Exception("something weird happened"), "unknown", True),
]


@pytest.mark.parametrize("exc,kind,retryable", CLASSIFY_CASES, ids=[f"{i}-{c[1]}" for i, c in enumerate(CLASSIFY_CASES)])
def test_classify_kinds(exc, kind, retryable):
    info = classify_llm_error(exc)
    assert info.kind == kind
    assert info.retryable is retryable
    assert info.message
    assert any("一" <= ch <= "鿿" for ch in info.message)  # 友好文案为中文


def test_classify_messages_match_contract():
    assert classify_llm_error(HttpError(401)).message == "API Key 无效或没有权限，请检查 Key 是否填对、是否开通了该模型"
    assert classify_llm_error(HttpError(402)).message == "账户余额不足或额度已用完"
    assert classify_llm_error(HttpError(429)).message == "请求太频繁被限流，请稍后再试或配置多个 Key"
    assert classify_llm_error(Exception("model_not_found")).message == "模型名称不存在，请用「获取模型列表」选择正确的模型"
    assert classify_llm_error(Exception("context_length_exceeded")).message == "输入内容超过模型的上下文长度"
    assert classify_llm_error(Exception("content_filter")).message == "内容被模型平台的安全审核拦截"
    assert classify_llm_error(Timeout("x")).message == "请求超时，请检查网络或调大超时时间"
    assert classify_llm_error(APIConnectionError("x")).message == "无法连接到模型服务，请检查 Base URL 和网络"
    assert classify_llm_error(HttpError(500)).message == "模型服务暂时不可用（服务端错误）"


def test_classify_unknown_truncates_to_100_chars():
    info = classify_llm_error(Exception("x" * 300))
    assert info.kind == "unknown"
    assert info.message.startswith("调用失败：")
    assert info.message == "调用失败：" + "x" * 100


@pytest.mark.parametrize("text,param", [
    ("Unsupported parameter: 'temperature' is not supported with this model", "temperature"),
    ("Invalid parameter: response_format is not supported", "response_format"),
    ("Unrecognized request argument supplied: max_tokens", "max_tokens"),
    ("'temperature' does not support 0.3 with this model. Only the default (1) value is supported", "temperature"),
    ("Unsupported parameter: 'max_tokens' is not supported with this model. Use 'max_completion_tokens' instead.", "max_tokens"),
])
def test_classify_unsupported_param(text, param):
    info = classify_llm_error(HttpError(400, text))
    assert info.kind == "unsupported_param"
    assert info.param == param
    assert param in info.message and "自动" in info.message


def test_classify_unsupported_param_max_completion_tokens():
    info = classify_llm_error(HttpError(400, "Unsupported parameter: 'max_completion_tokens' is not supported"))
    assert info.kind == "unsupported_param"
    assert info.param == "max_completion_tokens"


def test_classify_400_without_param_is_not_unsupported():
    assert classify_llm_error(HttpError(400, "bad request: messages empty")).kind != "unsupported_param"


def test_classify_param_text_without_400_is_not_unsupported_param():
    # 契约：400 且文本提到参数名，才算 unsupported_param
    assert classify_llm_error(HttpError(500, "temperature is not supported")).kind != "unsupported_param"


def test_classify_status_from_response_attribute():
    assert classify_llm_error(RespError(429)).kind == "rate_limit"
    assert classify_llm_error(RespError(500)).kind == "server"


def test_classify_404_without_model_is_not_model_not_found():
    assert classify_llm_error(HttpError(404, "page not found")).kind != "model_not_found"


def test_classify_never_raises_on_odd_exception():
    class Weird(Exception):
        def __str__(self):
            return ""

    info = classify_llm_error(Weird())
    assert info.kind == "unknown"


# ---------- 参数自动恢复 ----------

def test_response_format_unsupported_recovers_without_sleep(monkeypatch, tmp_path, sleeps):
    lib = _install(monkeypatch, FakeLiteLLM([HttpError(400, "Unsupported parameter: response_format is not supported"), "done"]))
    out = _client(tmp_path).chat("hi", response_format="json")
    assert out == "done"
    assert len(lib.calls) == 2
    assert "response_format" in lib.calls[0]
    assert "response_format" not in lib.calls[1]
    assert sleeps == []


def test_response_format_fix_persists_for_route(monkeypatch, tmp_path, sleeps):
    lib = _install(monkeypatch, FakeLiteLLM([HttpError(400, "response_format is not supported"), "a", "b"]))
    c = _client(tmp_path)
    c.chat("q1", response_format="json")
    c.chat("q2", response_format="json")
    assert len(lib.calls) == 3
    assert "response_format" not in lib.calls[2]
    assert sleeps == []


def test_recovery_does_not_consume_retries(monkeypatch, tmp_path, sleeps):
    # max_retries=0：普通错误只试 1 次，但参数恢复不计入
    lib = _install(monkeypatch, FakeLiteLLM([HttpError(400, "temperature is not supported"), "done"]))
    assert _client(tmp_path, max_retries=0).chat("hi") == "done"
    assert len(lib.calls) == 2


def test_temperature_unsupported_removed(monkeypatch, tmp_path, sleeps):
    lib = _install(monkeypatch, FakeLiteLLM([
        HttpError(400, "Unsupported value: 'temperature' does not support 0.3 with this model. Only the default (1) value is supported."),
        "done"]))
    assert _client(tmp_path).chat("hi") == "done"
    assert "temperature" in lib.calls[0] and "temperature" not in lib.calls[1]
    assert sleeps == []


def test_max_tokens_switches_to_max_completion_tokens(monkeypatch, tmp_path, sleeps):
    lib = _install(monkeypatch, FakeLiteLLM([
        HttpError(400, "Unsupported parameter: 'max_tokens' is not supported with this model. Use 'max_completion_tokens' instead."),
        "done"]))
    assert _client(tmp_path).chat("hi", max_tokens=77) == "done"
    assert lib.calls[0]["max_tokens"] == 77
    assert "max_tokens" not in lib.calls[1]
    assert lib.calls[1]["max_completion_tokens"] == 77
    assert sleeps == []


def test_max_completion_tokens_unsupported_drops_both(monkeypatch, tmp_path, sleeps):
    lib = _install(monkeypatch, FakeLiteLLM([
        HttpError(400, "Unsupported parameter: max_tokens is not supported"),
        HttpError(400, "Unsupported parameter: max_completion_tokens is not supported"),
        "done"]))
    assert _client(tmp_path).chat("hi", max_tokens=50) == "done"
    assert len(lib.calls) == 3
    assert "max_tokens" not in lib.calls[2] and "max_completion_tokens" not in lib.calls[2]
    assert sleeps == []
    # 之后同路由直接不带
    _install(monkeypatch, lib)
    lib.script = ["again"]
    _client(tmp_path).chat("hi2", max_tokens=50)
    assert "max_tokens" not in lib.calls[-1] and "max_completion_tokens" not in lib.calls[-1]


def test_multiple_params_recovered_in_one_call(monkeypatch, tmp_path, sleeps):
    lib = _install(monkeypatch, FakeLiteLLM([
        HttpError(400, "temperature is not supported"),
        HttpError(400, "response_format is not supported"),
        "done"]))
    assert _client(tmp_path).chat("hi", response_format="json") == "done"
    assert len(lib.calls) == 3
    assert "temperature" not in lib.calls[2] and "response_format" not in lib.calls[2]
    assert sleeps == []


def test_recovery_limit_three_per_call(monkeypatch, tmp_path, sleeps):
    # 一直报参数不支持：恢复最多 3 次，不会死循环（总调用数有界）
    lib = _install(monkeypatch, FakeLiteLLM(fn=lambda kw: HttpError(400, "temperature is not supported")))
    c = _client(tmp_path, max_retries=0)
    with pytest.raises(RuntimeError):
        c.chat("hi")
    assert 2 <= len(lib.calls) <= 5


def test_param_fix_is_per_route(monkeypatch, tmp_path, sleeps):
    def fn(kw):
        if kw.get("api_base") == "https://api.a.com" and "temperature" in kw:
            return HttpError(400, "temperature is not supported")
        return "ok"

    lib = _install(monkeypatch, FakeLiteLLM(fn=fn))
    _client(tmp_path).chat("hi")
    # 另一个路由不受影响：仍带 temperature
    b = _client(tmp_path, primary=BACKUP)
    b.chat("hi")
    assert "temperature" in lib.calls[-1]
    assert lib.calls[-1]["api_base"] == "https://api.b.com"


def test_reset_key_state_clears_param_fixes(monkeypatch, tmp_path, sleeps):
    lib = _install(monkeypatch, FakeLiteLLM([HttpError(400, "temperature is not supported"), "a", "b"]))
    c = _client(tmp_path)
    c.chat("q1")
    c.chat("q2")
    assert "temperature" not in lib.calls[2]
    assert llm_mod._PARAM_FIXES
    llm_mod.reset_key_state()
    assert not llm_mod._PARAM_FIXES
    c.chat("q3")
    assert "temperature" in lib.calls[3]


def test_param_fixes_keyed_by_route_id(monkeypatch, tmp_path, sleeps):
    _install(monkeypatch, FakeLiteLLM([HttpError(400, "temperature is not supported"), "a"]))
    c = _client(tmp_path)
    c.chat("q")
    assert c.primary_client.route_id in llm_mod._PARAM_FIXES
    assert "temperature" in llm_mod._PARAM_FIXES[c.primary_client.route_id]


def test_stream_applies_recorded_fix_and_recovers(monkeypatch, tmp_path, sleeps):
    def chunk(t):
        return SimpleNamespace(usage=None, choices=[SimpleNamespace(delta=SimpleNamespace(content=t))])

    calls = []

    class StreamLib:
        def completion(self, **kw):
            calls.append(dict(kw))
            if "temperature" in kw:
                raise HttpError(400, "temperature is not supported")
            return iter([chunk("he"), chunk("llo")])

    monkeypatch.setattr(llm_mod, "import_litellm", lambda: StreamLib())
    c = _client(tmp_path)
    assert "".join(c.chat_stream("hi")) == "hello"
    assert "temperature" in calls[0] and "temperature" not in calls[-1]
    n = len(calls)
    assert "".join(c.chat_stream("hi again")) == "hello"
    assert len(calls) == n + 1 and "temperature" not in calls[-1]
    assert sleeps == []


def test_vision_recovers_param(monkeypatch, tmp_path, sleeps):
    lib = _install(monkeypatch, FakeLiteLLM([HttpError(400, "temperature is not supported"), "img-ok"]))
    out = _client(tmp_path).chat_vision("看图", [(b"\x89PNG", "image/png")])
    assert out == "img-ok"
    assert "temperature" not in lib.calls[-1]


# ---------- 不可重试 / 可重试 ----------

@pytest.mark.parametrize("exc", [
    HttpError(404, "model `m1` does not exist"),
    Exception("context_length_exceeded"),
    Exception("content_filter triggered"),
    Exception("insufficient_quota"),
])
def test_non_retryable_not_retried_and_switch_to_backup(monkeypatch, tmp_path, sleeps, exc):
    def fn(kw):
        return exc if kw["api_base"] == "https://api.a.com" else "from-backup"

    lib = _install(monkeypatch, FakeLiteLLM(fn=fn))
    out = _client(tmp_path, backup=BACKUP).chat("hi")
    assert out == "from-backup"
    bases = [c["api_base"] for c in lib.calls]
    assert bases.count("https://api.a.com") == 1
    assert sleeps == []


def test_auth_error_not_retried_on_same_route(monkeypatch, tmp_path, sleeps):
    # 单 Key 被 401：Key 轮换逻辑冷却该 Key，不在同一路由 sleep 重试
    lib = _install(monkeypatch, FakeLiteLLM(fn=lambda kw: HttpError(401, "bad key")))
    with pytest.raises(RuntimeError) as ei:
        _client(tmp_path).chat("hi")
    assert len(lib.calls) == 1
    assert sleeps == []
    assert "Key" in str(ei.value) or "所有LLM模型均调用失败" in str(ei.value)


def test_retryable_error_still_retries(monkeypatch, tmp_path, sleeps):
    lib = _install(monkeypatch, FakeLiteLLM([HttpError(500, "boom"), HttpError(503, "boom"), "done"]))
    assert _client(tmp_path).chat("hi") == "done"
    assert len(lib.calls) == 3
    assert len(sleeps) == 2


def test_unknown_error_retries_until_exhausted(monkeypatch, tmp_path, sleeps):
    lib = _install(monkeypatch, FakeLiteLLM(fn=lambda kw: Exception("weird")))
    with pytest.raises(RuntimeError):
        _client(tmp_path).chat("hi")
    assert len(lib.calls) == 3  # max_retries=2 -> 3 次
    assert len(sleeps) == 2


def test_timeout_and_network_are_retried(monkeypatch, tmp_path, sleeps):
    lib = _install(monkeypatch, FakeLiteLLM([Timeout("Request timed out"), APIConnectionError("Connection error."), "done"]))
    assert _client(tmp_path).chat("hi") == "done"
    assert len(lib.calls) == 3


# ---------- chat() 异常文案与 last_error ----------

def test_chat_failure_message_includes_classification(monkeypatch, tmp_path, sleeps):
    _install(monkeypatch, FakeLiteLLM(fn=lambda kw: Exception("insufficient_quota")))
    c = _client(tmp_path)
    with pytest.raises(RuntimeError) as ei:
        c.chat("hi")
    assert str(ei.value) == "所有LLM模型均调用失败：账户余额不足或额度已用完"
    assert c.last_error is not None and c.last_error.kind == "quota" and c.last_error.retryable is False


def test_chat_failure_model_not_found_message(monkeypatch, tmp_path, sleeps):
    _install(monkeypatch, FakeLiteLLM(fn=lambda kw: HttpError(404, "model does not exist")))
    with pytest.raises(RuntimeError) as ei:
        _client(tmp_path).chat("hi")
    assert "模型名称不存在" in str(ei.value)


def test_last_error_reflects_last_failed_route(monkeypatch, tmp_path, sleeps):
    def fn(kw):
        return Exception("insufficient_quota") if kw["api_base"] == "https://api.a.com" else Exception("content_filter")

    _install(monkeypatch, FakeLiteLLM(fn=fn))
    c = _client(tmp_path, backup=BACKUP)
    with pytest.raises(RuntimeError) as ei:
        c.chat("hi")
    assert c.last_error.kind == "content_filter"
    assert "安全审核" in str(ei.value)


def test_last_error_reset_at_start_of_chat(monkeypatch, tmp_path, sleeps):
    lib = _install(monkeypatch, FakeLiteLLM(fn=lambda kw: Exception("insufficient_quota")))
    c = _client(tmp_path)
    with pytest.raises(RuntimeError):
        c.chat("hi")
    assert c.last_error is not None
    lib.fn = None
    lib.script = ["fine"]
    assert c.chat("hi2") == "fine"
    assert c.last_error is None


def test_last_error_initial_none(tmp_path):
    assert _client(tmp_path).last_error is None


def test_chat_stream_final_error_has_classification(monkeypatch, tmp_path, sleeps):
    _install(monkeypatch, FakeLiteLLM(fn=lambda kw: Exception("insufficient_quota")))
    c = _client(tmp_path)
    with pytest.raises(RuntimeError) as ei:
        list(c.chat_stream("hi"))
    assert "账户余额不足" in str(ei.value)


def test_chat_json_failure_propagates_friendly_message(monkeypatch, tmp_path, sleeps):
    _install(monkeypatch, FakeLiteLLM(fn=lambda kw: HttpError(404, "model does not exist")))
    c = _client(tmp_path)
    try:
        c.chat_json("hi")
    except RuntimeError as e:
        assert "模型名称不存在" in str(e)
    # 若 chat_json 内部吞掉异常返回空值也可接受，此时 last_error 仍应已记录
    assert c.last_error is not None and c.last_error.kind == "model_not_found"


# ---------- /settings/llm/test ----------

def _fake_lib(monkeypatch, lib):
    monkeypatch.setattr(llm_mod, "import_litellm", lambda: lib)
    monkeypatch.setattr(llm_mod.time, "sleep", lambda s: None)


def test_api_llm_test_failure_has_kind(env, monkeypatch):
    client, _app, _config = env
    _fake_lib(monkeypatch, FakeLiteLLM(fn=lambda kw: HttpError(401, "Incorrect API key")))
    r = client.post("/api/v1/settings/llm/test", json={"llm": {}})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert body["kind"] == "auth"
    assert "API Key" in body["error"]


def test_api_llm_test_failure_quota_and_model(env, monkeypatch):
    client, _app, _config = env
    _fake_lib(monkeypatch, FakeLiteLLM(fn=lambda kw: HttpError(404, "model does not exist")))
    body = client.post("/api/v1/settings/llm/test", json={"llm": {}}).json()
    assert body["ok"] is False and body["kind"] == "model_not_found"
    assert "模型名称不存在" in body["error"]


def test_api_llm_test_success_without_note(env, monkeypatch):
    client, _app, _config = env
    _fake_lib(monkeypatch, FakeLiteLLM(["正常"]))
    body = client.post("/api/v1/settings/llm/test", json={"llm": {}}).json()
    assert body["ok"] is True and body["reply"] == "正常"
    assert not body.get("note")


def test_api_llm_test_success_with_note_after_recovery(env, monkeypatch):
    client, _app, _config = env
    _fake_lib(monkeypatch, FakeLiteLLM([HttpError(400, "Unsupported parameter: 'temperature' is not supported"), "正常"]))
    body = client.post("/api/v1/settings/llm/test", json={"llm": {}}).json()
    assert body["ok"] is True
    assert body["reply"] == "正常"
    assert "temperature" in body["note"] and "自动调整" in body["note"]


# ---------- 补充：流式、API 其他 kind ----------

def _chunk(t):
    return SimpleNamespace(usage=None, choices=[SimpleNamespace(delta=SimpleNamespace(content=t))])


def test_stream_non_retryable_switches_to_backup(monkeypatch, tmp_path, sleeps):
    calls = []

    class StreamLib:
        def completion(self, **kw):
            calls.append(kw["api_base"])
            if kw["api_base"] == "https://api.a.com":
                raise Exception("insufficient_quota")
            return iter([_chunk("bk")])

    monkeypatch.setattr(llm_mod, "import_litellm", lambda: StreamLib())
    assert "".join(_client(tmp_path, backup=BACKUP).chat_stream("hi")) == "bk"
    assert calls == ["https://api.a.com", "https://api.b.com"]
    assert sleeps == []


def test_stream_recovery_is_bounded(monkeypatch, tmp_path, sleeps):
    calls = []

    class StreamLib:
        def completion(self, **kw):
            calls.append(kw)
            raise HttpError(400, "temperature is not supported")

    monkeypatch.setattr(llm_mod, "import_litellm", lambda: StreamLib())
    with pytest.raises(RuntimeError):
        list(_client(tmp_path).chat_stream("hi"))
    assert len(calls) <= 5


def test_api_llm_test_quota_kind(env, monkeypatch):
    client, _app, _config = env
    _fake_lib(monkeypatch, FakeLiteLLM(fn=lambda kw: HttpError(402, "payment required")))
    body = client.post("/api/v1/settings/llm/test", json={"llm": {}}).json()
    assert body["ok"] is False and body["kind"] == "quota"
    assert "余额" in body["error"]


def test_api_llm_test_max_tokens_note(env, monkeypatch):
    client, _app, _config = env
    _fake_lib(monkeypatch, FakeLiteLLM([
        HttpError(400, "Unsupported parameter: 'max_tokens' is not supported with this model. Use 'max_completion_tokens'"), "正常"]))
    body = client.post("/api/v1/settings/llm/test", json={"llm": {}}).json()
    assert body["ok"] is True and "max_tokens" in body["note"]
