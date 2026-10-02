"""
A股股票代码工具
不同数据源写入数据库的代码格式不同：有的存 "600519"，有的存 "sh600519"。
查询时要用 code_candidates() 同时匹配两种写法，比较时先用 bare_code() 去掉交易所前缀。
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

EXCHANGE_PREFIXES = ("sh", "sz", "bj")
CODE_LENGTH = 6
PREFIXED_CODE_LENGTH = 8
_CODE = re.compile(r"^(?:(sh|sz|bj)[.\-]?)?(\d{6})(?:\.(sh|sz|bj))?$", re.I)


class StockCodeError(ValueError):
    """标的代码无效或显式交易所与代码不一致。"""


@dataclass(frozen=True)
class StockIdentity:
    code: str
    exchange: str
    kind: str = "stock"
    market: str = "CN"
    currency: str = "CNY"


def _inferred_exchange(digits: str) -> str:
    if digits.startswith(("92", "4", "8")):
        return "bj"
    return "sh" if digits.startswith(("6", "9", "5")) else "sz"


def resolve_identity(code: str | None) -> StockIdentity:
    """单一 A 股/ETF/指数身份入口；显式指数优先，裸数字不会被猜成指数。"""
    raw = unicodedata.normalize("NFKC", str(code or "")).strip().lower()
    from src.services.fund_registry import ETF_PREFIXES, INDEX_CODES, _INDEX_BY_NAME

    index = INDEX_CODES.get(raw) or _INDEX_BY_NAME.get(raw)
    if index:
        return StockIdentity(index["code"], index["code"][:2], "index")
    match = _CODE.fullmatch(raw)
    if not match:
        raise StockCodeError("股票代码应为 6 位数字，可带 sh/sz/bj 前缀或 .SH/.SZ/.BJ 后缀")
    prefix, digits, suffix = match.groups()
    if prefix and suffix and prefix != suffix:
        raise StockCodeError("股票代码的交易所前后缀冲突")
    explicit = prefix or suffix
    index = INDEX_CODES.get(f"{explicit}{digits}") if explicit else None
    if index:
        return StockIdentity(index["code"], explicit, "index")
    expected = _inferred_exchange(digits)
    if explicit and explicit != expected:
        raise StockCodeError(f"交易所与股票代码冲突：{digits} 应使用 {expected.upper()}")
    return StockIdentity(digits, expected, "etf" if digits.startswith(ETF_PREFIXES) else "stock")


def bare_code(code: str | None) -> str:
    """去掉 sh/sz/bj 前缀，返回 6 位代码（无法识别时原样返回小写去空格的输入）。"""
    raw = unicodedata.normalize("NFKC", str(code or "")).strip().lower()
    if _CODE.fullmatch(raw):
        identity = resolve_identity(raw)
        return identity.code[2:] if identity.kind == "index" else identity.code
    return raw


def exchange_of(code: str | None) -> str:
    """按代码段判断交易所：92/4/8 开头为北交所，6/9/5 开头为上交所，其余为深交所。"""
    bare = bare_code(code)
    return _inferred_exchange(bare)


def prefixed_code(code: str | None) -> str:
    """带交易所前缀的代码，如 600519 → sh600519。"""
    bare = bare_code(code)
    return f"{exchange_of(bare)}{bare}" if len(bare) == CODE_LENGTH and bare.isdigit() else bare


def code_candidates(code: str | None) -> list[str]:
    """同一只股票在数据库中可能的代码写法（带或不带交易所前缀）。"""
    raw = (code or "").strip().lower()
    if not raw:
        return []
    if not _CODE.fullmatch(raw):
        return [raw]
    identity = resolve_identity(raw)
    if identity.kind == "index":
        digits = identity.code[2:]
        return list(dict.fromkeys([identity.code, f"{digits}.{identity.exchange}", raw]))
    return list(dict.fromkeys([identity.code, f"{identity.exchange}{identity.code}",
                               f"{identity.code}.{identity.exchange}", raw]))


def board_of(code: str | None) -> str:
    """所属板块：主板 / 创业板 / 科创板 / 北交所。"""
    bare = bare_code(code)
    if exchange_of(bare) == "bj":
        return "北交所"
    if bare.startswith("30"):
        return "创业板"
    if bare.startswith("68"):
        return "科创板"
    return "主板"


def is_st(name: str | None) -> bool:
    return "ST" in (name or "").upper()


def daily_limit_pct(code: str | None, name: str = "") -> float:
    """涨跌幅限制：ST 5%，创业板/科创板 20%，北交所 30%，其余 10%。"""
    if is_st(name):
        return 0.05
    return {"创业板": 0.20, "科创板": 0.20, "北交所": 0.30}.get(board_of(code), 0.10)


def diagnosis_code(code: str | None) -> str:
    """诊断记录的存储代码：内置指数保留带交易所前缀的规范代码（如 sh000300），其余按个股/ETF 取 6 位。

    指数与个股的 6 位代码会冲突（000001 上证指数 vs 平安银行），指数代码绝不能调用 bare_code。
    """
    return resolve_identity(code).code


def normalize_name(name: str | None) -> str:
    """规范化股票名称：NFKC 转半角后删除全部空白（交易所简称如「万  科Ａ」→「万科A」）。"""
    text = unicodedata.normalize("NFKC", str(name or ""))
    return "".join(ch for ch in text if not ch.isspace())


_CJK = r"[一-鿿]"
_EVENT_MARK = re.compile(rf"^(?:XD|XR|DR|N|C)(?=\*?ST|{_CJK})")
_ST_PREFIX = re.compile(rf"^(?:S\*ST|\*ST|SST|ST|S)(?={_CJK})")
_SHARE_CLASS = re.compile(rf"(?<={_CJK})[AB]$")


def name_variants(name: str | None) -> list[str]:
    """新闻/资讯匹配用的名称变体：原名、去掉 XD/N 等标记、去掉 ST 前缀、去掉结尾的 A/B 股后缀。"""
    base = normalize_name(name)
    variants = [base]
    current = _EVENT_MARK.sub("", base)
    variants.append(current)
    current = _ST_PREFIX.sub("", current)
    variants.append(current)
    variants.append(_SHARE_CLASS.sub("", current))
    result: list[str] = []
    for v in variants:
        if len(v) >= 2 and v not in result:
            result.append(v)
    return result
