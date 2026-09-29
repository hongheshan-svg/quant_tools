"""配置导出/导入（备份与恢复）：settings_store 单元测试与 API 测试。"""

from __future__ import annotations

import threading

import pytest
import yaml

from src import settings_store
from tests.test_api import env  # noqa: F401  (env 是 pytest fixture)

MASK = "******"

SAMPLE = {
    "llm": {"primary": {"provider": "deepseek", "api_key": "sk-secret-1234", "model": "deepseek-chat"}},
    "web": {"port": 8000, "api_token": "tok-abcdef"},
    "notifier": {
        "email": {"enabled": True, "password": "mail-pass", "smtp_host": "smtp.example.com"},
        "telegram": {"bot_token": "123:ABC", "chat_id": "42"},
        "webhook": {"url": "https://example.com/hook?key=zzz"},
        "feishu": {"webhook_url": "https://open.feishu.cn/x", "secret": "feishu-sec"},
    },
    "bot": {"dingtalk": {"client_id": "id1", "client_secret": "ding-sec"}},
    "search": {"bocha": {"api_keys": ["k-one-1111", "k-two-2222"], "enabled": True}},
    "note": "中文备注：涨停板",
}


def _write(path, data):
    path.write_text(yaml.dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")


def _read(path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


# ---------- settings_store ----------

def test_export_missing_file(tmp_path):
    text = settings_store.export_settings(path=tmp_path / "none.yaml")
    assert text.startswith("# 当前没有 settings.yaml")


def test_export_masks_secrets(tmp_path):
    p = tmp_path / "s.yaml"
    _write(p, SAMPLE)
    data = yaml.safe_load(settings_store.export_settings(include_secrets=False, path=p))
    assert data["llm"]["primary"]["api_key"] == MASK
    assert data["llm"]["primary"]["model"] == "deepseek-chat"
    assert data["web"]["port"] == 8000
    assert data["notifier"]["email"]["password"] == MASK
    assert data["notifier"]["email"]["smtp_host"] == "smtp.example.com"
    assert data["notifier"]["telegram"]["bot_token"] == MASK
    assert data["notifier"]["webhook"]["url"] == MASK
    assert data["notifier"]["feishu"]["secret"] == MASK          # 嵌套 notifier.*.secret
    assert data["bot"]["dingtalk"]["client_secret"] == MASK      # 嵌套 bot.*.client_secret
    assert data["bot"]["dingtalk"]["client_id"] == "id1"
    assert data["search"]["bocha"]["api_keys"] == [MASK, MASK]
    assert data["note"] == "中文备注：涨停板"


def test_export_with_secrets_is_raw(tmp_path):
    p = tmp_path / "s.yaml"
    _write(p, SAMPLE)
    data = yaml.safe_load(settings_store.export_settings(include_secrets=True, path=p))
    assert data == SAMPLE


def test_export_empty_secret_not_masked(tmp_path):
    p = tmp_path / "s.yaml"
    _write(p, {"llm": {"primary": {"api_key": "", "model": "m"}}, "search": {"bocha": {"api_keys": []}}})
    data = yaml.safe_load(settings_store.export_settings(path=p))
    assert data["llm"]["primary"]["api_key"] == ""
    assert data["search"]["bocha"]["api_keys"] == []


def test_export_list_with_empty_items(tmp_path):
    p = tmp_path / "s.yaml"
    _write(p, {"search": {"bocha": {"api_keys": ["k-one-1111", "", "k-two-2222"]}}})
    keys = yaml.safe_load(settings_store.export_settings(path=p))["search"]["bocha"]["api_keys"]
    assert len(keys) == 3
    assert keys[0] == MASK and keys[2] == MASK
    assert keys[1] in ("", None)


def test_import_restores_masked_values(tmp_path):
    p = tmp_path / "s.yaml"
    _write(p, SAMPLE)
    text = settings_store.export_settings(include_secrets=False, path=p)
    result = settings_store.import_settings(text, path=p)
    assert {"sections", "restored", "warnings"} <= set(result)
    assert _read(p) == SAMPLE       # 往返后内容等价，中文不乱码
    assert "中文备注" in p.read_text(encoding="utf-8")
    assert all("api_key" not in w for w in result["warnings"])


def test_import_new_values_override_and_partial_mask(tmp_path):
    p = tmp_path / "s.yaml"
    _write(p, SAMPLE)
    incoming = {"llm": {"primary": {"api_key": "sk-new-9999", "model": "x"}},
                "notifier": {"email": {"password": MASK}}}
    settings_store.import_settings(yaml.dump(incoming), path=p)
    data = _read(p)
    assert data["llm"]["primary"]["api_key"] == "sk-new-9999"
    assert data["notifier"]["email"]["password"] == "mail-pass"


def test_import_list_mask_restored_by_position(tmp_path):
    p = tmp_path / "s.yaml"
    _write(p, SAMPLE)
    settings_store.import_settings(yaml.dump({"search": {"bocha": {"api_keys": [MASK, MASK]}}}), path=p)
    assert _read(p)["search"]["bocha"]["api_keys"] == ["k-one-1111", "k-two-2222"]


def test_import_mask_without_current_value_dropped_with_warning(tmp_path):
    p = tmp_path / "s.yaml"
    _write(p, {"web": {"port": 1}})
    result = settings_store.import_settings(
        yaml.dump({"llm": {"primary": {"api_key": MASK, "model": "m"}},
                   "search": {"bocha": {"api_keys": ["new-key-1", MASK]}}}), path=p)
    data = _read(p)
    assert "api_key" not in data["llm"]["primary"]
    assert data["llm"]["primary"]["model"] == "m"
    assert data["search"]["bocha"]["api_keys"] == ["new-key-1"]
    assert result["warnings"]
    assert any("api_key" in w for w in result["warnings"])


def test_import_unknown_top_level_key_warns_but_kept(tmp_path):
    p = tmp_path / "s.yaml"
    result = settings_store.import_settings("my_custom_section:\n  a: 1\nweb:\n  port: 9\n", path=p)
    assert _read(p)["my_custom_section"] == {"a": 1}
    assert any("my_custom_section" in w for w in result["warnings"])
    assert "web" in result["sections"] or "web" in str(result["sections"])


@pytest.mark.parametrize("text", ["a: [1, 2\n  b: :", "- 1\n- 2\n", "just a string"])
def test_import_rejects_invalid(tmp_path, text):
    p = tmp_path / "s.yaml"
    _write(p, {"web": {"port": 1}})
    with pytest.raises(Exception):
        settings_store.import_settings(text, path=p)
    assert _read(p) == {"web": {"port": 1}}     # 失败时不动原文件


def test_concurrent_saves_keep_file_valid(tmp_path):
    p = tmp_path / "s.yaml"
    _write(p, SAMPLE)
    text = settings_store.export_settings(include_secrets=True, path=p)

    def worker(i):
        for _ in range(5):
            if i % 2:
                settings_store.import_settings(text, path=p)
            else:
                settings_store.save_section("web", {"port": 8000, "api_token": "tok-abcdef"}, path=p)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(6)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert isinstance(_read(p), dict)
    assert _read(p)["llm"]["primary"]["api_key"] == "sk-secret-1234"


# ---------- API ----------

def test_api_export_masked_and_raw(env):
    client, _, _ = env
    _write(settings_store.SETTINGS_PATH, SAMPLE)
    res = client.get("/api/v1/settings/export", params={"include_secrets": "false"})
    assert res.status_code == 200
    assert "yaml" in res.headers["content-type"]
    disposition = res.headers["content-disposition"]
    assert "attachment" in disposition
    import re
    assert re.search(r"settings-\d{8}\.yaml", disposition)
    data = yaml.safe_load(res.text)
    assert data["llm"]["primary"]["api_key"] == MASK
    assert data["llm"]["primary"]["model"] == "deepseek-chat"
    assert data["web"]["port"] == 8000
    assert data["search"]["bocha"]["api_keys"] == [MASK, MASK]
    assert data["notifier"]["webhook"]["url"] == MASK
    assert "sk-secret-1234" not in res.text
    raw = client.get("/api/v1/settings/export", params={"include_secrets": "true"})
    assert yaml.safe_load(raw.text) == SAMPLE


def test_api_import_roundtrip_restores(env):
    client, _, _ = env
    path = settings_store.SETTINGS_PATH
    _write(path, SAMPLE)
    exported = client.get("/api/v1/settings/export", params={"include_secrets": "false"}).text
    res = client.post("/api/v1/settings/import", json={"yaml": exported})
    assert res.status_code == 200, res.text
    body = res.json()
    assert {"sections", "restored", "warnings"} <= set(body)
    assert _read(path) == SAMPLE


def test_api_import_warnings_and_unknown_key(env):
    client, _, _ = env
    path = settings_store.SETTINGS_PATH
    _write(path, {"web": {"port": 1}})
    text = yaml.dump({"llm": {"primary": {"api_key": MASK}}, "zzz_unknown": {"a": 1}})
    body = client.post("/api/v1/settings/import", json={"yaml": text}).json()
    assert any("zzz_unknown" in w for w in body["warnings"])
    assert any("api_key" in w for w in body["warnings"])
    data = _read(path)
    assert data["zzz_unknown"] == {"a": 1}
    assert "api_key" not in data["llm"]["primary"]


@pytest.mark.parametrize("text", ["a: [1, 2\n  b: :", "- 1\n- 2\n"])
def test_api_import_invalid_400(env, text):
    client, _, _ = env
    assert client.post("/api/v1/settings/import", json={"yaml": text}).status_code == 400
