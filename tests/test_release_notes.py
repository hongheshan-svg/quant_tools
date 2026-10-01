"""发布说明生成：下载链接、Docker 标签按版本号生成，有版本亮点时放在最前面。"""

from scripts.release_notes import NOTES_DIR, build_notes, previous_tag_of


def test_build_notes_links_and_docker_tags():
    notes = build_notes("v1.2.3", "Owner/Repo", "## 主要功能\n\n- x")
    assert notes.startswith("## 主要功能")
    assert "https://github.com/Owner/Repo/releases/download/v1.2.3/AStockQuant-1.2.3-windows-setup.exe" in notes
    assert "AStockQuant-1.2.3-macos-arm64.dmg" in notes and "AStockQuant-1.2.3-linux-x86_64.AppImage" in notes
    assert "docker pull ghcr.io/owner/repo:1.2.3" in notes        # GHCR 镜像名必须小写
    assert "`1.2.3`、`1.2` 和 `latest`" in notes


def test_build_notes_without_highlights():
    assert build_notes("v2.0.0", "o/r").startswith("## 下载")


def test_highlights_exist_for_released_versions():
    assert (NOTES_DIR / "v1.0.0.md").exists()


def test_changelog_link():
    assert "**完整变更记录**：https://github.com/o/r/compare/v1.0.0...v1.1.0" in build_notes("v1.1.0", "o/r", previous_tag="v1.0.0")
    assert "**完整变更记录**：https://github.com/o/r/commits/v1.0.0" in build_notes("v1.0.0", "o/r")   # 首个版本


def test_previous_tag_of_unknown_tag():
    assert previous_tag_of("v999.0.0-not-a-tag") == ""
