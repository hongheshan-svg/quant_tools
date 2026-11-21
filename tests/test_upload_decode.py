"""上传文件解码：带 BOM 的 UTF-16/UTF-32、券商导出的「文本 .xls」（同步 DSA 8179a7be 并扩展到交割单）。"""
import codecs

import pytest

from src.services import real_portfolio as rp
from src.services.watchlist import read_import_file
from src.utils.text_decode import decode_upload, is_real_excel
from tests.test_real_import_preview import CSV

TEXT = "证券代码,证券名称\n600519,贵州茅台\n"


@pytest.mark.parametrize("raw", [
    TEXT.encode("utf-16"),                                   # Python 写入 BOM（本机字节序）
    codecs.BOM_UTF16_LE + TEXT.encode("utf-16-le"),
    codecs.BOM_UTF16_BE + TEXT.encode("utf-16-be"),
    codecs.BOM_UTF32_LE + TEXT.encode("utf-32-le"),          # BOM 以 UTF-16 LE 的 BOM 开头
    TEXT.encode("utf-8-sig"),
    TEXT.encode("gbk"),
])
def test_decode_upload_encodings(raw):
    assert decode_upload(raw).lstrip("﻿") == TEXT


def test_decode_upload_falls_back_without_raising():
    assert "600519" in decode_upload(b"\xff\xfe\x00" + b"600519")      # 坏的 UTF-16 也不抛异常
    assert not is_real_excel(TEXT.encode("utf-16")) and is_real_excel(b"PK\x03\x04rest")


@pytest.mark.parametrize("name", ["list.csv", "list.txt", "导出.xls"])
def test_watchlist_import_reads_utf16(tmp_path, name):
    path = tmp_path / name
    path.write_bytes(TEXT.replace(",", "\t").encode("utf-16"))
    assert "600519" in read_import_file(str(path)) and "贵州茅台" in read_import_file(str(path))


@pytest.mark.parametrize("name,separator", [("交割单.csv", ","), ("交割单.txt", "\t"), ("交割单.xls", "\t")])
def test_trade_import_reads_utf16_and_text_xls(tmp_path, name, separator):
    path = tmp_path / name
    path.write_bytes(CSV.replace(",", separator).encode("utf-16"))
    trades, actions, _, error = rp.parse_import_rows(rp.read_rows(str(path)))
    assert error == "" and len(trades) == 1 and trades[0]["code"] == "600519"
    assert {a["action"] for a in actions} == {"dividend", "bonus", "tax"}
