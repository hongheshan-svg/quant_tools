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


def overseas_identity(raw: str) -> StockIdentity | None:
    """海外身份显式后缀优先；裸六位仍保留为 A 股，避免串市场。"""
    value = raw.strip().upper()
    aliases = {"HK": ("HK", "HKD"), "T": ("JP", "JPY"), "KS": ("KR", "KRW"),
               "KQ": ("KR", "KRW"), "TW": ("TW", "TWD"), "TWO": ("TW", "TWD")}
    match = re.fullmatch(r"HK[.:]?(\d{1,5})", value)
    if match:
        value = match[1].zfill(5) + ".HK"
    for prefix, suffix in (("JP:", ".T"), ("KR:", ".KS"), ("TW:", ".TW")):
        if value.startswith(prefix):
            value = value[len(prefix):] + suffix
    match = re.fullmatch(r"(\d{1,6})\.(HK|T|KS|KQ|TW|TWO)", value)
    if match:
        digits, exchange = match.groups()
        widths = {"HK": (1, 5), "T": (4, 4), "KS": (6, 6), "KQ": (6, 6), "TW": (4, 6), "TWO": (4, 6)}
        if not widths[exchange][0] <= len(digits) <= widths[exchange][1]:
            raise StockCodeError("海外证券代码长度不符")
        if exchange == "HK":
            digits = digits.zfill(5)
        market, currency = aliases[exchange]
        return StockIdentity(digits + "." + exchange, exchange, "stock", market, currency)
    if value.startswith(("US:", "US.")):
        value = value[3:]
    if value.endswith(".US"):
        value = value[:-3]
    if re.fullmatch(r"[A-Z]{1,5}(?:[.\-][A-Z])?", value):
        return StockIdentity(value.replace(".", "-"), "US", "stock", "US", "USD")
    return None


# 自由文本里形似美股代码、其实是术语的大写缩写（指标、估值、宏观、交易所片段、常见英文词）。
# 只用于从问句或模型参数里识别「裸」代码；显式写成 US:MA、MA.US 的仍按美股处理。
TICKER_JARGON = frozenset({
    # 交易所、市场与板块片段
    "SH", "SZ", "BJ", "HK", "SS", "US", "CN", "AH", "ST", "SST", "PT", "IPO", "ETF", "LOF", "FOF", "QDII",
    "QFII", "REIT", "REITS", "KCB", "CYB",
    # 技术指标
    "MA", "EMA", "SMA", "EXPMA", "BBI", "MACD", "DIF", "DEA", "KDJ", "RSI", "BOLL", "OBV", "DMI", "ADX", "CCI",
    "WR", "BIAS", "VOL", "VWAP", "ATR", "SAR", "TRIX", "ASI", "VR", "PSY", "ROC", "MTM", "CR", "BRAR", "DMA",
    # 估值与财务
    "PE", "PB", "PS", "PEG", "PCF", "ROE", "ROA", "ROI", "ROIC", "EPS", "BPS", "TTM", "LTM", "NTM", "FWD", "YOY",
    "QOQ", "MOM", "YTD", "EBIT", "EBITDA", "DCF", "CAGR", "FCF", "NAV", "AUM", "GMV", "ARPU", "CAPEX", "OPEX",
    # 宏观与政策
    "CPI", "PPI", "GDP", "PMI", "LPR", "MLF", "SLF", "SLO", "RRR", "OMO", "FOMC", "FED", "QE", "QT", "USD", "CNY",
    "CNH", "HKD", "EUR", "GBP", "JPY",
    # 交易用语与常见英文
    "BUY", "SELL", "HOLD", "LONG", "SHORT", "CALL", "PUT", "STOP", "LOSS", "RISK", "CASH", "BOND", "FUND",
    "STOCK", "INDEX", "PRICE", "TRADE", "TREND", "BULL", "BEAR", "HIGH", "LOW", "OPEN", "CLOSE", "AI", "IT",
    "OK", "VS", "PK", "CEO", "CFO", "AND", "OR", "THE", "FOR", "NOT", "YES", "NO", "IS", "OF", "TO", "IN",
    "ON", "AT", "BY", "IF", "HI", "HEY", "WHAT", "WHY", "HOW", "WHO", "NEW", "NOW",
})
# 缩写后面紧跟这些字时是术语：A股、AH股、K线、V型、M头、W底、N字
_JARGON_SUFFIX = ("股", "线", "型", "头", "底", "字", "形", "值")


def is_ticker_jargon(token: str, following: str = "") -> bool:
    """问句或模型参数里的裸大写缩写是否应视为术语而不是美股代码。

    单个字母（A股、K线、T+0）、TICKER_JARGON 里的缩写，以及紧跟「股/线/型」等字的缩写都算术语；
    带 US:、.US 或其他市场后缀的显式代码不调用这个判断。
    """
    value = str(token or "").strip().upper()
    if not value or not re.fullmatch(r"[A-Z]{1,5}(?:[.\-][A-Z])?", value):
        return False
    return len(value) == 1 or value in TICKER_JARGON or str(following or "").startswith(_JARGON_SUFFIX)


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
    foreign = overseas_identity(raw)
    if foreign:
        return foreign
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
    foreign = overseas_identity(raw)
    if foreign:
        return foreign.code
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
        foreign = overseas_identity(raw)
        return list(dict.fromkeys([foreign.code, raw])) if foreign else [raw]
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
