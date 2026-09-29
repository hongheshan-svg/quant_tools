"""新增推送渠道（Telegram、Discord、Slack、PushPlus、Server酱、ntfy、Gotify、Pushover、Bark、Webhook）与推送模块公共逻辑。全部离线。"""

from __future__ import annotations

import importlib
import json
from typing import Any

import httpx
import pytest

from src import notifier
from src.notifier.base import markdown_to_text

NEW_CHANNELS = ["telegram", "discord", "slack", "pushplus", "serverchan", "ntfy", "gotify", "pushover", "bark", "webhook"]
OLD_CHANNELS = ["wechat", "dingtalk", "feishu", "email"]
CLASS_NAMES = {"telegram": "TelegramNotifier", "discord": "DiscordNotifier", "slack": "SlackNotifier", "pushplus": "PushPlusNotifier",
               "serverchan": "ServerChanNotifier", "ntfy": "NtfyNotifier", "gotify": "GotifyNotifier", "pushover": "PushoverNotifier",
               "bark": "BarkNotifier", "webhook": "WebhookNotifier"}


class FakeResp:
    def __init__(self, status_code: int = 200, data: Any = None, text: str = ""):
        self.status_code = status_code
        self._data = data
        self.text = text or (json.dumps(data) if data is not None else "")

    def json(self):
        if self._data is None:
            raise ValueError("no json")
        return self._data


# 渠道 -> (完整配置, 成功响应, 失败响应列表, 期望 URL 片段)
SPECS: dict[str, dict[str, Any]] = {
    "telegram": dict(cfg={"bot_token": "123:ABC", "chat_id": "-1001"}, ok=lambda: FakeResp(200, {"ok": True}),
                     fails=[lambda: FakeResp(200, {"ok": False}), lambda: FakeResp(400, {"ok": False}), lambda: FakeResp(500)],
                     url="https://api.telegram.org/bot123:ABC/sendMessage", required=["bot_token", "chat_id"]),
    "discord": dict(cfg={"webhook_url": "https://discord.com/api/webhooks/1/abc"}, ok=lambda: FakeResp(204),
                    fails=[lambda: FakeResp(400), lambda: FakeResp(500)], url="https://discord.com/api/webhooks/1/abc", required=["webhook_url"]),
    "slack": dict(cfg={"webhook_url": "https://hooks.slack.com/services/T/B/x"}, ok=lambda: FakeResp(200, text="ok"),
                  fails=[lambda: FakeResp(400, text="invalid_payload"), lambda: FakeResp(404)], url="https://hooks.slack.com/services/T/B/x",
                  required=["webhook_url"]),
    "pushplus": dict(cfg={"token": "tok123"}, ok=lambda: FakeResp(200, {"code": 200, "msg": "ok"}),
                     fails=[lambda: FakeResp(200, {"code": 999}), lambda: FakeResp(500)], url="pushplus", required=["token"]),
    "serverchan": dict(cfg={"sendkey": "SCT123abc"}, ok=lambda: FakeResp(200, {"code": 0}),
                       fails=[lambda: FakeResp(200, {"code": 40001}), lambda: FakeResp(500)], url="https://sctapi.ftqq.com/SCT123abc.send",
                       required=["sendkey"]),
    "ntfy": dict(cfg={"topic": "quant"}, ok=lambda: FakeResp(200, {"id": "x"}),
                 fails=[lambda: FakeResp(500), lambda: FakeResp(403)], url="https://ntfy.sh", required=["topic"]),
    "gotify": dict(cfg={"server": "https://gotify.example.com", "token": "gt"}, ok=lambda: FakeResp(200, {"id": 1}),
                   fails=[lambda: FakeResp(401), lambda: FakeResp(500)], url="https://gotify.example.com/message", required=["server", "token"]),
    "pushover": dict(cfg={"user_key": "uk", "api_token": "at"}, ok=lambda: FakeResp(200, {"status": 1}),
                     fails=[lambda: FakeResp(200, {"status": 0}), lambda: FakeResp(400, {"status": 0})], url="pushover.net",
                     required=["user_key", "api_token"]),
    "bark": dict(cfg={"device_key": "dk"}, ok=lambda: FakeResp(200, {"code": 200}),
                 fails=[lambda: FakeResp(200, {"code": 400}), lambda: FakeResp(500)], url="https://api.day.app/push", required=["device_key"]),
    "webhook": dict(cfg={"url": "https://example.com/hook"}, ok=lambda: FakeResp(200, text="ok"),
                    fails=[lambda: FakeResp(500), lambda: FakeResp(404)], url="https://example.com/hook", required=["url"]),
}


