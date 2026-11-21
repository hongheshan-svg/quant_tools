"""离线环境诊断 --doctor / GET /system/doctor：脱敏、分项隔离、退出码。"""
import json
import sys

import pytest

from src.database.db import init_db
from src.services import doctor
from tests.test_api import env  # noqa: F401
from tests.test_self_learning import _reset_db_engine

SECRETS = ("sk-live-abcdef1234567890", "sk-second-key-zzzz9999", "123456789:AAH-telegram-secret",
           "smtp-pass-0001", "https://open.feishu.cn/open-apis/bot/v2/hook/abcd-efgh-ijkl")


@pytest.fixture
def config(tmp_path):
    db = tmp_path / "data" / "quant.db"
    db.parent.mkdir()
    _reset_db_engine()
    init_db(str(db))
    yield {
        "database": {"sqlite_path": str(db)},
        "logging": {"log_dir": str(tmp_path / "logs")},
        "llm": {"primary": {"provider": "deepseek", "model": "deepseek-v4-flash", "api_key": [SECRETS[0], SECRETS[1]]},
                "cache_path": str(tmp_path / "data" / "llm.sqlite3")},
        "notifier": {"telegram": {"enabled": True, "bot_token": SECRETS[2], "chat_id": "42"},
                     "email": {"enabled": False, "password": SECRETS[3]},
                     "feishu": {"enabled": True, "webhook_url": SECRETS[4]}},
    }
    _reset_db_engine()


def test_report_is_redacted_and_counts_keys(config, tmp_path):
    report = doctor.run_doctor(config, tmp_path / "missing.yaml")
    text = json.dumps(report, ensure_ascii=False) + doctor.format_doctor(report)
    for secret in SECRETS:
        assert secret not in text
    assert "/Users/" not in text and "/home/" not in text
    llm = report["checks"]["llm"]
    assert llm["ready"] and llm["roles"]["primary"] == {"provider": "deepseek", "model": "deepseek-v4-flash", "keys": 2, "ready": True}
    assert "Telegram" in report["checks"]["notifier"]["configured_channels"]
    assert report["checks"]["storage"]["status"] == "ok"
    assert report["environment"]["packages"]["fastapi"] not in ("未安装", "未知")


def test_missing_llm_key_and_unwritable_storage_are_errors(config, tmp_path):
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x")
    config["llm"]["primary"]["api_key"] = "your-deepseek-api-key-here"
    config["logging"]["log_dir"] = str(blocker / "logs")
    report = doctor.run_doctor(config, tmp_path / "missing.yaml")
    assert report["status"] == "error"
    assert report["checks"]["llm"]["status"] == "error" and report["checks"]["llm"]["roles"]["primary"]["keys"] == 0
    assert report["checks"]["storage"]["log_dir_writable"] is False
    assert "有错误" in doctor.format_doctor(report)


def test_one_failing_check_does_not_hide_others(config, tmp_path, monkeypatch):
    import src.services.setup_status as setup_mod

    def boom(cfg):
        raise RuntimeError(f"读取失败 {SECRETS[0]}")
    monkeypatch.setattr(setup_mod, "setup_status", boom)
    report = doctor.run_doctor(config, tmp_path / "missing.yaml")
    assert report["checks"]["setup"]["status"] == "error" and "配置向导" in report["checks"]["setup"]["error"]
    assert SECRETS[0] not in report["checks"]["setup"]["error"]
    assert report["checks"]["llm"]["ready"] is True


@pytest.mark.parametrize("ready,code", [(True, 0), (False, 1)])
def test_cli_exit_code_and_json(config, monkeypatch, capsys, ready, code):
    import main as main_mod

    monkeypatch.setenv("QUANT_NO_NOTIFY", "")
    if not ready:
        config["llm"]["primary"]["api_key"] = ""
    monkeypatch.setattr(main_mod, "load_config", lambda *a, **k: config)
    monkeypatch.setattr(sys, "argv", ["main.py", "--doctor", "--json"])
    with pytest.raises(SystemExit) as caught:
        main_mod.main()
    out = json.loads(capsys.readouterr().out)
    assert caught.value.code == (1 if out["status"] == "error" else 0)
    assert (out["checks"]["llm"]["ready"] is True) == ready and SECRETS[0] not in json.dumps(out)


def test_api_returns_redacted_report_with_text(env):
    client, _, _ = env
    response = client.get("/api/v1/system/doctor")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] in ("ok", "warn", "error") and body["text"].startswith("环境诊断")
    assert "sk-secret-1234" not in response.text and "mail-pass" not in response.text
