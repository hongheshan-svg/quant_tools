"""部署默认规则：首次启动可加载，升级保留用户规则并更新示例。"""

import shlex
import shutil
import subprocess
from pathlib import Path

import server
from src.strategy.screening_rules import load_rules

ROOT = Path(__file__).resolve().parents[1]


def test_docker_only_changes_run_backend_deployment_regressions():
    from scripts.ci_changes import affected_jobs

    assert affected_jobs(["docker/Dockerfile"])["backend"]
    assert affected_jobs(["docker/entrypoint.sh"])["backend"]


def test_desktop_startup_loads_rules_and_preserves_custom_rules_on_upgrade(tmp_path, monkeypatch):
    monkeypatch.chdir(ROOT)
    bundle = tmp_path / "bundle"
    (bundle / "config").mkdir(parents=True)
    example = bundle / "config" / "screening_rules.yaml.example"
    shutil.copyfile(ROOT / "config" / "screening_rules.yaml.example", example)
    workdir = tmp_path / "用户数据"

    server.prepare_workdir(workdir, bundle)
    assert len(load_rules("config/screening_rules.yaml")) == 5

    custom = workdir / "config" / "screening_rules.yaml"
    custom.write_text(example.read_text(encoding="utf-8").replace("价值质量", "自定义价值策略"), encoding="utf-8")
    example.write_text(example.read_text(encoding="utf-8").replace("价值质量", "新版默认价值策略"), encoding="utf-8")
    server.prepare_workdir(workdir, bundle)

    assert load_rules("config/screening_rules.yaml")[0]["label"] == "自定义价值策略"
    assert load_rules("config/screening_rules.yaml.example")[0]["label"] == "新版默认价值策略"


def test_docker_default_files_enable_rules_and_preserve_custom_rules(tmp_path):
    app = tmp_path / "container app"
    defaults = app / "defaults" / "config"
    defaults.mkdir(parents=True)
    # 按真实 Docker COPY 清单组装默认配置，避免测试用额外文件掩盖镜像遗漏。
    for line in (ROOT / "docker" / "Dockerfile").read_text(encoding="utf-8").splitlines():
        if not line.startswith("COPY "):
            continue
        parts = shlex.split(line)
        if parts and parts[0] == "COPY" and parts[-1] == "./defaults/config/":
            for source in parts[1:-1]:
                shutil.copyfile(ROOT / source, defaults / Path(source).name)

    script = (ROOT / "docker" / "entrypoint.sh").read_text(encoding="utf-8")
    # 仅运行配置初始化，权限切换和服务启动不属于本测试。
    startup = script.split('if [ "$(id -u)" = "0" ]; then', 1)[0].replace("/app", shlex.quote(str(app)))
    subprocess.run(["sh", "-c", startup], check=True, capture_output=True, text=True)
    rules = app / "config" / "screening_rules.yaml"
    assert len(load_rules(str(rules))) == 5

    rules.write_text((defaults / "screening_rules.yaml.example").read_text(encoding="utf-8").replace("价值质量", "用户价值策略"), encoding="utf-8")
    subprocess.run(["sh", "-c", startup], check=True, capture_output=True, text=True)
    assert load_rules(str(rules))[0]["label"] == "用户价值策略"
