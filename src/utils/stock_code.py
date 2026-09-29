"""
A股股票代码工具
不同数据源写入数据库的代码格式不同：有的存 "600519"，有的存 "sh600519"。
查询时要用 code_candidates() 同时匹配两种写法，比较时先用 bare_code() 去掉交易所前缀。
"""

from __future__ import annotations

EXCHANGE_PREFIXES = ("sh", "sz", "bj")
CODE_LENGTH = 6
PREFIXED_CODE_LENGTH = 8


def bare_code(code: str | None) -> str:
    """去掉 sh/sz/bj 前缀，返回 6 位代码（无法识别时原样返回小写去空格的输入）。"""
    raw = (code or "").strip().lower()
    if len(raw) == PREFIXED_CODE_LENGTH and raw[:2] in EXCHANGE_PREFIXES and raw[2:].isdigit():
        return raw[2:]
    return raw


def exchange_of(code: str | None) -> str:
    """按代码段判断交易所：92/4/8 开头为北交所，6/9/5 开头为上交所，其余为深交所。"""
    bare = bare_code(code)
    if bare.startswith("92") or bare.startswith(("4", "8")):
        return "bj"
    if bare.startswith(("6", "9", "5")):
        return "sh"
    return "sz"


def prefixed_code(code: str | None) -> str:
    """带交易所前缀的代码，如 600519 → sh600519。"""
    bare = bare_code(code)
    return f"{exchange_of(bare)}{bare}" if len(bare) == CODE_LENGTH and bare.isdigit() else bare


def code_candidates(code: str | None) -> list[str]:
    """同一只股票在数据库中可能的代码写法（带或不带交易所前缀）。"""
    raw = (code or "").strip().lower()
    if not raw:
        return []
    bare = bare_code(raw)
    cands = [raw]
    if len(bare) == CODE_LENGTH and bare.isdigit():
        cands.extend([bare, *(f"{p}{bare}" for p in EXCHANGE_PREFIXES)])
    return list(dict.fromkeys(cands))


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
    raw = (code or "").strip().lower()
    if len(raw) == PREFIXED_CODE_LENGTH and raw[:2] in EXCHANGE_PREFIXES and raw[2:].isdigit():
        from src.services.fund_registry import INDEX_CODES

        if raw in INDEX_CODES:
            return raw
    return bare_code(raw)
