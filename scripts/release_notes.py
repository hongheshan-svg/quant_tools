"""
生成 GitHub Release 说明（中文）：版本亮点 + 桌面端下载 + 首次打开说明 + Docker 镜像。

    python scripts/release_notes.py v1.0.0                       # 输出到标准输出
    python scripts/release_notes.py v1.0.0 --repo owner/repo -o notes.md

版本亮点取自 .github/release-notes/<tag>.md（没有时省略）；最后附「完整变更记录」链接：与上一个版本标签对比，
首个版本列出全部提交（上一个标签默认用 git describe 查找，也可以用 --previous-tag 指定）。
GitHub 发布页面会把单个换行显示为换行，所以每个段落、列表项都写在一行里。
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NOTES_DIR = ROOT / ".github" / "release-notes"
DEFAULT_REPO = "hongheshan-svg/quant_tools"


def build_notes(tag: str, repo: str, highlights: str = "", previous_tag: str = "") -> str:
    version = tag.removeprefix("v")
    major_minor = ".".join(version.split(".")[:2])
    base = f"https://github.com/{repo}/releases/download/{tag}"
    image = f"ghcr.io/{repo.lower()}"
    changelog = (f"https://github.com/{repo}/compare/{previous_tag}...{tag}" if previous_tag
                 else f"https://github.com/{repo}/commits/{tag}")
    files = {
        "windows": f"AStockQuant-{version}-windows-setup.exe",
        "macos": f"AStockQuant-{version}-macos-arm64.dmg",
        "linux": f"AStockQuant-{version}-linux-x86_64.AppImage",
    }
    parts = []
    if highlights.strip():
        parts.append(highlights.strip())
    parts.append(f"""## 下载

| 系统 | 安装包 | 说明 |
| --- | --- | --- |
| Windows 10/11（x64） | [{files['windows']}]({base}/{files['windows']}) | 安装程序 |
| macOS 13 及以上（Apple 芯片） | [{files['macos']}]({base}/{files['macos']}) | 拖进「应用程序」 |
| Linux x64（glibc 2.35+，如 Ubuntu 22.04 及以上） | [{files['linux']}]({base}/{files['linux']}) | 免安装，直接运行 |

桌面端已内置后台服务和 Web 界面，不需要安装 Python 或 Node.js。启动时会检查本页的新版本（Windows、Linux 下载后重启即完成更新，macOS 提示前往下载）。

### 首次打开

- **Windows：** 出现「Windows 已保护你的电脑」时，点「更多信息 → 仍要运行」。
- **macOS：** 应用没有经过 Apple 公证。首次打开被拦截时，到「系统设置 → 隐私与安全性」点「仍要打开」，或在终端执行 `xattr -cr /Applications/AStockQuant.app`。
- **Linux：** 执行 `chmod +x {files['linux']}` 后直接运行。AppImage 需要 FUSE 2（`sudo apt install libfuse2`，Ubuntu 24.04 起为 `libfuse2t64`）。
- 首次启动会在后台下载采集用的 Chromium（约 150 MB）。在【设置 → AI 模型】填写大模型 API Key 后即可使用 AI 功能，首页的配置向导会列出还没完成的项目。
- 数据目录：Windows 为 `%APPDATA%\\quant-tools-desktop`，macOS 为 `~/Library/Application Support/quant-tools-desktop`，Linux 为 `~/.config/quant-tools-desktop`。

## Docker

多架构镜像（`linux/amd64`、`linux/arm64`）发布在 GitHub Container Registry，标签为 `{version}`、`{major_minor}` 和 `latest`：

```bash
docker pull {image}:{version}
docker run -d --name quant-tools -p 8000:8000 \\
  -e QUANT__WEB__AUTH_ENABLED=true \\
  -e QUANT__LLM__PRIMARY__API_KEY=sk-xxx \\
  -v "$PWD/config:/app/config" -v "$PWD/data:/app/data" -v "$PWD/logs:/app/logs" \\
  {image}:{version}
```

浏览器打开 http://localhost:8000 ，首次登录时设置密码（容器外的访问都需要登录）。配置可以挂载 `config/settings.yaml`，也可以用 `QUANT__` 开头的环境变量覆盖（如 `QUANT__LLM__PRIMARY__API_KEY` 覆盖 `llm.primary.api_key`）。镜像运行 Web 界面、API 和定时任务；`docker run --rm ... {image}:{version} python main.py --once` 可手动执行一遍收盘后任务。

**完整变更记录**：{changelog}

> 本项目仅供学习和研究使用，输出结果不构成任何投资建议。""")
    return "\n\n".join(parts) + "\n"


def previous_tag_of(tag: str) -> str:
    """tag 之前最近的版本标签；没有或不在 git 仓库里时返回空字符串"""
    try:
        out = subprocess.run(["git", "describe", "--tags", "--abbrev=0", "--match", "v*", f"{tag}^"],
                             cwd=ROOT, capture_output=True, text=True, timeout=10)
        return out.stdout.strip() if out.returncode == 0 else ""
    except Exception:
        return ""


def main() -> int:
    parser = argparse.ArgumentParser(description="生成 GitHub Release 说明")
    parser.add_argument("tag", help="版本标签，如 v1.0.0")
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY") or DEFAULT_REPO)
    parser.add_argument("--previous-tag", help="上一个版本标签（默认用 git describe 查找）")
    parser.add_argument("-o", "--output", help="输出文件（默认标准输出）")
    args = parser.parse_args()
    highlights_file = NOTES_DIR / f"{args.tag}.md"
    highlights = highlights_file.read_text(encoding="utf-8") if highlights_file.exists() else ""
    previous = args.previous_tag if args.previous_tag is not None else previous_tag_of(args.tag)
    notes = build_notes(args.tag, args.repo, highlights, previous)
    if args.output:
        Path(args.output).write_text(notes, encoding="utf-8")
    else:
        sys.stdout.write(notes)
    return 0


if __name__ == "__main__":
    sys.exit(main())
