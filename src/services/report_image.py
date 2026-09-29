"""Markdown 转分享图：先转成带内联样式的 HTML，再用 Playwright 无头 Chromium 截图成 PNG。"""

from __future__ import annotations

import hashlib
import html
import threading
from collections import OrderedDict

import re

FONT_STACK = '"PingFang SC","Microsoft YaHei","Noto Sans CJK SC",sans-serif'
_CACHE_SIZE = 20
_CACHE: OrderedDict[tuple[str, str], bytes] = OrderedDict()
_cache_lock = threading.Lock()

_CSS = f"""
* {{ box-sizing: border-box; }}
body {{ margin: 0; padding: 24px; background: #eef1f6; font-family: {FONT_STACK}; color: #1f2937; }}
.card {{ background: #fff; border-radius: 14px; padding: 28px 32px; box-shadow: 0 2px 12px rgba(15,23,42,.08); }}
.card > h1.title {{ margin: 0 0 16px; padding-bottom: 12px; border-bottom: 2px solid #3b82f6; font-size: 20px; color: #1d4ed8; }}
.content {{ font-size: 14px; line-height: 1.75; word-break: break-word; }}
.content h2, .content h3, .content h4, .content h5 {{ margin: 18px 0 8px; color: #111827; }}
.content h2 {{ font-size: 17px; }} .content h3 {{ font-size: 15px; border-left: 4px solid #3b82f6; padding-left: 8px; }}
.content p {{ margin: 6px 0; }} .content ul {{ margin: 6px 0; padding-left: 22px; }} .content li {{ margin: 3px 0; }}
.content code {{ background: #f3f4f6; padding: 1px 4px; border-radius: 3px; }}
.content blockquote {{ margin: 8px 0; padding: 6px 12px; background: #fff7ed; border-left: 4px solid #f59e0b; color: #92400e !important; }}
.content hr {{ border: 0; border-top: 1px solid #e5e7eb; margin: 14px 0; }}
.footer {{ margin-top: 18px; padding-top: 10px; border-top: 1px solid #e5e7eb; font-size: 12px; color: #9ca3af; text-align: center; }}
"""


_BOLD = re.compile(r"\*\*(.+?)\*\*")
_CODE = re.compile(r"`([^`]+)`")


def _inline(text: str) -> str:
    """转义后处理行内粗体、行内代码。"""
    return _CODE.sub(r"<code>\1</code>", _BOLD.sub(r"<strong>\1</strong>", html.escape(text)))


def _markdown_body(markdown: str) -> str:
    """简单 markdown（标题、列表、引用、粗体、分隔线）转 HTML 片段；先转义，避免注入。"""
    parts: list[str] = []
    in_list = False
    for raw in markdown.split("\n"):
        line = raw.strip()
        is_item = line.startswith(("- ", "* "))
        if in_list and not is_item:
            parts.append("</ul>")
            in_list = False
        if not line:
            continue
        if is_item:
            if not in_list:
                parts.append("<ul>")
                in_list = True
            parts.append(f"<li>{_inline(line[2:])}</li>")
        elif line.startswith("#"):
            level = min(len(line) - len(line.lstrip("#")), 4)
            parts.append(f"<h{level + 1 if level == 1 else level}>{_inline(line.lstrip('#').strip())}</h{level + 1 if level == 1 else level}>")
        elif line.startswith(">"):
            parts.append(f"<blockquote>{_inline(line[1:].strip())}</blockquote>")
        elif line == "---":
            parts.append("<hr>")
        else:
            parts.append(f"<p>{_inline(line)}</p>")
    if in_list:
        parts.append("</ul>")
    return "\n".join(parts)


def build_share_html(title: str, markdown: str, footer: str = "") -> str:
    """把 markdown 转成带内联 CSS 的完整 HTML（卡片样式，宽度由截图视口决定，适配 760px）。"""
    inner = _markdown_body(markdown or "")
    footer_html = f'<div class="footer">{html.escape(footer)}</div>' if footer else ""
    return (
        '<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{html.escape(title)}</title><style>{_CSS}</style></head><body>"
        f'<div class="card"><h1 class="title">{html.escape(title)}</h1>'
        f'<div class="content">{inner}</div>{footer_html}</div></body></html>'
    )


def render_png(html_text: str, width: int = 760) -> bytes:
    """用无头 Chromium 把 HTML 截成 PNG（2 倍清晰度、整页）；每次独立启动并关闭浏览器。"""
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                page = browser.new_page(viewport={"width": width, "height": 100}, device_scale_factor=2)
                page.set_content(html_text, wait_until="load")
                return page.screenshot(full_page=True, type="png")
            finally:
                browser.close()
    except Exception as e:  # Playwright 各类异常没有统一基类
        raise RuntimeError(f"生成分享图失败：{e}（可能需要执行 playwright install chromium）") from e


def render_markdown_image(title: str, markdown: str, footer: str = "") -> bytes:
    """markdown 转分享图 PNG；结果按（标题, 内容哈希）缓存最近 20 张。"""
    key = (title, hashlib.sha256(f"{markdown}\0{footer}".encode()).hexdigest())
    with _cache_lock:
        if key in _CACHE:
            _CACHE.move_to_end(key)
            return _CACHE[key]
    png = render_png(build_share_html(title, markdown, footer))
    with _cache_lock:
        _CACHE[key] = png
        while len(_CACHE) > _CACHE_SIZE:
            _CACHE.popitem(last=False)
    return png
