"""
ETF 与指数注册表：内置主要 A 股指数，ETF 列表来自新浪（akshare fund_etf_category_sina，普通 HTTP），存入 fund_info。

身份约定：
- ETF 用 6 位代码（51/56/58/15/16 开头，与个股不冲突）
- 指数与个股的 6 位代码会冲突（000001 上证指数 vs 平安银行），指数的规范代码一律带交易所前缀
  （sh000300、sz399006、bj899050），任何地方都不能对指数代码调用 bare_code
- 裸 6 位数字只按 ETF 匹配，裸 000300 不当作指数
- 中证指数公司独有、没有交易所代码的指数（93xxxx 等）沿用 sh 前缀作为规范代码（与沪市股票、B 股代码段不冲突），
  别名带 .csi；日线来自中证指数官网，国证自由现金流（sz980092）腾讯只有 1 天历史，日线来自国证指数官网
ETF 与指数的行情存 fund_daily，不写入 stock_daily。
"""

from __future__ import annotations

import re
import threading
from datetime import datetime, timedelta
from typing import Any

from loguru import logger

from src.database.db import get_db_session
from src.database.models import FundInfo

ETF_REFRESH_DAYS = 7
ETF_PREFIXES = ("50", "51", "52", "56", "58", "15", "16")  # 场内基金代码段，与个股不冲突

# (规范代码, 名称, 别名, 日线来源)；来源省略时为腾讯 K 线，csindex / cnindex 为中证、国证指数官网
_INDEX_ROWS: tuple[tuple, ...] = (
    ("sh000001", "上证指数", ("上证综指", "上证综合指数", "shzs")),
    ("sz399001", "深证成指", ("深成指", "深证成份指数", "szcz")),
    ("sz399006", "创业板指", ("创业板指数", "创业板", "cybz")),
    ("sh000300", "沪深300", ("300指数", "沪深三百", "hs300", "csi300")),
    ("sh000016", "上证50", ("上证50指数", "sz50")),
    ("sh000905", "中证500", ("500指数", "zz500", "csi500")),
    ("sh000852", "中证1000", ("1000指数", "zz1000", "csi1000")),
    ("sh000688", "科创50", ("科创50指数", "科创板50", "kc50")),
    ("bj899050", "北证50", ("北证50指数", "bz50")),
    ("sz399673", "创业板50", ("创业板50指数", "cyb50")),
    ("sh000922", "中证红利", ("中证红利指数", "zzhl")),
    ("sz399303", "国证2000", ("国证2000指数", "gz2000")),
    ("sh000906", "中证800", ("800指数", "zz800")),
    ("sh000010", "上证180", ("上证180指数",)),
    ("sh000009", "上证380", ("上证380指数",)),
    ("sz399330", "深证100", ("深证100指数",)),
    ("sz399005", "中小100", ("中小板指", "中小100指数")),
    ("sh000015", "红利指数", ("上证红利", "上证红利指数")),
    ("sz399986", "中证银行", ("银行指数",)),
    ("sz399975", "证券公司", ("证券公司指数", "中证证券公司", "券商指数")),
    ("sz399989", "中证医疗", ("医疗指数",)),
    ("sz399997", "中证白酒", ("白酒指数",)),
    ("sz399967", "中证军工", ("军工指数",)),
    ("sz399324", "深证红利", ("深证红利指数",)),
    ("sh000941", "新能源", ("中证新能源", "新能源指数")),
    ("sz399296", "创成长", ("创业板成长", "创成长指数")),
    ("sz399365", "国证粮食", ("粮食指数",)),
    ("sz980092", "自由现金流", ("国证自由现金流", "980092.cni"), "cnindex"),
    ("sh932365", "中证现金流", ("中证全指自由现金流",), "csindex"),
    ("sh932366", "300现金流", ("沪深300自由现金流",), "csindex"),
    ("sh930955", "红利低波100", ("中证红利低波动100", "红利低波"), "csindex"),
    ("sh931446", "东证红利低波", ("中证东方红红利低波动",), "csindex"),
    ("sh931052", "国信价值", ("中证国信价值",), "csindex"),
    ("sh931643", "科创创业50", ("中证科创创业50", "双创50"), "csindex"),
    ("sh930606", "中证钢铁", ("钢铁指数",), "csindex"),
)


