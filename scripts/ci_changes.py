"""GitHub Actions 路径分流；发布和无法确定变更范围时执行全部门禁。"""

import os
import subprocess


def affected_jobs(paths: list[str]) -> dict[str, bool]:
    common = any(path.startswith(".github/workflows/") for path in paths)
    backend = common or any(path.startswith(("src/", "api/", "tests/", "config/", "scripts/", "evals/", "docker/")) or path in ("main.py", "server.py", "requirements.txt") or path.startswith("run_") for path in paths)
    web = common or any(path.startswith(("apps/web/", "api/")) for path in paths)
    desktop = common or any(path.startswith("apps/desktop/") or path in ("server.py", "requirements.txt", "scripts/build_desktop.py", "scripts/smoke_backend.py", "scripts/macos_signature_audit.sh") for path in paths)
    return {"backend": backend, "web": web, "desktop": desktop}


if __name__ == "__main__":
    base = os.getenv("DIFF_BASE", "")
    if not base or set(base) == {"0"} or os.getenv("GITHUB_REF", "").startswith("refs/tags/"):
        jobs = dict.fromkeys(("backend", "web", "desktop"), True)
    else:
        diff = subprocess.run(["git", "diff", "--name-only", base, "HEAD"], capture_output=True, text=True)
        jobs = affected_jobs(diff.stdout.splitlines()) if diff.returncode == 0 else dict.fromkeys(("backend", "web", "desktop"), True)
    output = "\n".join(f"{job}={str(enabled).lower()}" for job, enabled in jobs.items()) + "\n"
    with open(os.environ["GITHUB_OUTPUT"], "a") as stream:
        stream.write(output)
