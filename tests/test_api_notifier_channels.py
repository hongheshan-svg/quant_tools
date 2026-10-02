"""推送渠道扩展的 Web API：fields、掩码保留、测试消息。"""

from __future__ import annotations

from src import notifier, settings_store
from tests.test_api import _wait, env  # noqa: F401  (env 是 fixture)

NEW = ["telegram", "discord", "slack", "pushplus", "serverchan", "ntfy", "gotify", "pushover", "bark", "webhook"]


def _add(config):
    config["notifier"].update({
        "telegram": {"enabled": True, "bot_token": "123:SECRET", "chat_id": "-100"},
        "pushplus": {"enabled": True, "token": "pp-secret"},
        "gotify": {"enabled": False, "server": "https://g.example.com", "token": "gotify-secret"},
        "bark": {"enabled": False, "device_key": ""},
    })


def test_get_notifier_settings_fields_and_masks(env):  # noqa: F811
    client, _, config = env
    _add(config)
    data = client.get("/api/v1/settings/notifier").json()
    assert set(data["fields"]) == set(NEW)
    assert {"key", "label", "required", "secret", "placeholder", "type"} <= set(data["fields"]["telegram"][0])
    assert len(data["channels"]) == 14 and data["channels"]["telegram"] == "Telegram"
    n = data["notifier"]
    assert n["telegram"]["bot_token"] == "******" and n["telegram"]["chat_id"] == "-100"
    assert n["pushplus"]["token"] == "******" and n["gotify"]["token"] == "******"
    assert n["gotify"]["server"] == "https://g.example.com"
    assert n["bark"]["device_key"] == ""                       # 空值不掩码
    assert n["email"]["password"] == "******"
    assert "SECRET" not in str(data) and "secret" not in str(n["telegram"]) .lower()


def test_put_notifier_keeps_masked_secret_and_updates_others(env, monkeypatch):  # noqa: F811
    client, _, config = env
    from src import config_loader

    _add(config)
    settings_store.save_section("notifier", {"telegram": {"enabled": True, "bot_token": "123:SECRET", "chat_id": "-100"},
                                             "pushplus": {"enabled": True, "token": "pp-secret"}}, merge=True)
    monkeypatch.setattr(config_loader, "reload_config", lambda: config)
    body = {"notifier": {"telegram": {"enabled": True, "bot_token": "******", "chat_id": "-200"},
                         "pushplus": {"enabled": True, "token": "new-token"}}}
    assert client.put("/api/v1/settings/notifier", json=body).json() == {"ok": True}
    saved = settings_store.read_settings()["notifier"]
    assert saved["telegram"]["bot_token"] == "123:SECRET" and saved["telegram"]["chat_id"] == "-200"
    assert saved["pushplus"]["token"] == "new-token"


def test_put_notifier_masked_email_still_kept(env, monkeypatch):  # noqa: F811
    client, _, config = env
    from src import config_loader

    monkeypatch.setattr(config_loader, "reload_config", lambda: config)
    body = {"notifier": {"email": {"enabled": True, "smtp_host": "smtp.qq.com", "password": "******", "to": ["a@x.com"]}}}
    assert client.put("/api/v1/settings/notifier", json=body).json() == {"ok": True}
    assert settings_store.read_settings()["notifier"]["email"]["password"] == "mail-pass"


def test_diagnose_endpoint_new_channel(env):  # noqa: F811
    client, _, config = env
    _add(config)
    body = {"notifier": {"telegram": {"enabled": True, "bot_token": "******", "chat_id": ""}}}
    res = client.post("/api/v1/settings/notifier/diagnose", json=body).json()
    tg = next(c for c in res["channels"] if c["channel"] == "telegram")
    assert tg["configured"] is False and any("缺少" in i for i in tg["issues"])
    body = {"notifier": {"telegram": {"enabled": True, "bot_token": "******", "chat_id": "-1"}}}
    res = client.post("/api/v1/settings/notifier/diagnose", json=body).json()
    assert next(c for c in res["channels"] if c["channel"] == "telegram")["configured"] is True


def test_test_endpoint_new_channel_uses_saved_secret(env, monkeypatch):  # noqa: F811
    client, _, config = env
    _add(config)
    seen = {}

    def fake_send(self, title, content):
        seen["token"] = self.__dict__.get("bot_token") or str(self.__dict__)
        seen["title"] = title
        return True

    monkeypatch.setattr(notifier.NOTIFIERS["telegram"], "send", fake_send)
    body = {"notifier": {"telegram": {"enabled": False, "bot_token": "******", "chat_id": "-100"}}}
    res = client.post("/api/v1/settings/notifier/test/telegram", json=body).json()
    assert res["ok"] is True and seen["title"] == "推送测试"
    assert "123:SECRET" in seen["token"]                        # 掩码被还原成原值再发送


def test_test_endpoint_failure_and_incomplete(env, monkeypatch):  # noqa: F811
    client, _, config = env
    _add(config)
    monkeypatch.setattr(notifier.NOTIFIERS["pushplus"], "send", lambda self, t, c: False)
    res = client.post("/api/v1/settings/notifier/test/pushplus", json={"notifier": {}}).json()
    assert res["ok"] is False and res["error"]
    res = client.post("/api/v1/settings/notifier/test/bark", json={"notifier": {}}).json()
    assert res == {"ok": False, "error": "配置不完整"}
    res = client.post("/api/v1/settings/notifier/test/nope", json={"notifier": {}}).json()
    assert res["ok"] is False


def test_batch_test_returns_each_enabled_channel_result_without_network(env, monkeypatch):
    client, _, config = env
    config['notifier'] = {'telegram': {'enabled': True, 'bot_token': 'test-token', 'chat_id': '1'}, 'pushplus': {'enabled': True, 'token': 'test'}, 'email': {'enabled': False}}
    calls = []
    monkeypatch.setattr(notifier, 'test_channel', lambda cfg, name: calls.append(name) or {'ok': name == 'telegram', 'error': '' if name == 'telegram' else '模拟失败'})
    result = client.post('/api/v1/settings/notifier/test-batch', json={'notifier': {}}).json()
    assert set(calls) == {'telegram', 'pushplus'} and result['ok'] is False
    assert result['channels']['pushplus']['error'] == '模拟失败'
