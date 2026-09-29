"""
ETF 与指数日线：腾讯 fqkline 接口（普通 HTTP，无需浏览器），写入 fund_daily（不写 stock_daily）。

腾讯 K 线每行 [日期, 开, 收, 高, 低, 成交量(手), ...]，ETF 在 qfqday、指数在 day 键下；成交量统一换算成份/股。
接口没有成交额：ETF 按成交量乘当日均价估算，指数记 0（指数成交量乘点位没有意义）。
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta

import httpx
from loguru import logger

from src.collectors.source_chain import source_health
from src.database.db import get_db_session
from src.database.models import FundDaily
from src.utils.stock_code import EXCHANGE_PREFIXES, exchange_of

KLINE_URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
LOTS_TO_SHARES = 100          # 腾讯成交量单位是手
ENSURE_CALENDAR_DAYS = 150
ENSURE_RETRY_MINUTES = 30
TOPUP_DAYS = 30

_failed_at: dict[str, datetime] = {}
_topup_at: dict[str, datetime] = {}
_lock = threading.Lock()


def tx_symbol(code: str) -> str:
    """腾讯代码：指数用规范代码本身（sh000300），ETF 的 6 位代码按交易所补前缀。"""
    raw = (code or "").strip().lower()
    if len(raw) == 8 and raw[:2] in EXCHANGE_PREFIXES and raw[2:].isdigit():
        return raw
    if len(raw) == 6 and raw.isdigit():
        return f"{exchange_of(raw)}{raw}"
    raise ValueError(f"不是有效的 ETF/指数代码: {code}")


def is_index(code: str) -> bool:
    raw = (code or "").strip().lower()
    return len(raw) == 8 and raw[:2] in EXCHANGE_PREFIXES


def _to_float(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def parse_kline(code: str, payload: dict, days: int) -> list[dict]:
    """解析腾讯 fqkline 的响应，返回按日期升序的日线（volume 为份/股，amount 为元，change_pct 为 %）。"""
    symbol = tx_symbol(code)
    data = (payload.get("data") or {}).get(symbol)
    if not isinstance(data, dict):
        raise ValueError(f"腾讯 K 线没有返回 {symbol}")
    rows = data.get("qfqday") or data.get("day") or []
    if not rows:
        raise ValueError(f"腾讯 K 线 {symbol} 为空")
    name = ""
    quote = (data.get("qt") or {}).get(symbol)
    if isinstance(quote, list) and len(quote) > 1:
        name = str(quote[1] or "").strip()
    index = is_index(code)
    bars: list[dict] = []
    prev_close = 0.0
    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) < 6:
            continue
        close = _to_float(row[2])
        if close <= 0:
            continue
        open_, high, low = _to_float(row[1]), _to_float(row[3]), _to_float(row[4])
        volume = _to_float(row[5]) * LOTS_TO_SHARES
        amount = 0.0 if index else volume * (open_ + high + low + close) / 4
        bars.append({
            "code": code, "name": name, "trade_date": str(row[0])[:10],
            "open": open_, "high": high, "low": low, "close": close,
            "volume": volume, "amount": amount,
            "change_pct": (close - prev_close) / prev_close * 100 if prev_close else 0.0,
        })
        prev_close = close
    if len(bars) > days:  # 多取的第一根只用来算涨跌幅
        bars = bars[-days:]
    return bars


def fetch_fund_daily(code: str, days: int = 250) -> list[dict]:
    """下载 ETF/指数最近 days 根日线（按日期升序）；网络或解析失败抛异常。"""
    symbol = tx_symbol(code)
    begin = time.monotonic()
    try:
        resp = httpx.get(KLINE_URL, params={"param": f"{symbol},day,,,{days + 1},qfq"}, timeout=15,
                         headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        bars = parse_kline(code, resp.json(), days)
    except Exception as e:
        source_health.record("基金日线", "腾讯", False, str(e)[:200], time.monotonic() - begin)
        raise
    source_health.record("基金日线", "腾讯", True, elapsed=time.monotonic() - begin)
    return bars


def _save_bars(code: str, name: str, bars: list[dict], db_path: str, since: str = "") -> int:
    """写入 fund_daily 中缺失的日期，返回新增条数。"""
    with get_db_session(db_path) as session:
        existing = {d for (d,) in session.query(FundDaily.trade_date).filter(FundDaily.code == code).all()}
        fresh = [b for b in bars if b["trade_date"] not in existing and b["trade_date"] >= since]
        if fresh:
            session.add_all(FundDaily(**{**b, "code": code, "name": b.get("name") or name}) for b in fresh)
    return len(fresh)


def _known_name(code: str, db_path: str) -> str:
    from src.database.models import FundInfo
    from src.services.fund_registry import INDEX_CODES

    if code in INDEX_CODES:
        return INDEX_CODES[code]["name"]
    with get_db_session(db_path) as session:
        return session.query(FundInfo.name).filter(FundInfo.code == code).limit(1).scalar() or ""


def ensure_fund_daily(code: str, db_path: str, min_bars: int = 60, now: datetime | None = None) -> int:
    """本地近 150 天 fund_daily 不足 min_bars 根时联网下载，只写入缺失的日期；返回新增条数。

    下载失败的代码 30 分钟内不再重试，避免界面反复打开同一只基金时一直卡在网络请求上。
    """
    now = now or datetime.now()
    since = (now - timedelta(days=ENSURE_CALENDAR_DAYS)).strftime("%Y-%m-%d")
    with get_db_session(db_path) as session:
        have = session.query(FundDaily.id).filter(FundDaily.code == code, FundDaily.trade_date >= since).count()
    if have >= min_bars:
        return 0
    with _lock:
        failed = _failed_at.get(f"{db_path}|{code}")
        if failed and now - failed < timedelta(minutes=ENSURE_RETRY_MINUTES):
            return 0
    try:
        bars = fetch_fund_daily(code)
    except Exception as e:
        with _lock:
            _failed_at[f"{db_path}|{code}"] = now
        logger.warning(f"补齐 ETF/指数日线失败 [{code}]: {e}")
        return 0
    added = _save_bars(code, _known_name(code, db_path), bars, db_path, since)
    if added:
        logger.info(f"已补齐 {code} 日线 {added} 根")
    return added


def _expected_latest_date(now: datetime) -> str:
    """当前应有的最新收盘日：15:30 后为今天（交易日），否则往前找上一个交易日。"""
    from src import trading_calendar

    day = now.date() if now.hour * 100 + now.minute >= 1530 else now.date() - timedelta(days=1)
    for _ in range(15):
        if trading_calendar.is_trade_day(day):
            break
        day -= timedelta(days=1)
    return day.strftime("%Y-%m-%d")


def refresh_recent_fund_daily(code: str, db_path: str, now: datetime | None = None) -> int:
    """本地最新日线落后于应有的最新收盘日时，联网补最近 30 根（同一代码 30 分钟内最多一次）。

    ETF/指数没有日常采集任务，只靠这个函数在查看、诊断时保持日线新鲜。返回新增条数。
    """
    from sqlalchemy import func

    now = now or datetime.now()
    with get_db_session(db_path) as session:
        latest = session.query(func.max(FundDaily.trade_date)).filter(FundDaily.code == code).scalar()
    if not latest or latest >= _expected_latest_date(now):
        return 0
    with _lock:
        tried = _topup_at.get(f"{db_path}|{code}")
        if tried and now - tried < timedelta(minutes=ENSURE_RETRY_MINUTES):
            return 0
        _topup_at[f"{db_path}|{code}"] = now
    try:
        bars = fetch_fund_daily(code, TOPUP_DAYS)
    except Exception as e:
        logger.warning(f"更新 ETF/指数日线失败 [{code}]: {e}")
        return 0
    return _save_bars(code, _known_name(code, db_path), bars, db_path, latest)


def get_fund_daily(code: str, db_path: str, limit: int | None = None) -> list[dict]:
    """读取 fund_daily 日线（按日期升序），字段与个股日线接口一致（没有的字段为 None）。"""
    with get_db_session(db_path) as session:
        rows = session.query(FundDaily).filter(FundDaily.code == code).order_by(FundDaily.trade_date.asc()).all()
        result = [{
            "code": code, "name": r.name or "", "trade_date": r.trade_date, "open": r.open, "high": r.high, "low": r.low,
            "close": r.close, "change_pct": r.change_pct, "volume": r.volume, "amount": r.amount,
            "turnover": None, "total_mv": None, "circ_mv": None,
        } for r in rows]
    return result[-limit:] if limit and limit > 0 else result