class Recorder:
    def __init__(self):
        self.calls: list[dict] = []
        self.factory = lambda: FakeResp(200)
        self.raise_exc: Exception | None = None

    def __call__(self, *args, **kwargs):
        # httpx.post(url, ...) / httpx.get(url, ...) / httpx.request(method, url, ...)
        self.calls.append({"args": args, "kwargs": kwargs})
        if self.raise_exc:
            raise self.raise_exc
        return self.factory()


def _url(call: dict) -> str:
    for a in call["args"]:
        if isinstance(a, str) and a.startswith("http"):
            return a
    return str(call["kwargs"].get("url", ""))


def _strings(obj: Any) -> list[str]:
    out: list[str] = []
    if isinstance(obj, str):
        out.append(obj)
        try:
            inner = json.loads(obj)
            if isinstance(inner, (dict, list)):
                out.extend(_strings(inner))
        except ValueError:
            pass
    elif isinstance(obj, bytes):
        out.extend(_strings(obj.decode("utf-8", "ignore")))
    elif isinstance(obj, dict):
        for v in obj.values():
            out.extend(_strings(v))
    elif isinstance(obj, list):
        for v in obj:
            out.extend(_strings(v))
    return out


def _payload_text(call: dict) -> str:
    kw = call["kwargs"]
    parts = []
    for key in ("json", "data", "params", "content"):
        if key in kw and kw[key] is not None:
            parts.extend(_strings(kw[key]))
    return "\n".join(parts)


def _body_strings(call: dict) -> list[str]:
    kw = call["kwargs"]
    out: list[str] = []
    for key in ("json", "data", "params", "content"):
        if key in kw and kw[key] is not None:
            out.extend(_strings(kw[key]))
    return out


def _headers(call: dict) -> dict:
    return {str(k).lower(): v for k, v in (call["kwargs"].get("headers") or {}).items()}


@pytest.fixture
def http(monkeypatch):
    rec = Recorder()
    for name in ("post", "get", "request", "put"):
        monkeypatch.setattr(httpx, name, rec, raising=False)
    monkeypatch.delenv("QUANT_NO_NOTIFY", raising=False)
    return rec


def _cls(name):
    mod = importlib.import_module(f"src.notifier.{name}")
    return getattr(mod, CLASS_NAMES[name]), mod


def _config(name: str, **extra) -> dict:
    return {"notifier": {name: {"enabled": True, **SPECS[name]["cfg"], **extra}}}


# ---------- 各渠道通用行为 ----------

@pytest.mark.parametrize("name", NEW_CHANNELS)
def test_success_sends_request_with_title_and_content(http, name):
    cls, mod = _cls(name)
    assert mod.MAX_CONTENT_BYTES > 0
    http.factory = SPECS[name]["ok"]
    assert cls(_config(name)).send("每日报告：涨停复盘", "内容正文ABC") is True
    assert len(http.calls) == 1
    call = http.calls[0]
    assert SPECS[name]["url"] in _url(call)
    text = _payload_text(call)
    assert "内容正文ABC" in text
    assert "每日报告：涨停复盘" in text or name in ("discord", "slack", "telegram", "webhook")  # 中文标题至少不丢（部分渠道把标题并入正文）


@pytest.mark.parametrize("name", NEW_CHANNELS)
def test_failures_return_false(http, name):
    cls, _ = _cls(name)
    for fail in SPECS[name]["fails"]:
        http.factory = fail
        assert cls(_config(name)).send("t", "c") is False