def _build_indexes() -> list[dict[str, Any]]:
    items = []
    for code, name, aliases, *rest in _INDEX_ROWS:
        source = rest[0] if rest else "tencent"
        bare = code[2:]
        suffix = "csi" if source == "csindex" else code[:2]
        items.append({"code": code, "name": name, "aliases": [*aliases, f"{bare}.{suffix}"], "source": source})
    return items


INDEXES: list[dict[str, Any]] = _build_indexes()
INDEX_CODES: dict[str, dict[str, Any]] = {i["code"]: i for i in INDEXES}
_INDEX_BY_NAME: dict[str, dict[str, Any]] = {}
for _item in INDEXES:
    for _key in (_item["name"], *_item["aliases"]):
        _INDEX_BY_NAME.setdefault(_key.lower(), _item)

_PREFIXED = re.compile(r"^(sh|sz|bj)[.\-]?(\d{6})$")
_SUFFIXED = re.compile(r"^(\d{6})\.(sh|sz|bj)$")
_BARE = re.compile(r"^\d{6}$")


def _parse_code(text: str) -> tuple[str, str] | None:
    """识别代码写法：sh000300 / sh.000300 / 000300.SH / 300123；返回（交易所前缀或空, 6 位数字）。"""
    raw = text.strip().lower()
    if m := _PREFIXED.match(raw):
        return m.group(1), m.group(2)
    if m := _SUFFIXED.match(raw):
        return m.group(2), m.group(1)
    if _BARE.match(raw):
        return "", raw
    return None


def _exchange_of_etf(code: str) -> str:
    return "sh" if code.startswith(("5", "6")) else "sz"


def _db_path() -> str:
    from src.config_loader import load_config

    return load_config().get("database", {}).get("sqlite_path", "data/quant.db")


# ---------- 刷新 ----------


def _fetch_etf_frame():
    import akshare as ak

    return ak.fund_etf_category_sina(symbol="ETF基金")


def refresh_etf_list(db_path: str, force: bool = False) -> int:
    """刷新 fund_info 中的 ETF 列表（超过 7 天才刷新）；失败保留旧数据返回 0，成功返回写入的 ETF 数。

    内置指数在 INDEXES 里，识别和搜索都直接读内存，不写 fund_info（fund_info 里只放 ETF）。
    """
    if not force:
        with get_db_session(db_path) as session:
            from sqlalchemy import func

            latest = session.query(func.max(FundInfo.updated_at)).filter(FundInfo.kind == "etf").scalar()
        if latest and datetime.now() - latest < timedelta(days=ETF_REFRESH_DAYS):
            return 0
    try:
        df = _fetch_etf_frame()
        items: dict[str, str] = {}
        for _, row in df.iterrows():
            parsed = _parse_code(str(row.get("代码", "")))
            name = str(row.get("名称", "") or "").strip()
            if parsed and name:
                items[parsed[1]] = name
    except Exception as e:
        logger.warning(f"刷新 ETF 列表失败，保留旧数据: {e}")
        return 0
    if not items:
        return 0
    now = datetime.now()
    with get_db_session(db_path) as session:
        existing = {r.code: r for r in session.query(FundInfo).filter(FundInfo.kind == "etf").all()}
        for code, name in items.items():
            row = existing.get(code)
            if row is None:
                session.add(FundInfo(code=code, name=name, kind="etf", exchange=_exchange_of_etf(code), updated_at=now))
            else:
                row.name, row.updated_at = name, now
    logger.info(f"ETF 列表已刷新：{len(items)} 只")
    return len(items)


