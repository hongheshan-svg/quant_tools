"""完整配置校验：src/services/config_check.py、/system/config-check、main.py --check-config、导入接口附带校验。"""

from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest
import yaml

import main as main_mod
from src import settings_store
from tests.test_api import env  # noqa: F401  (env 是 pytest fixture)

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE_PATH = ROOT / "config" / "settings.yaml.example"


def _example() -> dict:
    return yaml.safe_load(EXAMPLE_PATH.read_text(encoding="utf-8"))


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def run(raw: dict | None, *, key: bool = True, merged_extra: dict | None = None) -> dict:
    """以 example 为底合并 raw 得到 config，再校验。key=True 时给主模型一个有效 Key，排除语义噪音。"""
    from src.services.config_check import check_config

    example = _example()
    config = _merge(example, raw or {})
    if key:
        config["llm"]["primary"]["api_key"] = "sk-real-key-1234"
    if merged_extra:
        config = _merge(config, merged_extra)
    return check_config(config, raw, example)


def find(res: dict, path: str, level: str | None = None) -> list[dict]:
    return [i for i in res["issues"] if i["path"] == path and (level is None or i["level"] == level)]


def under(res: dict, prefix: str, level: str | None = None) -> list[dict]:
    return [i for i in res["issues"] if (i["path"] == prefix or i["path"].startswith(prefix + ".")) and (level is None or i["level"] == level)]


# ---------- 结构 ----------

def test_result_shape_and_ordering():
    res = run({"zzz": 1, "web": {"port": 0}, "aaa_unknown": 1})
    assert set(res) >= {"ok", "errors", "warnings", "issues"}
    assert res["errors"] == len([i for i in res["issues"] if i["level"] == "error"])
    assert res["warnings"] == len([i for i in res["issues"] if i["level"] == "warning"])
    assert res["ok"] is (res["errors"] == 0)
    levels = [i["level"] for i in res["issues"]]
    assert levels == sorted(levels, key=lambda x: 0 if x == "error" else 1)       # error 在前
    for lv in ("error", "warning"):
        paths = [i["path"] for i in res["issues"] if i["level"] == lv]
        assert paths == sorted(paths)
    assert all(i["message"] for i in res["issues"])


def test_example_as_raw_has_no_unknown_or_type_errors():
    from src.services.config_check import check_config

    example = _example()
    res = check_config(copy.deepcopy(example), copy.deepcopy(example), example)
    # 只允许主模型/备用模型 Key 相关的语义问题
    bad = [i for i in res["issues"] if not i["path"].startswith("llm.")]
    assert bad == [], bad
    assert all("未知" not in i["message"] for i in res["issues"])
    assert not any(i["level"] == "error" and i["path"] != "llm.primary.api_key" and i["path"] != "llm.primary" for i in res["issues"]), res["issues"]


def test_example_read_automatically_when_none():
    from src.services.config_check import check_config

    example = _example()
    res = check_config(copy.deepcopy(example), {"zzz_top": 1}, None)
    assert find(res, "zzz_top", "warning")


def test_raw_none_skips_raw_checks():
    from src.services.config_check import check_config

    example = _example()
    config = copy.deepcopy(example)
    config["llm"]["primary"]["api_key"] = "sk-ok-1234"
    res = check_config(config, None, example)
    assert res["ok"] is True
    assert not [i for i in res["issues"] if "未知" in i["message"]]


# ---------- 未知键 ----------

def test_unknown_top_level_key():
    res = run({"zzz_unknown": {"a": 1}})
    assert find(res, "zzz_unknown", "warning")
    assert res["ok"] is True


def test_unknown_nested_key():
    res = run({"web": {"prot": 8000}})
    hit = find(res, "web.prot", "warning")
    assert hit and "未知" in hit[0]["message"]


def test_dynamic_keys_exempt():
    raw = {
        "strategy": {"adaptive_weights": {"foo_score": 0.3}, "source_confidence": {"某源": 0.8}},
        "screening": {"strategies": {"我的策略": {"x": 1}}},
        "llm": {"pricing": {"my-model": {"input": 1, "output": 2}}},
        "notifier": {"routes": {"alert": ["wechat"]}},
        "search": {"bocha": {"api_keys": ["k"], "whatever": 1}, "tavily": {"extra": 2}},
    }
    res = run(raw)
    for p in ("strategy.adaptive_weights", "strategy.source_confidence", "screening.strategies", "llm.pricing", "notifier.routes", "search.bocha", "search.tavily"):
        assert not under(res, p, "warning"), (p, res["issues"])
    assert not [i for i in res["issues"] if "未知" in i["message"]]


