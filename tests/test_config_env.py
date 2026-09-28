"""环境变量覆盖配置（QUANT__ 开头），写回文件时剔除"""

import yaml

from src import config_loader, settings_store


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")


def test_env_overrides_parse_nested_keys_and_types():
    base = {"llm": {"primary": {"api_key": "your-key", "max_tokens": 4096}}, "notifier": {"dingtalk": {"secret": ""}}}
    env = {
        "QUANT__LLM__PRIMARY__API_KEY": "123456",        # 原值是字符串：纯数字也保留为字符串
        "QUANT__LLM__PRIMARY__MAX_TOKENS": "8192",        # 原值是数字：按 YAML 解析
        "QUANT__WEB__AUTH_ENABLED": "true",               # 新增的项：按 YAML 解析
        "QUANT__ALERTS__WATCHLIST": "['600519', '000001']",
        "QUANT__NOTIFIER__DINGTALK__SECRET": "",          # 空值忽略（没配置的 Actions Secret）
        "OTHER": "x",
    }
    assert config_loader.env_overrides(env, base) == {
        "alerts": {"watchlist": ["600519", "000001"]},
        "llm": {"primary": {"api_key": "123456", "max_tokens": 8192}},
        "web": {"auth_enabled": True},
    }


def test_load_config_applies_env_and_save_config_strips_it(tmp_path, monkeypatch):
    settings = tmp_path / "config" / "settings.yaml"
    _write(settings.with_name("settings.yaml.example"), {"llm": {"primary": {"api_key": "your-key", "model": "deepseek-chat"}}, "strategy": {"weights": {"a": 1}}})
    _write(settings, {"llm": {"primary": {"model": "deepseek-reasoner"}}})
    monkeypatch.setenv("QUANT__LLM__PRIMARY__API_KEY", "sk-secret")
    monkeypatch.setenv("QUANT__LLM__PRIMARY__MODEL", "qwen-max")
    path = str(settings)
    config_loader._config_cache.pop(path, None)

    config = config_loader.load_config(path)
    assert config["llm"]["primary"] == {"api_key": "sk-secret", "model": "qwen-max"}

    # 自学习写回完整配置：环境变量的值不落盘，文件里原有的值保留
    config["strategy"]["adaptive_weights"] = {"a": 0.9}
    config_loader.save_config(config, path)
    written = yaml.safe_load(settings.read_text(encoding="utf-8"))
    assert "api_key" not in written["llm"]["primary"]
    assert written["llm"]["primary"]["model"] == "deepseek-reasoner"
    assert written["strategy"]["adaptive_weights"] == {"a": 0.9}
    assert "sk-secret" not in settings.read_text(encoding="utf-8")
    config_loader._config_cache.pop(path, None)


def test_save_section_strips_env_values(tmp_path, monkeypatch):
    settings = tmp_path / "settings.yaml"
    _write(settings, {"notifier": {"dingtalk": {"enabled": False, "webhook": "https://old"}}})
    monkeypatch.setenv("QUANT__NOTIFIER__DINGTALK__WEBHOOK", "https://from-env")
    monkeypatch.setenv("QUANT__NOTIFIER__EMAIL__PASSWORD", "mail-secret")

    settings_store.save_section("notifier", {
        "dingtalk": {"enabled": True, "webhook": "https://from-env"},
        "email": {"password": "mail-secret", "smtp_server": "smtp.qq.com"},
    }, path=settings)
    written = yaml.safe_load(settings.read_text(encoding="utf-8"))
    assert written["notifier"]["dingtalk"] == {"enabled": True, "webhook": "https://old"}
    assert written["notifier"]["email"] == {"smtp_server": "smtp.qq.com"}


def test_env_overrides_only_apply_to_settings_yaml(tmp_path, monkeypatch):
    pool = tmp_path / "stock_pool.yaml"
    _write(pool, {"blacklist": []})
    monkeypatch.setenv("QUANT__WEB__PORT", "9000")
    config_loader._config_cache.pop(str(pool), None)
    assert config_loader.load_config(str(pool)) == {"blacklist": []}
    config_loader._config_cache.pop(str(pool), None)
