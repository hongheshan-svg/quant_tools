"""
推送公共工具
各机器人对单条消息有字节上限（企业微信 markdown 4096 字节），超长内容按行拆成多条依次发送。
"""

from __future__ import annotations


def split_by_bytes(content: str, max_bytes: int) -> list[str]:
    """按行把内容拆成每段不超过 max_bytes 字节（UTF-8）的若干段；单行超长时按字符硬切。"""
    chunks: list[str] = []
    current: list[str] = []
    size = 0
    for line in content.split("\n"):
        pieces = [line]
        if len(line.encode("utf-8")) > max_bytes:
            pieces, buf = [], ""
            for ch in line:
                if len((buf + ch).encode("utf-8")) > max_bytes:
                    pieces.append(buf)
                    buf = ""
                buf += ch
            pieces.append(buf)
        for piece in pieces:
            piece_size = len(piece.encode("utf-8")) + 1  # 含换行
            if current and size + piece_size > max_bytes:
                chunks.append("\n".join(current))
                current, size = [], 0
            current.append(piece)
            size += piece_size
    if current:
        chunks.append("\n".join(current))
    return [c for c in chunks if c.strip()] or [""]


def paged_titles(title: str, count: int) -> list[str]:
    """多条消息时在标题后加 (1/3) 这样的页码。"""
    if count <= 1:
        return [title]
    return [f"{title}（{i}/{count}）" for i in range(1, count + 1)]


def markdown_to_text(content: str) -> str:
    """去掉标题、粗体、引用、分隔线、代码等 markdown 标记，给不支持 markdown 的渠道发纯文本。"""
    import re

    lines = []
    for raw in content.split("\n"):
        line = raw.rstrip()
        stripped = line.strip()
        if re.fullmatch(r"[-*_]{3,}", stripped):
            continue
        line = re.sub(r"^\s*#{1,6}\s*", "", line)
        line = re.sub(r"^\s*>\s?", "", line)
        line = re.sub(r"^(\s*)[*+]\s+", r"\1- ", line)
        line = re.sub(r"\*\*(.+?)\*\*", r"\1", line)
        line = re.sub(r"__(.+?)__", r"\1", line)
        line = line.replace("`", "")
        lines.append(line)
    return "\n".join(lines).strip()


def markdown_to_slack(content: str) -> str:
    """转成 Slack mrkdwn：**粗体** 变 *粗体*，标题变 *标题*，分隔线去掉。"""
    import re

    lines = []
    for raw in content.split("\n"):
        if re.fullmatch(r"\s*[-*_]{3,}\s*", raw):
            continue
        line = re.sub(r"^\s*#{1,6}\s*(.+?)\s*$", r"*\1*", raw)
        line = re.sub(r"\*\*(.+?)\*\*", r"*\1*", line)
        lines.append(line)
    return "\n".join(lines)


def send_paged(title: str, content: str, max_bytes: int, send_one) -> bool:
    """拆分后逐段调用 send_one(标题, 内容)，某段失败不影响后续，全部成功才返回 True。"""
    chunks = split_by_bytes(content, max_bytes)
    return all([send_one(t, c) for t, c in zip(paged_titles(title, len(chunks)), chunks)])


def markdown_to_html(content: str) -> str:
    """把推送用的简单 markdown（标题、列表、引用、粗体、分隔线）转成邮件 HTML。"""
    import html
    import re

    bold = re.compile(r"\*\*(.+?)\*\*")
    parts, in_list = [], False
    for raw in content.split("\n"):
        line = bold.sub(r"<b>\1</b>", html.escape(raw.strip()))
        is_item = raw.strip().startswith(("- ", "* "))
        if in_list and not is_item:
            parts.append("</ul>")
            in_list = False
        if not line:
            continue
        if is_item:
            if not in_list:
                parts.append("<ul>")
                in_list = True
            parts.append(f"<li>{line[2:]}</li>")
        elif raw.strip().startswith("#"):
            level = min(len(raw.strip()) - len(raw.strip().lstrip("#")), 4) + 1
            parts.append(f"<h{level}>{line.lstrip('#').strip()}</h{level}>")
        elif raw.strip().startswith("&gt;") or raw.strip().startswith(">"):
            parts.append(f"<blockquote style='color:#666'>{line.removeprefix('&gt;').strip()}</blockquote>")
        elif raw.strip() == "---":
            parts.append("<hr>")
        else:
            parts.append(f"<p>{line}</p>")
    if in_list:
        parts.append("</ul>")
    return "<html><body style='font-family:sans-serif;font-size:14px'>" + "\n".join(parts) + "</body></html>"