@pytest.mark.parametrize("name", NEW_CHANNELS)
def test_exception_returns_false(http, name):
    cls, _ = _cls(name)
    http.raise_exc = httpx.ConnectError("boom")
    assert cls(_config(name)).send("t", "c") is False
    http.raise_exc = RuntimeError("weird")
    assert cls(_config(name)).send("t", "c") is False


@pytest.mark.parametrize("name", NEW_CHANNELS)
def test_disabled_sends_nothing(http, name):
    cls, _ = _cls(name)
    http.factory = SPECS[name]["ok"]
    cfg = _config(name)
    cfg["notifier"][name]["enabled"] = False
    assert cls(cfg).send("t", "c") is False
    assert cls({"notifier": {}}).send("t", "c") is False
    assert http.calls == []


@pytest.mark.parametrize("name,field", [(n, f) for n in NEW_CHANNELS for f in SPECS[n]["required"]])
def test_missing_required_field_sends_nothing(http, name, field):
    cls, _ = _cls(name)
    http.factory = SPECS[name]["ok"]
    for empty in ("", None):
        cfg = _config(name)
        cfg["notifier"][name][field] = empty
        assert cls(cfg).send("t", "c") is False
    assert http.calls == []


@pytest.mark.parametrize("name", NEW_CHANNELS)
def test_long_content_is_split_within_limit(http, name):
    cls, mod = _cls(name)
    http.factory = SPECS[name]["ok"]
    lines = [f"第{i:04d}行 涨停股票分析内容，龙头继续走强" for i in range(1, 3000)]
    content = "\n".join(lines)
    assert len(content.encode("utf-8")) > 2 * mod.MAX_CONTENT_BYTES
    assert cls(_config(name)).send("标题", content) is True
    assert len(http.calls) >= 2
    joined = "\n".join(_payload_text(c) for c in http.calls)
    for marker in ("第0001行", "第1500行", "第2999行"):
        assert marker in joined
    for call in http.calls:
        body = max(_body_strings(call), key=lambda s: len(s.encode("utf-8")))
        assert len(body.encode("utf-8")) <= mod.MAX_CONTENT_BYTES


@pytest.mark.parametrize("name", NEW_CHANNELS)
def test_long_content_partial_failure_returns_false(http, name):
    cls, mod = _cls(name)
    seq = iter([SPECS[name]["ok"]] + [SPECS[name]["fails"][0]] * 100)

    def factory():
        return next(seq)()

    http.factory = factory
    content = "\n".join(f"第{i}行 内容内容内容内容内容内容内容内容" for i in range(1, 6000))
    assert cls(_config(name)).send("标题", content) is False


# ---------- 渠道细节 ----------

def test_telegram_payload_and_custom_api_base_and_thread(http):
    cls, _ = _cls("telegram")
    http.factory = SPECS["telegram"]["ok"]
    cfg = _config("telegram", api_base="https://tg.proxy.example.com", message_thread_id="42")
    assert cls(cfg).send("标题", "正文") is True
    call = http.calls[0]
    assert _url(call).startswith("https://tg.proxy.example.com/bot123:ABC/sendMessage")
    text = _payload_text(call)
    assert "-1001" in text and "正文" in text and "42" in text
    http.calls.clear()
    cls(_config("telegram")).send("t", "c")
    assert _url(http.calls[0]).startswith("https://api.telegram.org/bot123:ABC/sendMessage")


def test_telegram_thread_id_absent_by_default(http):
    cls, _ = _cls("telegram")
    http.factory = SPECS["telegram"]["ok"]
    cls(_config("telegram")).send("t", "c")
    assert "message_thread_id" not in json.dumps(http.calls[0]["kwargs"].get("json") or {})


def test_pushplus_token_and_topic(http):
    cls, _ = _cls("pushplus")
    http.factory = SPECS["pushplus"]["ok"]
    assert cls(_config("pushplus", topic="grp1")).send("标题", "正文") is True
    text = _payload_text(http.calls[0])
    assert "tok123" in text or "tok123" in _url(http.calls[0])
    assert "grp1" in text and "标题" in text and "正文" in text


