"""
生成 GitHub Release 说明（英文，发布页面面向所有用户）：版本亮点 + 桌面端下载 + 首次打开说明 + Docker 镜像。

    python scripts/release_notes.py v1.0.0                       # 输出到标准输出
    python scripts/release_notes.py v1.0.0 --repo owner/repo -o notes.md

版本亮点取自 .github/release-notes/<tag>.md（没有时省略）。GitHub 自动生成的变更列表由发布工作流
（softprops/action-gh-release 的 generate_release_notes）追加在后面。
"""

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NOTES_DIR = ROOT / ".github" / "release-notes"
DEFAULT_REPO = "hongheshan-svg/quant_tools"


def build_notes(tag: str, repo: str, highlights: str = "") -> str:
    version = tag.removeprefix("v")
    major_minor = ".".join(version.split(".")[:2])
    base = f"https://github.com/{repo}/releases/download/{tag}"
    image = f"ghcr.io/{repo.lower()}"
    files = {
        "windows": f"AStockQuant-{version}-windows-setup.exe",
        "macos": f"AStockQuant-{version}-macos-arm64.dmg",
        "linux": f"AStockQuant-{version}-linux-x86_64.AppImage",
    }
    parts = []
    if highlights.strip():
        parts.append(highlights.strip())
    parts.append(f"""## Downloads

| Platform | Package | |
| --- | --- | --- |
| Windows 10/11 (x64) | [{files['windows']}]({base}/{files['windows']}) | Installer |
| macOS 13+ (Apple silicon) | [{files['macos']}]({base}/{files['macos']}) | Drag into Applications |
| Linux x64 (glibc 2.35+, e.g. Ubuntu 22.04+) | [{files['linux']}]({base}/{files['linux']}) | Portable, no install needed |

The desktop app bundles the backend service and web UI; no Python or Node.js is required. It checks this page for updates (Windows and Linux update in place, macOS links to the download).

### First launch

- **Windows:** if SmartScreen shows "Windows protected your PC", click **More info → Run anyway**.
- **macOS:** the app is not notarized by Apple. If it is blocked on first launch, open **System Settings → Privacy & Security** and click **Open Anyway**, or run `xattr -cr /Applications/AStockQuant.app`.
- **Linux:** `chmod +x {files['linux']}` and run it. AppImage needs FUSE 2 (`sudo apt install libfuse2`, or `libfuse2t64` on Ubuntu 24.04+).
- On first start the app downloads Chromium (~150 MB) in the background for browser-based data sources. Fill in an LLM API key in **Settings → AI model** to enable the AI features; the setup wizard on the home page lists what is still missing.
- Data directory: `%APPDATA%\\quant-tools-desktop` (Windows), `~/Library/Application Support/quant-tools-desktop` (macOS), `~/.config/quant-tools-desktop` (Linux).

## Docker

Multi-arch image (`linux/amd64`, `linux/arm64`) on GitHub Container Registry, tagged `{version}`, `{major_minor}` and `latest`:

```bash
docker pull {image}:{version}
docker run -d --name quant-tools -p 8000:8000 \\
  -e QUANT__WEB__AUTH_ENABLED=true \\
  -e QUANT__LLM__PRIMARY__API_KEY=sk-xxx \\
  -v "$PWD/config:/app/config" -v "$PWD/data:/app/data" -v "$PWD/logs:/app/logs" \\
  {image}:{version}
```

Open http://localhost:8000 and set a password on first login (requests from outside the container always require login). Settings come from a mounted `config/settings.yaml` or `QUANT__`-prefixed environment variables (`QUANT__LLM__PRIMARY__API_KEY` overrides `llm.primary.api_key`). The image runs the web UI, API and scheduled jobs; `docker run --rm ... {image}:{version} python main.py --once` runs the after-close pipeline once.

> For learning and research only. Nothing produced by this software is investment advice.""")
    return "\n\n".join(parts) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="生成 GitHub Release 说明")
    parser.add_argument("tag", help="版本标签，如 v1.0.0")
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY") or DEFAULT_REPO)
    parser.add_argument("-o", "--output", help="输出文件（默认标准输出）")
    args = parser.parse_args()
    highlights_file = NOTES_DIR / f"{args.tag}.md"
    highlights = highlights_file.read_text(encoding="utf-8") if highlights_file.exists() else ""
    notes = build_notes(args.tag, args.repo, highlights)
    if args.output:
        Path(args.output).write_text(notes, encoding="utf-8")
    else:
        sys.stdout.write(notes)
    return 0


if __name__ == "__main__":
    sys.exit(main())
