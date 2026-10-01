"""发布说明生成：下载链接、Docker 标签按版本号生成，有版本亮点时放在最前面。"""

from scripts.release_notes import NOTES_DIR, build_notes


def test_build_notes_links_and_docker_tags():
    notes = build_notes("v1.2.3", "Owner/Repo", "## Highlights\n\n- x")
    assert notes.startswith("## Highlights")
    assert "https://github.com/Owner/Repo/releases/download/v1.2.3/AStockQuant-1.2.3-windows-setup.exe" in notes
    assert "AStockQuant-1.2.3-macos-arm64.dmg" in notes and "AStockQuant-1.2.3-linux-x86_64.AppImage" in notes
    assert "docker pull ghcr.io/owner/repo:1.2.3" in notes        # GHCR 镜像名必须小写
    assert "`1.2.3`, `1.2` and `latest`" in notes


def test_build_notes_without_highlights():
    assert build_notes("v2.0.0", "o/r").startswith("## Downloads")


def test_highlights_exist_for_released_versions():
    assert (NOTES_DIR / "v1.0.0.md").exists()
