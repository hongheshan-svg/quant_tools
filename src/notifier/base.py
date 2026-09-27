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