def test_serverchan_turbo_key_url(http):
    cls, _ = _cls("serverchan")
    http.factory = SPECS["serverchan"]["ok"]
    assert cls(_config("serverchan", sendkey="SCT123abcXYZ")).send("标题中文", "正文") is True
    call = http.calls[0]
    assert _url(call).startswith("https://sctapi.ftqq.com/SCT123abcXYZ.send")
    text = _payload_text(call)
    assert "标题中文" in text and "正文" in text
    keys = set()
    for k in ("json", "data", "params"):
        if isinstance(call["kwargs"].get(k), dict):
            keys |= set(call["kwargs"][k])
    assert {"title", "desp"} <= keys


def test_serverchan_sc3_key_url(http):
    cls, _ = _cls("serverchan")
    http.factory = SPECS["serverchan"]["ok"]
    key = "sctp1234tAbCdEf"
    assert cls(_config("serverchan", sendkey=key)).send("t", "c") is True
    assert _url(http.calls[0]).startswith(f"https://1234.push.ft07.com/send/{key}.send")


def test_ntfy_json_publish_default_server_and_no_token(http):
    cls, _ = _cls("ntfy")
    http.factory = SPECS["ntfy"]["ok"]
    assert cls(_config("ntfy")).send("中文标题", "**正文**") is True
    call = http.calls[0]
    assert _url(call).rstrip("/") == "https://ntfy.sh"
    body = call["kwargs"]["json"]
    assert body["topic"] == "quant" and body["title"] == "中文标题" and "正文" in body["message"] and body["markdown"] is True
    assert "authorization" not in _headers(call)


def test_ntfy_token_custom_server(http):
    cls, _ = _cls("ntfy")
    http.factory = SPECS["ntfy"]["ok"]
    assert cls(_config("ntfy", token="tk_abc", server="https://ntfy.example.com")).send("t", "c") is True
    call = http.calls[0]
    assert _url(call).rstrip("/") == "https://ntfy.example.com"
    assert _headers(call)["authorization"] == "Bearer tk_abc"


def test_gotify_priority_default_and_custom(http):
    cls, _ = _cls("gotify")
    http.factory = SPECS["gotify"]["ok"]
    cls(_config("gotify")).send("标题", "正文")
    call = http.calls[0]
    assert _url(call) == "https://gotify.example.com/message"
    payload = call["kwargs"].get("json") or call["kwargs"].get("data")
    assert int(payload["priority"]) == 5 and payload["title"] == "标题" and "正文" in payload["message"]
    assert "gt" in (_url(call) + json.dumps(call["kwargs"], default=str))
    http.calls.clear()
    cls(_config("gotify", priority=8, server="https://gotify.example.com/")).send("t", "c")
    payload = http.calls[0]["kwargs"].get("json") or http.calls[0]["kwargs"].get("data")
    assert int(payload["priority"]) == 8
    assert _url(http.calls[0]) == "https://gotify.example.com/message"


def test_pushover_payload(http):
    cls, _ = _cls("pushover")
    http.factory = SPECS["pushover"]["ok"]
    assert cls(_config("pushover")).send("标题", "正文") is True
    text = _payload_text(http.calls[0])
    assert "uk" in text and "at" in text and "标题" in text and "正文" in text


def test_bark_payload_default_server_and_group(http):
    cls, _ = _cls("bark")
    http.factory = SPECS["bark"]["ok"]
    assert cls(_config("bark", group="量化")).send("标题", "正文") is True
    call = http.calls[0]
    assert _url(call) == "https://api.day.app/push"
    payload = call["kwargs"]["json"]
    assert payload["device_key"] == "dk" and payload["title"] == "标题" and "正文" in payload["body"] and payload["group"] == "量化"
    http.calls.clear()
    cls(_config("bark", server="https://bark.example.com/")).send("t", "c")
    assert _url(http.calls[0]) == "https://bark.example.com/push"