def test_list_elements_exempt():
    raw = {"alerts": {"rules": [{"type": "price", "weird_key": 1, "code": "600519"}]},
           "notifier": {"image": {"channels": [{"anything": 1}]}}}
    res = run(raw)
    assert not [i for i in res["issues"] if "未知" in i["message"]], res["issues"]


def test_env_override_not_unknown(monkeypatch):
    monkeypatch.setenv("QUANT__WEB__PORT", "9000")
    res = run({})
    assert not [i for i in res["issues"] if "未知" in i["message"]]


# ---------- 类型 ----------

def test_bool_written_as_string():
    res = run({"web": {"auth_enabled": "yes"}})
    assert find(res, "web.auth_enabled", "error")


def test_number_written_as_string():
    res = run({"web": {"port": "abc"}})
    assert find(res, "web.port", "error")


def test_list_written_as_dict():
    res = run({"notifier": {"email": {"to": {"a": 1}}}})
    assert find(res, "notifier.email.to", "error")


def test_string_written_as_list():
    res = run({"web": {"host": ["127.0.0.1"]}})
    assert find(res, "web.host", "error")


def test_int_float_interchangeable():
    res = run({"llm": {"primary": {"temperature": 1}, "timeout_seconds": 45.5}, "web": {"port": 8000.0}})
    assert not find(res, "llm.primary.temperature", "error")
    assert not find(res, "llm.timeout_seconds", "error")
    assert not find(res, "web.port", "error")


def test_key_list_or_string_allowed():
    res1 = run({"llm": {"primary": {"api_key": ["sk-a-1234", "sk-b-5678"]}}}, key=False)
    res2 = run({"llm": {"primary": {"api_key": "sk-a-1234,sk-b-5678"}}}, key=False)
    for r in (res1, res2):
        assert not find(r, "llm.primary.api_key", "error")
    res3 = run({"search": {"bocha": {"api_keys": "k1,k2"}}})
    assert not under(res3, "search.bocha", "error")


def test_null_or_empty_example_default_not_checked():
    # vision.api_key 默认为空字符串，可写任意类型的 Key（列表）；example 为 null 的项同理
    res = run({"llm": {"vision": {"api_key": ["a", "b"], "model": "gpt-4o"}}})
    assert not under(res, "llm.vision", "error")


# ---------- 格式与范围 ----------

@pytest.mark.parametrize("val", ["25:00", "9:30", "12:60", "abc", "1530"])
def test_scheduler_time_invalid(val):
    res = run({"scheduler": {"daily_analysis_time": val}})
    assert find(res, "scheduler.daily_analysis_time", "error"), val


@pytest.mark.parametrize("val", ["00:00", "09:30", "23:59"])
def test_scheduler_time_valid(val):
    res = run({"scheduler": {"daily_analysis_time": val}})
    assert not find(res, "scheduler.daily_analysis_time", "error")


@pytest.mark.parametrize("key", ["cailianshe_interval", "hot_search_interval", "stock_data_interval"])
def test_scheduler_interval_positive(key):
    assert find(run({"scheduler": {key: 0}}), f"scheduler.{key}", "error")
    assert find(run({"scheduler": {key: -5}}), f"scheduler.{key}", "error")
    assert not find(run({"scheduler": {key: 10}}), f"scheduler.{key}", "error")


@pytest.mark.parametrize("val,bad", [(0, True), (1, False), (10, False), (11, True)])
def test_watchlist_workers_range(val, bad):
    assert bool(find(run({"watchlist": {"workers": val}}), "watchlist.workers", "error")) is bad


@pytest.mark.parametrize("val,bad", [(0, True), (1, False), (500, False), (501, True)])
def test_watchlist_max_stocks_range(val, bad):
    assert bool(find(run({"watchlist": {"max_stocks": val}}), "watchlist.max_stocks", "error")) is bad


@pytest.mark.parametrize("val,bad", [(0, False), (30, False), (-1, True)])
def test_zero_default_minutes_allow_zero(val, bad):
    """示例默认为 0 的分钟项（watchlist.timeout_minutes，0 表示不限）允许 0，不允许负数。"""
    assert bool(find(run({"watchlist": {"timeout_minutes": val}}), "watchlist.timeout_minutes", "error")) is bad


@pytest.mark.parametrize("val,bad", [(0, True), (1, False), (65535, False), (65536, True), (-1, True)])
def test_web_port_range(val, bad):
    assert bool(find(run({"web": {"port": val}}), "web.port", "error")) is bad


def test_quiet_hours():
    assert not find(run({"notifier": {"quiet_hours": []}}), "notifier.quiet_hours", "error")
    assert not find(run({"notifier": {"quiet_hours": ["22:00", "07:00"]}}), "notifier.quiet_hours", "error")
    assert find(run({"notifier": {"quiet_hours": ["22:00"]}}), "notifier.quiet_hours", "error")
    assert find(run({"notifier": {"quiet_hours": ["25:00", "07:00"]}}), "notifier.quiet_hours", "error")
    assert find(run({"notifier": {"quiet_hours": ["22:00", "07:00", "08:00"]}}), "notifier.quiet_hours", "error")


