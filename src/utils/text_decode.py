"""上传文件的文本解码与「伪 Excel」识别（自选股导入、交割单导入共用）。"""

from __future__ import annotations

import codecs

# 真正的 Excel：.xlsx 是 zip（PK），.xls 是 OLE 复合文档。券商软件导出的 .xls 常是制表符分隔的文本。
EXCEL_MAGIC = (b"PK\x03\x04", b"\xd0\xcf\x11\xe0")


def decode_upload(raw: bytes) -> str:
    """按 BOM 识别 UTF-32/UTF-16（UTF-32 LE 的 BOM 以 UTF-16 LE 的 BOM 开头，要先判断），
    否则依次试 UTF-8（可带 BOM）、GBK，都不行时按 UTF-8 忽略错误字节。"""
    for boms, encoding in (((codecs.BOM_UTF32_LE, codecs.BOM_UTF32_BE), "utf-32"),
                           ((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE), "utf-16")):
        if raw.startswith(boms):
            try:
                return raw.decode(encoding)
            except UnicodeDecodeError:
                break
    for encoding in ("utf-8-sig", "gbk"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="ignore")


def is_real_excel(raw: bytes) -> bool:
    return raw.startswith(EXCEL_MAGIC)
