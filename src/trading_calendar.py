"""
A股交易日历
数据来源：新浪交易日历（AKShare tool_trade_date_hist_sina），缓存在数据库 trade_calendar 表。

用法：先调用 load() 把日历读进内存（可按需联网刷新），之后 is_trade_day() / next_trade_day()
只查内存，可以高频调用。日历缺失或日期超出日历范围时退化为「周一至周五」规则，
保证数据源故障时调度不会完全停摆。
"""

from __future__ import annotations

import threading
from datetime import date, datetime, timedelta

from loguru import logger
from sqlalchemy import func

from src.database.db import get_db_session
from src.database.models import TradeCalendar

WEEKEND_START = 5
REFRESH_INTERVAL_DAYS = 7        # 缓存超过 7 天重新拉取
MIN_COVERAGE_DAYS = 30           # 缓存至少要覆盖到今天之后 30 天（跨年时等待新一年日历）
REFRESH_RETRY_SECONDS = 6 * 3600  # 联网失败或新一年日历未发布时，同一进程 6 小时内不重复尝试
MAX_LOOKAHEAD_DAYS = 30
MARKET_DATA_READY_HHMM = (9, 25)  # 集合竞价结束后当天的行情和涨停池才是今天的数据
TRADE_SESSIONS = ((925, 1130), (1300, 1500))  # 含集合竞价结果公布后的 9:25

_lock = threading.Lock()
_trade_days: set[str] = set()
_first_day = ""
_last_day = ""
_last_refresh_attempt: datetime | None = None


def _to_date(value: date | datetime | str | None) -> date:
    if value is None:
        return date.today()
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()


def _set_days(days: set[str]) -> None:
    global _trade_days, _first_day, _last_day
    _trade_days = days
    _first_day = min(days) if days else ""
    _last_day = max(days) if days else ""


def _fetch_remote() -> set[str]:
    import akshare as ak

    df = ak.tool_trade_date_hist_sina()
    return {_to_date(v).strftime("%Y-%m-%d") for v in df["trade_date"].tolist()}


def _is_stale(session) -> bool:
    last_update, last_day = session.query(func.max(TradeCalendar.updated_at), func.max(TradeCalendar.trade_date)).one()
    if not last_update or not last_day:
        return True
    if datetime.now() - last_update > timedelta(days=REFRESH_INTERVAL_DAYS):
        return True
    return last_day < (date.today() + timedelta(days=MIN_COVERAGE_DAYS)).strftime("%Y-%m-%d")


def _save(db_path: str, days: set[str]) -> None:
    now = datetime.now()
    with get_db_session(db_path) as session:
        session.query(TradeCalendar).delete()
        session.bulk_save_objects([TradeCalendar(trade_date=d, updated_at=now) for d in sorted(days)])


def load(db_path: str = "data/quant.db", refresh: bool = True) -> bool:
    """把交易日历读进内存；refresh=True 时缓存过期或覆盖不足会联网更新。返回是否有可用日历。"""
    global _last_refresh_attempt
    with _lock:
        try:
            with get_db_session(db_path) as session:
                cached = {row[0] for row in session.query(TradeCalendar.trade_date).all()}
                stale = _is_stale(session)
        except Exception as e:
            logger.warning(f"读取交易日历缓存失败: {e}")
            cached, stale = set(), True

        throttled = _last_refresh_attempt and datetime.now() - _last_refresh_attempt < timedelta(seconds=REFRESH_RETRY_SECONDS)
        if refresh and stale and not throttled:
            _last_refresh_attempt = datetime.now()
            try:
                remote = _fetch_remote()
                if remote:
                    _save(db_path, remote)
                    cached = remote
                    logger.info(f"交易日历已更新: {min(remote)} ~ {max(remote)}，共 {len(remote)} 个交易日")
            except Exception as e:
                logger.warning(f"交易日历联网更新失败，{'沿用缓存' if cached else '按周一至周五判断'}: {e}")

        if cached:
            _set_days(cached)
        return bool(_trade_days)


def is_trade_day(d: date | datetime | str | None = None) -> bool:
    """判断是否为A股交易日（只查内存，不做 IO）。"""
    day = _to_date(d)
    key = day.strftime("%Y-%m-%d")
    if _trade_days and _first_day <= key <= _last_day:
        return key in _trade_days
    return day.weekday() < WEEKEND_START


def has_calendar_coverage(start: str, end: str) -> bool:
    """区间是否由正式交易日历覆盖；周一至周五降级规则不用于策略自动调权。"""
    with _lock:
        return bool(_trade_days and _first_day <= start <= end <= _last_day)


def market_data_ready(now: datetime | None = None) -> bool:
    """今天是否已有当日行情：交易日且已过 9:25。
    节假日或开盘前，行情接口返回的是上一个交易日的数据，不能按今天的日期入库。"""
    now = now or datetime.now()
    return is_trade_day(now) and (now.hour, now.minute) >= MARKET_DATA_READY_HHMM


def in_trade_session(now: datetime | None = None) -> bool:
    """当前是否处于交易时段（交易日 9:25-11:30、13:00-15:00）。"""
    now = now or datetime.now()
    hhmm = now.hour * 100 + now.minute
    return is_trade_day(now) and any(start <= hhmm <= end for start, end in TRADE_SESSIONS)


def trade_days_only(dates):
    """从日期列表中去掉非交易日（兼容此前在节假日按当天日期写入的重复数据）。"""
    return [d for d in dates if is_trade_day(d)]


def prev_trade_day(d: date | datetime | str | None = None) -> date:
    """d 之前（不含 d）的最近一个交易日。"""
    day = _to_date(d)
    for _ in range(MAX_LOOKAHEAD_DAYS):
        day -= timedelta(days=1)
        if is_trade_day(day):
            return day
    return day


def next_trade_day(d: date | datetime | str | None = None, include_self: bool = False) -> date:
    """d 之后（include_self=True 时含 d 当天）的第一个交易日。"""
    day = _to_date(d)
    if include_self and is_trade_day(day):
        return day
    for _ in range(MAX_LOOKAHEAD_DAYS):
        day += timedelta(days=1)
        if is_trade_day(day):
            return day
    return day