@pytest.mark.parametrize("val,bad", [("single", False), ("standard", False), ("full", False), ("turbo", True)])
def test_diagnosis_mode_enum(val, bad):
    assert bool(find(run({"diagnosis": {"mode": val}}), "diagnosis.mode", "error")) is bad


@pytest.mark.parametrize("val,bad", [("litellm", False), ("openai", False), ("langchain", True)])
def test_llm_backend_enum(val, bad):
    assert bool(find(run({"llm": {"backend": val}}), "llm.backend", "error")) is bad


@pytest.mark.parametrize("val,bad", [("zh", False), ("en", False), ("fr", True)])
def test_report_language_enum(val, bad):
    assert bool(find(run({"report": {"language": val}}), "report.language", "error")) is bad


def test_report_language_absent_ok():
    from src.services.config_check import check_config

    example = _example()
    config = copy.deepcopy(example)
    config.pop("report", None)
    config["llm"]["primary"]["api_key"] = "sk-x-1234"
    res = check_config(config, {}, example)
    assert not find(res, "report.language")


# ---------- 语义 ----------

def test_primary_without_key_is_error():
    for k in ("", "your-deepseek-api-key-here", []):
        res = run({"llm": {"primary": {"api_key": k}}}, key=False)
        hit = under(res, "llm.primary", "error")
        assert hit and any("API Key" in i["message"] for i in hit), k
        assert res["ok"] is False


def test_primary_with_key_ok():
    res = run({})
    assert not under(res, "llm.primary", "error")


def test_ollama_needs_no_key():
    res = run({"llm": {"primary": {"provider": "ollama", "model": "qwen2", "api_key": "", "base_url": "http://localhost:11434"}}}, key=False)
    assert not under(res, "llm.primary", "error")


def test_backup_placeholder_key_warns():
    res = run({"llm": {"backup": {"provider": "openai", "api_key": "your-openai-api-key-here"}}})
    assert under(res, "llm.backup", "warning")
    assert not under(res, "llm.backup", "error")


def test_backup_without_provider_no_warning():
    res = run({"llm": {"backup": {"provider": "", "api_key": ""}}})
    assert not under(res, "llm.backup", "warning")


def test_enabled_notifier_channel_incomplete_is_error():
    res = run({"notifier": {"telegram": {"enabled": True, "bot_token": "", "chat_id": ""}}})
    assert under(res, "notifier.telegram", "error")
    assert res["ok"] is False


def test_enabled_wechat_with_placeholder_webhook():
    res = run({"notifier": {"wechat": {"enabled": True, "webhook_url": "https://x/your-key"}}})
    assert under(res, "notifier.wechat", "error")


def test_disabled_channel_incomplete_no_issue():
    res = run({"notifier": {"telegram": {"enabled": False, "bot_token": "", "chat_id": ""}}})
    assert not under(res, "notifier.telegram")


def test_complete_channel_no_issue():
    res = run({"notifier": {"telegram": {"enabled": True, "bot_token": "123:abc", "chat_id": "42"}}})
    assert not under(res, "notifier.telegram", "error")


def test_public_host_without_auth_is_error():
    res = run({"web": {"host": "0.0.0.0", "auth_enabled": False}})
    hit = under(res, "web", "error")
    assert hit and any("登录" in i["message"] for i in hit)
    assert res["ok"] is False


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1"])
def test_local_host_ok(host):
    res = run({"web": {"host": host, "auth_enabled": False}})
    assert not [i for i in res["issues"] if i["path"].startswith("web") and i["level"] == "error"]


def test_public_host_with_auth_ok():
    res = run({"web": {"host": "0.0.0.0", "auth_enabled": True}})
    assert not [i for i in res["issues"] if i["path"].startswith("web") and i["level"] == "error" and "登录" in i["message"]]


def test_search_enabled_without_provider_warns():
    res = run({"search": {"enabled": True}})
    assert under(res, "search", "warning")
    assert not under(res, "search", "error")


def test_search_enabled_with_provider_ok():
    res = run({"search": {"enabled": True, "bocha": {"api_keys": ["real-key-1234"]}}})
    assert not under(res, "search", "warning")


def test_search_disabled_no_warning():
    assert not under(run({"search": {"enabled": False}}), "search", "warning")


def test_bot_enabled_without_credentials_warns():
    res = run({"bot": {"dingtalk": {"enabled": True, "client_id": "", "client_secret": ""}}})
    assert under(res, "bot", "warning")
    assert not under(res, "bot", "error")


