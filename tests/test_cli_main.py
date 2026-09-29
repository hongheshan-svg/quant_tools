"""main.py 命令行参数、推送检查，以及 GitHub Actions 工作流。"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
import yaml

import main as main_mod

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch):
    # 先 setenv 让 monkeypatch 记录原状态，teardown 时还原（原本不存在则删除），再删掉
    monkeypatch.setenv("QUANT_NO_NOTIFY", "")
    monkeypatch.delenv("QUANT_NO_NOTIFY", raising=False)


# ---------- parse_args ----------

def test_parse_args_defaults_and_old_flags():
    a = main_mod.parse_args([])
    assert not a.once and not a.steps and not a.stocks and not a.no_notify and not a.check_notify and not a.debug
    a = main_mod.parse_args(["--once", "--steps", "collect,analysis", "--debug"])
    assert a.once and a.steps == "collect,analysis" and a.debug


def test_parse_args_new_flags():
    a = main_mod.parse_args(["--stocks", "600519,000001", "--no-notify", "--check-notify"])
    assert a.stocks == "600519,000001" and a.no_notify and a.check_notify


def test_no_notify_sets_env(monkeypatch):
    _patch_heavy(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["main.py", "--once", "--no-notify"])
    monkeypatch.setattr("src.scheduler.run_once", lambda config, steps: [])
    main_mod.main()
    assert os.environ.get("QUANT_NO_NOTIFY") == "1"


# ---------- format_notify_check ----------

def _ch(name, enabled, configured=True, issues=()):
    return {"channel": name, "label": name, "enabled": enabled, "configured": configured, "issues": list(issues)}


def test_format_notify_check():
    text, ok = main_mod.format_notify_check({"channels": [_ch("wechat", True), _ch("email", False, False, ["缺少 SMTP 服务器"])], "routes": []})
    assert ok is True and "wechat" in text
    text, ok = main_mod.format_notify_check({"channels": [_ch("wechat", True, False, ["缺少 Webhook 地址"])], "routes": []})
    assert ok is False and "缺少 Webhook 地址" in text
    text, ok = main_mod.format_notify_check({"channels": [_ch("wechat", False), _ch("email", False)], "routes": []})
    assert ok is False                                                     # 没有任何启用渠道
    _, ok = main_mod.format_notify_check({"channels": [_ch("wechat", True)], "routes": ["路由指向未启用渠道"]})
    assert ok is False
    assert isinstance(text, str)


# ---------- main() ----------

def _patch_heavy(monkeypatch):
    monkeypatch.setattr(main_mod, "load_config", lambda *a, **k: {"database": {"sqlite_path": str(Path(__import__("tempfile").gettempdir()) / "cli_test.db")}, "notifier": {}})
    monkeypatch.setattr(main_mod, "init_db", lambda *a, **k: None)
    monkeypatch.setattr(main_mod, "setup_logging", lambda *a, **k: None)


def _exit_code(fn) -> int:
    with pytest.raises(SystemExit) as e:
        fn()
    return int(e.value.code or 0)


def test_once_steps_unchanged(monkeypatch):
    _patch_heavy(monkeypatch)
    calls = []
    monkeypatch.setattr("src.scheduler.run_once", lambda config, steps: calls.append(steps) or [{"label": "采集", "seconds": 1}])
    monkeypatch.setattr(sys, "argv", ["main.py", "--once", "--steps", "collect, analysis"])
    main_mod.main()
    assert calls == [["collect", "analysis"]]
    calls.clear()
    monkeypatch.setattr(sys, "argv", ["main.py", "--once"])
    main_mod.main()
    assert calls == [None]


def test_once_invalid_step_exits_2(monkeypatch):
    _patch_heavy(monkeypatch)

    def boom(config, steps):
        raise ValueError("未知步骤")

    monkeypatch.setattr("src.scheduler.run_once", boom)
    monkeypatch.setattr(sys, "argv", ["main.py", "--once", "--steps", "nope"])
    assert _exit_code(main_mod.main) == 2


@pytest.mark.parametrize("raw,expected", [
    ("600519,000001", ["600519", "000001"]),
    ("600519，000001", ["600519", "000001"]),
    ("600519, 000001，300750 ,", ["600519", "000001", "300750"]),
])
def test_stocks_calls_watchlist_report(monkeypatch, raw, expected):
    _patch_heavy(monkeypatch)
    from src.services.watchlist import WatchlistService
    from src.services.watchlist_report import WatchlistReportService

    monkeypatch.setattr(WatchlistService, "__init__", lambda self, config=None: None)
    monkeypatch.setattr(WatchlistService, "resolve", lambda self, text: (text, "测试") if text.isdigit() and len(text) == 6 else None)
    calls = []
    monkeypatch.setattr(WatchlistReportService, "run",
                        lambda self, push=True, progress=None, now=None, codes=None: calls.append((push, list(codes))) or {"total": len(codes), "done": len(codes), "failed": [], "pushed": True})
    monkeypatch.setattr(sys, "argv", ["main.py", "--stocks", raw])
    try:
        main_mod.main()
    except SystemExit as e:
        assert not e.code
    assert calls == [(True, expected)]


@pytest.mark.parametrize("ok,code", [(True, 0), (False, 1)])
def test_check_notify_exit_code(monkeypatch, ok, code):
    _patch_heavy(monkeypatch)
    channels = [_ch("wechat", True, ok, [] if ok else ["缺少 Webhook 地址"])]
    monkeypatch.setattr("src.notifier.diagnose", lambda config: {"channels": channels, "routes": []})
    monkeypatch.setattr(sys, "argv", ["main.py", "--check-notify"])
    try:
        main_mod.main()
        got = 0
    except SystemExit as e:
        got = int(e.code or 0)
    assert got == code


# ---------- workflow ----------

def _workflow() -> dict:
    return yaml.safe_load((ROOT / ".github/workflows/daily-analysis.yml").read_text(encoding="utf-8"))


def test_workflow_dispatch_inputs():
    wf = _workflow()
    on = wf.get("on", wf.get(True))
    inputs = on["workflow_dispatch"]["inputs"]
    assert "stocks" in inputs and "no_notify" in inputs and "steps" in inputs
    assert inputs["no_notify"]["type"] == "boolean"


def test_workflow_env_and_enabled_form():
    wf = _workflow()
    env = wf["jobs"]["run"]["env"]
    for key in ("QUANT__NOTIFIER__TELEGRAM__ENABLED", "QUANT__NOTIFIER__PUSHPLUS__TOKEN", "QUANT__NOTIFIER__SERVERCHAN__SENDKEY",
                "QUANT__NOTIFIER__BARK__DEVICE_KEY", "QUANT__NOTIFIER__WEBHOOK__URL", "QUANT__SEARCH__ENABLED",
                "QUANT__SEARCH__BOCHA__API_KEYS"):
        assert key in env, key
    enabled = {k: v for k, v in env.items() if k.endswith("__ENABLED")}
    assert enabled
    for k, v in enabled.items():
        s = str(v)
        assert "!= ''" in s and "&& 'true' || ''" in s, f"{k} 必须用 (... != '') && 'true' || '' 形式"


def test_workflow_run_scripts_do_not_inline_inputs():
    wf = _workflow()
    for step in wf["jobs"]["run"]["steps"]:
        assert "${{ inputs." not in str(step.get("run", "")), step.get("name")
        assert "${{ github.event.inputs." not in str(step.get("run", "")), step.get("name")