# ---------- Webhook ----------

def test_webhook_default_json_body(http):
    cls, _ = _cls("webhook")
    http.factory = SPECS["webhook"]["ok"]
    assert cls(_config("webhook")).send("标题", "正文\n第二行") is True
    call = http.calls[0]
    assert _url(call) == "https://example.com/hook"
    body = call["kwargs"].get("json")
    if body is None:
        body = json.loads(call["kwargs"]["content"] if "content" in call["kwargs"] else call["kwargs"]["data"])
    assert body == {"title": "标题", "content": "正文\n第二行"}


def _sent_body(call: dict) -> str:
    kw = call["kwargs"]
    body = kw.get("content", kw.get("data"))
    if isinstance(body, bytes):
        body = body.decode("utf-8")
    return body


def test_webhook_template_json_placeholders_valid_json(http):
    cls, _ = _cls("webhook")
    http.factory = SPECS["webhook"]["ok"]
    tpl = '{"msgtype":"text","text":{"content":$content_json},"t":$title_json}'
    content = '他说 "涨停"\n第二行\\反斜杠 中文'
    assert cls(_config("webhook", body_template=tpl)).send('标题"引号', content) is True
    parsed = json.loads(_sent_body(http.calls[0]))
    assert parsed["text"]["content"] == content and parsed["t"] == '标题"引号'


def test_webhook_template_plain_placeholders(http):
    cls, _ = _cls("webhook")
    http.factory = SPECS["webhook"]["ok"]
    assert cls(_config("webhook", body_template="title=$title&text=$content")).send("标题", "正文") is True
    assert _sent_body(http.calls[0]) == "title=标题&text=正文"


def test_webhook_custom_headers_and_methods(http):
    cls, _ = _cls("webhook")
    http.factory = SPECS["webhook"]["ok"]
    assert cls(_config("webhook", headers={"X-Token": "abc", "Content-Type": "application/json"})).send("t", "c") is True
    assert _headers(http.calls[0])["x-token"] == "abc"
    # GET
    http.calls.clear()
    assert cls(_config("webhook", method="GET")).send("t", "c") is True
    call = http.calls[0]
    used_get = "get" in [str(a).lower() for a in call["args"] if isinstance(a, str) and not a.startswith("http")] or call["kwargs"].get("method", "").lower() == "get"
    # 通过 httpx.get 调用时 args 中没有 method，需要区分：httpx.get 和 httpx.request 是同一个 recorder，这里只要求没有 POST
    assert "post" not in [str(a).lower() for a in call["args"] if isinstance(a, str) and not a.startswith("http")]
    assert str(call["kwargs"].get("method", "get")).lower() == "get"
    del used_get


def test_webhook_default_method_is_post(http):
    cls, _ = _cls("webhook")
    http.factory = SPECS["webhook"]["ok"]
    cls(_config("webhook")).send("t", "c")
    call = http.calls[0]
    methods = [str(a).lower() for a in call["args"] if isinstance(a, str) and not a.startswith("http")]
    method = methods[0] if methods else str(call["kwargs"].get("method", "post")).lower()
    assert method == "post"


def test_webhook_2xx_ok_others_fail(http):
    cls, _ = _cls("webhook")
    for code, expected in ((200, True), (201, True), (204, True), (301, False), (400, False), (500, False)):
        http.factory = lambda c=code: FakeResp(c)
        assert cls(_config("webhook")).send("t", "c") is expected, code


# ---------- markdown_to_text ----------

def test_markdown_to_text_strips_marks():
    text = markdown_to_text("# 标题\n## 二级\n**加粗**内容\n> 引用\n---\n`代码` 与 - 列表项")
    for mark in ("#", "**", "`", ">"):
        assert mark not in text
    assert "---" not in text
    for word in ("标题", "二级", "加粗", "内容", "引用", "代码", "列表项"):
        assert word in text


