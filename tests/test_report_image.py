"""分享图：HTML 构建（纯函数）与渲染缓存。"""

from __future__ import annotations

import pytest

from src.services import report_image


def test_build_html_basic():
    html = report_image.build_share_html("贵州茅台 AI 诊断", "## 结论\n\n- 买入\n- **重点** 观察\n", "生成于 2026-09-29")
    assert "<html" in html.lower()
    assert "贵州茅台 AI 诊断" in html and "生成于 2026-09-29" in html
    assert "<h2" in html and "<li" in html and "<strong>重点</strong>" in html


def test_build_html_escapes():
    html = report_image.build_share_html("<script>alert(1)</script>", "x <script>alert(2)</script>", "<img src=x>")
    assert "<script>" not in html and "<img src=x>" not in html


def test_build_html_empty_and_long():
    assert "标题" in report_image.build_share_html("标题", "")
    big = "\n".join(f"- 第 {i} 项 **粗**" for i in range(5000))
    assert len(report_image.build_share_html("长", big)) > len(big)


@pytest.fixture
def counting(monkeypatch):
    calls = []

    def fake(html, width=760):
        calls.append(html)
        return b"\x89PNG-" + str(len(calls)).encode()
    monkeypatch.setattr(report_image, "render_png", fake)
    cache = getattr(report_image, "_CACHE", None)
    if hasattr(cache, "clear"):
        cache.clear()
    return calls


def test_render_cache(counting):
    a = report_image.render_markdown_image("T", "# md")
    b = report_image.render_markdown_image("T", "# md")
    assert a == b and len(counting) == 1
    report_image.render_markdown_image("T", "# other")
    assert len(counting) == 2


def test_cache_bounded_to_20(counting):
    for i in range(25):
        report_image.render_markdown_image("T", f"m{i}")
    assert len(counting) == 25
    report_image.render_markdown_image("T", "m24")  # 最近的仍命中
    assert len(counting) == 25
    report_image.render_markdown_image("T", "m0")  # 最早的已被淘汰
    assert len(counting) == 26
