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
