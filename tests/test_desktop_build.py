"""桌面端打包前消除新旧 JavaScript 引擎的模块覆盖，检查不访问网络。"""

from types import SimpleNamespace

import pytest

from scripts import build_desktop


def test_healthy_mini_racer_does_not_reinstall(monkeypatch):
    monkeypatch.setattr(build_desktop.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0))
    monkeypatch.setattr(build_desktop, "run", lambda *args: pytest.fail("healthy runtime must not reinstall"))
    build_desktop.prepare_mini_racer()


def test_legacy_module_is_repaired_and_rechecked(monkeypatch):
    checks, installs = [], []
    results = iter([SimpleNamespace(returncode=1, stderr="legacy mini-racer selected"), SimpleNamespace(returncode=0)])

    def probe(command, **kwargs):
        checks.append(command)
        return next(results)

    monkeypatch.setattr(build_desktop.subprocess, "run", probe)
    monkeypatch.setattr(build_desktop, "run", lambda command: installs.append(command))
    monkeypatch.setattr(build_desktop, "version", lambda name: "0.14.1")
    build_desktop.prepare_mini_racer()
    assert len(checks) == 2 and checks[0] == checks[1]
    assert installs == [[build_desktop.sys.executable, "-m", "pip", "install", "--force-reinstall", "--no-deps", "mini-racer==0.14.1"]]


def test_unusable_mini_racer_stops_before_packaging(monkeypatch):
    monkeypatch.setattr(build_desktop.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=1, stderr="native library unavailable"))
    monkeypatch.setattr(build_desktop, "run", lambda command: None)
    monkeypatch.setattr(build_desktop, "version", lambda name: "0.14.1")
    with pytest.raises(SystemExit, match="未开始打包"):
        build_desktop.build_backend()
