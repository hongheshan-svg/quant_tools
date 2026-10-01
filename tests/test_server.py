"""server.py：数据目录准备（桌面端 / 打包后运行）"""

from pathlib import Path

import server


def test_prepare_workdir_copies_bundled_config(tmp_path, monkeypatch):
    monkeypatch.chdir(Path.cwd())  # 结束后恢复工作目录
    bundle = tmp_path / "bundle"
    (bundle / "config").mkdir(parents=True)
    (bundle / "config" / "settings.yaml.example").write_text("web:\n  port: 8000\n", encoding="utf-8")
    (bundle / "config" / "stock_pool.yaml").write_text("blacklist: []\n", encoding="utf-8")
    workdir = tmp_path / "用户数据"

    server.prepare_workdir(workdir, bundle)
    assert Path.cwd() == workdir.resolve()
    assert (workdir / "config" / "settings.yaml.example").read_text(encoding="utf-8") == "web:\n  port: 8000\n"
    assert (workdir / "config" / "stock_pool.yaml").exists()

    # 升级后：示例配置更新为新版本，用户改过的股票池规则保留
    (bundle / "config" / "settings.yaml.example").write_text("web:\n  port: 9000\n", encoding="utf-8")
    (workdir / "config" / "stock_pool.yaml").write_text("blacklist: ['600000']\n", encoding="utf-8")
    server.prepare_workdir(workdir, bundle)
    assert "9000" in (workdir / "config" / "settings.yaml.example").read_text(encoding="utf-8")
    assert "600000" in (workdir / "config" / "stock_pool.yaml").read_text(encoding="utf-8")


def test_prepare_workdir_without_bundle(tmp_path, monkeypatch):
    monkeypatch.chdir(Path.cwd())
    server.prepare_workdir(tmp_path / "w")
    assert (tmp_path / "w" / "config").is_dir()
    assert server.bundle_dir() is None


def test_use_system_browser_dir(monkeypatch):
    from src.services import setup_status

    # 先 setenv 再 delenv，测试结束后才会还原为原来的状态
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", "x")
    monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH")
    server.use_system_browser_dir()
    # 打包后 Playwright 不再改用安装包内的目录，下载、启动和配置向导检查的是同一个目录
    assert server.os.environ["PLAYWRIGHT_BROWSERS_PATH"] == setup_status.default_browsers_dir()
    assert setup_status._browser_dirs() == [setup_status.default_browsers_dir()]

    # 用户自己指定的目录不覆盖
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", "/opt/browsers")
    server.use_system_browser_dir()
    assert server.os.environ["PLAYWRIGHT_BROWSERS_PATH"] == "/opt/browsers"


def test_self_check_passes_and_reports_failures(monkeypatch, capsys):
    assert server.self_check() == 0
    assert "Self-check passed" in capsys.readouterr().out

    import py_mini_racer

    class Broken:
        def eval(self, code):
            raise OSError("icudtl.dat not found")

    monkeypatch.setattr(py_mini_racer, "MiniRacer", Broken)
    assert server.self_check() == 1
    out = capsys.readouterr().out
    assert "FAIL  mini_racer: OSError: icudtl.dat not found" in out and "ok    pinyin" in out