def test_markdown_to_text_plain_unchanged_and_empty():
    assert markdown_to_text("普通文本 abc") == "普通文本 abc"
    assert markdown_to_text("").strip() == ""


# ---------- __init__：注册表、配置检查、广播 ----------

def test_registry_has_14_channels_old_first():
    assert list(notifier.NOTIFIERS)[:4] == OLD_CHANNELS
    assert set(notifier.NOTIFIERS) == set(OLD_CHANNELS + NEW_CHANNELS) and len(notifier.NOTIFIERS) == 14
    assert set(notifier.CHANNEL_LABELS) == set(notifier.NOTIFIERS)
    assert notifier.CHANNEL_LABELS["telegram"] == "Telegram"
    for name in NEW_CHANNELS:
        assert notifier.NOTIFIERS[name] is _cls(name)[0]


def test_channel_fields_only_new_channels():
    assert set(notifier.CHANNEL_FIELDS) == set(NEW_CHANNELS)
    for name, fields in notifier.CHANNEL_FIELDS.items():
        assert fields
        for f in fields:
            assert {"key", "label", "required", "secret", "placeholder", "type"} <= set(f)
        required = {f["key"] for f in fields if f["required"]}
        assert required == set(SPECS[name]["required"]), name
    tg = {f["key"]: f for f in notifier.CHANNEL_FIELDS["telegram"]}
    assert tg["bot_token"]["secret"] is True and tg["bot_token"]["required"] is True
    assert tg["api_base"]["required"] is False


def test_secret_fields():
    assert "bot_token" in notifier.secret_fields("telegram")
    assert "token" in notifier.secret_fields("pushplus")
    assert "sendkey" in notifier.secret_fields("serverchan")
    assert notifier.secret_fields("email") == ["password"]
    for old in ("wechat", "dingtalk", "feishu"):
        assert notifier.secret_fields(old) == []
    for name in NEW_CHANNELS:
        assert set(notifier.secret_fields(name)) == {f["key"] for f in notifier.CHANNEL_FIELDS[name] if f["secret"]}


@pytest.mark.parametrize("name", NEW_CHANNELS)
def test_is_configured_and_placeholder(name):
    assert notifier.is_configured(_config(name), name) is True
    for field in SPECS[name]["required"]:
        cfg = _config(name)
        cfg["notifier"][name][field] = ""
        assert notifier.is_configured(cfg, name) is False
        cfg["notifier"][name][field] = "your-xxx"
        assert notifier.is_configured(cfg, name) is False
    assert notifier.is_configured({"notifier": {}}, name) is False


def _diag(cfg, name):
    return next(c for c in notifier.diagnose(cfg)["channels"] if c["channel"] == name)


@pytest.mark.parametrize("name", NEW_CHANNELS)
def test_diagnose_ok_and_missing_and_placeholder(name):
    ok = _diag(_config(name), name)
    assert ok["configured"] is True and ok["enabled"] is True and ok["issues"] == []
    assert ok["label"] == notifier.CHANNEL_LABELS[name]
    for field in SPECS[name]["required"]:
        cfg = _config(name)
        cfg["notifier"][name][field] = ""
        d = _diag(cfg, name)
        assert d["configured"] is False and any("缺少" in i for i in d["issues"])
        cfg["notifier"][name][field] = "your-xxx"
        d = _diag(cfg, name)
        assert d["configured"] is False and any("占位符" in i for i in d["issues"])


def test_diagnose_url_scheme_checks():
    d = _diag(_config("gotify", server="gotify.example.com"), "gotify")
    assert any("http" in i for i in d["issues"])
    d = _diag(_config("webhook", url="example.com/hook"), "webhook")
    assert any("http" in i for i in d["issues"])
    d = _diag(_config("ntfy", server="ntfy.example.com"), "ntfy")
    assert any("http" in i for i in d["issues"])