def test_bot_disabled_no_warning():
    assert not under(run({}), "bot", "warning")


def test_auto_confirm_warns():
    res = run({"trading": {"auto_confirm": True}})
    hit = under(res, "trading.auto_confirm", "warning")
    assert hit and "模拟盘" in hit[0]["message"]
    assert res["ok"] is True


# ---------- 异常降级 ----------

def test_check_item_exception_degrades_to_warning(monkeypatch):
    def boom(config):
        raise RuntimeError("诊断炸了")

    monkeypatch.setattr("src.notifier.diagnose", boom)
    res = run({})                                    # 不能整体失败
    assert res["warnings"] >= 1
    assert any("诊断炸了" in i["message"] or "失败" in i["message"] or "异常" in i["message"] for i in res["issues"] if i["level"] == "warning")


def test_garbage_config_does_not_raise():
    from src.services.config_check import check_config

    res = check_config({"llm": None, "web": "oops", "notifier": []}, {"llm": 3}, _example())
    assert isinstance(res["issues"], list)


# ---------- API ----------

def _write(path, data):
    path.write_text(yaml.dump(data, allow_unicode=True), encoding="utf-8")


def test_api_config_check(env):  # noqa: F811
    client, _, _ = env
    _write(settings_store.SETTINGS_PATH, {"zzz_unknown": 1, "web": {"port": 8000}})
    res = client.get("/api/v1/system/config-check")
    assert res.status_code == 200, res.text
    body = res.json()
    assert set(body) >= {"ok", "errors", "warnings", "issues"}
    assert isinstance(body["issues"], list)
    assert body["ok"] is (body["errors"] == 0)


def test_api_config_check_without_settings_file(env):  # noqa: F811
    client, _, _ = env
    assert not settings_store.SETTINGS_PATH.exists()
    res = client.get("/api/v1/system/config-check")
    assert res.status_code == 200, res.text


def test_api_import_includes_check(env):  # noqa: F811
    client, _, _ = env
    _write(settings_store.SETTINGS_PATH, {"web": {"port": 1}})
    text = yaml.dump({"web": {"port": 0}, "zzz_unknown": {"a": 1}})
    res = client.post("/api/v1/settings/import", json={"yaml": text})
    assert res.status_code == 200, res.text                      # 只提示，不拦截
    body = res.json()
    assert "check" in body
    chk = body["check"]
    assert chk["errors"] >= 1 and chk["ok"] is False
    assert any("zzz_unknown" == i["path"] for i in chk["issues"])
    assert any(i["path"] == "web.port" and i["level"] == "error" for i in chk["issues"])   # 按「示例配置 + 导入内容」合并后校验
    assert any("错误" in w for w in body["warnings"])
    # 仍然写入
    assert yaml.safe_load(settings_store.SETTINGS_PATH.read_text(encoding="utf-8"))["web"]["port"] == 0


def test_api_import_clean_has_no_error_warning(env):  # noqa: F811
    client, _, _ = env
    _write(settings_store.SETTINGS_PATH, {"web": {"port": 1}})
    body = client.post("/api/v1/settings/import", json={"yaml": yaml.dump({"web": {"port": 8001}, "llm": {"primary": {"api_key": "sk-real-1234"}}})}).json()
    assert "check" in body
    assert not any("个错误" in w for w in body["warnings"])


# ---------- CLI ----------

@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch):
    monkeypatch.setenv("QUANT_NO_NOTIFY", "")
    monkeypatch.delenv("QUANT_NO_NOTIFY", raising=False)


def test_parse_args_check_config():
    assert main_mod.parse_args(["--check-config"]).check_config is True
    assert main_mod.parse_args([]).check_config is False


def _run_main(monkeypatch, config, capsys):
    monkeypatch.setattr(main_mod, "load_config", lambda *a, **k: config)
    monkeypatch.setattr(sys, "argv", ["main.py", "--check-config"])
    try:
        main_mod.main()
        code = 0
    except SystemExit as e:
        code = int(e.code or 0)
    return code, capsys.readouterr().out


def test_cli_check_config_exit_codes(monkeypatch, capsys):
    good = _merge(_example(), {"llm": {"primary": {"api_key": "sk-ok-1234"}}})
    code, out = _run_main(monkeypatch, good, capsys)
    assert code == 0, out
    bad = _merge(_example(), {"llm": {"primary": {"api_key": "sk-ok-1234"}}, "web": {"port": 0}})
    code, out = _run_main(monkeypatch, bad, capsys)
    assert code == 1
    assert "web.port" in out


def test_cli_check_config_no_key_exit_1(monkeypatch, capsys):
    cfg = _example()                                  # 主模型 Key 是占位
    code, out = _run_main(monkeypatch, cfg, capsys)
    assert code == 1
    assert "llm.primary" in out