def refresh_etf_list_background(db_path: str) -> threading.Thread:
    """后台线程刷新搜索用的股票列表（表为空或超过 1 天）和 ETF 列表（7 天内不重复联网）；
    有更新时重置股票搜索索引。由常驻服务启动时调用。

    股票列表来自交易所官方列表，节假日也能取到；否则新装的程序在节假日没有行情可采，
    按名称、拼音甚至代码都搜不到个股。
    """

    def run() -> None:
        refreshed = 0
        try:
            from src.collectors.stock_info import StockInfoCollector

            with StockInfoCollector() as collector:
                refreshed += collector.refresh_if_stale(db_path)
        except Exception as e:
            logger.debug(f"后台刷新股票列表失败: {e}")
        try:
            refreshed += refresh_etf_list(db_path)
        except Exception as e:
            logger.debug(f"后台刷新 ETF 列表失败: {e}")
        if refreshed:
            from src.services.stock_search import StockSearch

            StockSearch.reset()

    thread = threading.Thread(target=run, name="etf-refresh", daemon=True)
    thread.start()
    return thread


# ---------- 识别 ----------


def _etf_row(db_path: str, code: str) -> FundInfo | None:
    with get_db_session(db_path) as session:
        row = session.query(FundInfo).filter(FundInfo.code == code, FundInfo.kind == "etf").first()
        return FundInfo(code=row.code, name=row.name, kind=row.kind, exchange=row.exchange) if row else None


def resolve_fund(text: str, db_path: str) -> dict[str, Any] | None:
    """指数（带前缀代码、名称、别名）或 ETF（6 位代码、名称）→ {"kind", "code"（规范代码）, "name"}；识别不了返回 None。

    裸 6 位数字只按 ETF 匹配，不当作指数（避免与个股冲突）。
    """
    import unicodedata
    query = unicodedata.normalize("NFKC", text or "").strip()
    if not query:
        return None
    parsed = _parse_code(query)
    if parsed:
        prefix, digits = parsed
        if prefix:
            index = INDEX_CODES.get(f"{prefix}{digits}")
            if index:
                return {"kind": "index", "code": index["code"], "name": index["name"]}
            from src.utils.stock_code import resolve_identity

            resolve_identity(query)  # ETF 和个股也不能静默丢掉错误的交易所
        try:
            etf = _etf_row(db_path, digits)
        except Exception as e:
            logger.debug(f"查询 ETF 失败: {e}")
            return None
        return {"kind": "etf", "code": etf.code, "name": etf.name or ""} if etf else None
    index = _INDEX_BY_NAME.get(query.lower())
    if index:
        return {"kind": "index", "code": index["code"], "name": index["name"]}
    try:
        with get_db_session(db_path) as session:
            row = session.query(FundInfo).filter(FundInfo.kind == "etf", FundInfo.name == query).first()
            if row is None:
                row = session.query(FundInfo).filter(FundInfo.kind == "etf", FundInfo.name.ilike(query)).first()
            return {"kind": "etf", "code": row.code, "name": row.name or ""} if row else None
    except Exception as e:
        logger.debug(f"按名称查询 ETF 失败: {e}")
        return None


def is_fund_code(code: str, db_path: str | None = None) -> bool:
    """规范代码是否为已知指数（带前缀）或 ETF（6 位，在 fund_info 中）。"""
    raw = (code or "").strip().lower()
    if raw in INDEX_CODES:
        return True
    if not _BARE.match(raw) or not raw.startswith(ETF_PREFIXES):
        return False  # 个股代码不查库
    try:
        return _etf_row(db_path or _db_path(), raw) is not None
    except Exception:
        return False


def fund_kind(code: str, db_path: str | None = None) -> str:
    """规范代码的类型：index / etf / 空串（不是基金）。"""
    raw = (code or "").strip().lower()
    if raw in INDEX_CODES:
        return "index"
    return "etf" if is_fund_code(raw, db_path) else ""


def list_funds(db_path: str) -> list[dict[str, Any]]:
    """fund_info 中的全部 ETF，加上内置指数（供搜索索引使用）。"""
    funds = [{"kind": "index", "code": i["code"], "name": i["name"]} for i in INDEXES]
    try:
        with get_db_session(db_path) as session:
            funds += [{"kind": "etf", "code": c, "name": n} for c, n in
                      session.query(FundInfo.code, FundInfo.name).filter(FundInfo.kind == "etf").all() if n]
    except Exception as e:
        logger.debug(f"读取 ETF 列表失败: {e}")
    return funds