def test_diagnose_discord_and_slack_domains():
    assert _diag(_config("discord", webhook_url="http://discord.com/api/webhooks/1/a"), "discord")["issues"]
    assert _diag(_config("discord", webhook_url="https://evil.example.com/api/webhooks/1/a"), "discord")["issues"]
    assert _diag(_config("discord", webhook_url="https://discord.com/api/webhooks/1/a"), "discord")["issues"] == []
    assert _diag(_config("discord", webhook_url="https://discordapp.com/api/webhooks/1/a"), "discord")["issues"] == []
    assert _diag(_config("slack", webhook_url="http://hooks.slack.com/services/a"), "slack")["issues"]
    assert _diag(_config("slack", webhook_url="https://example.com/services/a"), "slack")["issues"]
    assert _diag(_config("slack", webhook_url="https://hooks.slack.com/services/a"), "slack")["issues"] == []


def test_enabled_channels_new_and_routes():
    cfg = {"notifier": {
        "telegram": {"enabled": True, **SPECS["telegram"]["cfg"]},
        "bark": {"enabled": True, **SPECS["bark"]["cfg"]},
        "ntfy": {"enabled": False, **SPECS["ntfy"]["cfg"]},
        "gotify": {"enabled": True, "server": "https://g.example.com", "token": "your-token"},
        "routes": {"alert": ["bark"], "chat": ["ntfy"]},
    }}
    assert set(notifier.enabled_channels(cfg)) == {"telegram", "bark"}
    assert notifier.enabled_channels(cfg, "alert") == ["bark"]
    assert notifier.enabled_channels(cfg, "daily_report") and set(notifier.enabled_channels(cfg, "daily_report")) == {"telegram", "bark"}
    assert notifier.enabled_channels(cfg, "chat") == []
    d = notifier.diagnose(cfg)
    assert any("Ntfy" in r or "ntfy" in r.lower() for r in d["routes"])


def test_broadcast_routes_only_to_selected_new_channel(http):
    http.factory = lambda: FakeResp(200, {"ok": True, "code": 200})
    cfg = {"notifier": {
        "telegram": {"enabled": True, **SPECS["telegram"]["cfg"]},
        "bark": {"enabled": True, **SPECS["bark"]["cfg"]},
        "routes": {"alert": ["bark"]},
    }}
    assert notifier.broadcast(cfg, "标题", "内容", kind="alert") == {"bark": True}
    assert len(http.calls) == 1 and "api.day.app" in _url(http.calls[0])
    http.calls.clear()
    assert notifier.broadcast(cfg, "标题", "内容", kind="daily_report") == {"telegram": True, "bark": True}
    assert len(http.calls) == 2


def test_broadcast_isolated_failures(http, monkeypatch):
    cfg = {"notifier": {"telegram": {"enabled": True, **SPECS["telegram"]["cfg"]}, "bark": {"enabled": True, **SPECS["bark"]["cfg"]}}}

    def boom(self, title, content):
        raise RuntimeError("x")

    monkeypatch.setattr(notifier.NOTIFIERS["telegram"], "send", boom)
    http.factory = lambda: FakeResp(200, {"code": 200})
    assert notifier.broadcast(cfg, "t", "c") == {"telegram": False, "bark": True}


def test_quant_no_notify_blocks_broadcast(http, monkeypatch):
    http.factory = SPECS["bark"]["ok"]
    cfg = _config("bark")
    monkeypatch.setenv("QUANT_NO_NOTIFY", "1")
    assert notifier.broadcast(cfg, "t", "c") == {}
    assert http.calls == []
    monkeypatch.setenv("QUANT_NO_NOTIFY", "")
    assert notifier.broadcast(cfg, "t", "c") == {"bark": True}


def test_test_channel_new_channel(http):
    http.factory = SPECS["bark"]["ok"]
    cfg = _config("bark")
    cfg["notifier"]["bark"]["enabled"] = False
    assert notifier.test_channel(cfg, "bark")["ok"] is True
    assert notifier.test_channel({"notifier": {}}, "bark") == {"ok": False, "error": "配置不完整"}
    http.factory = SPECS["bark"]["fails"][0]
    assert notifier.test_channel(cfg, "bark")["ok"] is False
