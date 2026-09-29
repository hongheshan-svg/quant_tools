"""
个股日线历史下载与解析，供 scripts/fetch_history.py 批量回补和按需补齐共用。

默认顺序：腾讯 → 新浪 → 东方财富 → baostock → 通达信 → efinance → Tushare（后四个见 extra_sources.py），
由 data_sources.daily_history 调整；前一个失败或没有数据才用下一个。

ensure_daily_history(code)：本地近期日线不足时联网下载并补齐缺失的交易日（不覆盖已有行），
用于个股详情 K 线、AI 诊断、问股等只关心单只股票的场景。
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta

import akshare as ak
from loguru import logger

from src.collectors.source_chain import source_health
from src.database.db import get_db_session
from src.database.models import StockDaily
from src.utils.stock_code import bare_code, code_candidates, prefixed_code

CHANGE_LOOKBACK_DAYS = 10   # 多取几天，区间第一天的涨跌幅也按前收计算
ENSURE_MIN_BARS = 60        # 按需补齐：本地近期日线少于该数量时联网下载
ENSURE_CALENDAR_DAYS = 150  # 按需补齐的时间跨度（约 100 个交易日）
ENSURE_RETRY_MINUTES = 30   # 同一只股票补齐失败后 30 分钟内不再重试

RETRYABLE_ERROR_KEYWORDS = (
    "connection aborted",
    "remote end closed connection",
    "read timed out",
    "timed out",
    "connection reset",
    "temporarily unavailable",
    "max retries exceeded",
    "proxyerror",
    "ssl",
)


def safe_float(value, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        num = float(value)
        if num != num:  # NaN
            return default
        return num
    except Exception:
        return default


def pick(row, keys: list[str], default=None):
    for key in keys:
        if key in row.index:
            value = row.get(key)
            if value is not None and str(value).strip() != "":
                return value
    return default


def is_retryable_exception(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(k in msg for k in RETRYABLE_ERROR_KEYWORDS)


def call_with_retry(fn, attempts: int = 3, base_sleep: float = 0.6):
    last_exc = None
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except Exception as exc:
            last_exc = exc
            if attempt >= attempts or not is_retryable_exception(exc):
                raise
            time.sleep(base_sleep * attempt)
    raise RuntimeError(f"retry failed: {last_exc}")


def normalize_daily_date_column(df):
    if df is None or df.empty:
        return df

    normalized = df.copy()
    date_candidates = ["date", "日期", "trade_date", "交易日期", "datetime", "时间", "Date", "DATE"]
    date_col = next((c for c in date_candidates if c in normalized.columns), None)

    if date_col is None:
        normalized = normalized.reset_index()
        date_col = next((c for c in date_candidates if c in normalized.columns), None)
        if date_col is None and "index" in normalized.columns:
            date_col = "index"

    if date_col is None:
        return None

    normalized["date"] = normalized[date_col].astype(str).str[:10]
    return normalized


def to_ak_symbol(code: str) -> str:
    """AKShare 新浪/腾讯接口的代码格式，如 600519 → sh600519。"""
    return prefixed_code(code)


def _fetch_tx(code: str, start_date: str, end_date: str):
    return call_with_retry(
        lambda: ak.stock_zh_a_hist_tx(symbol=to_ak_symbol(code), start_date=start_date.replace("-", ""),
                                      end_date=end_date.replace("-", ""), adjust="qfq"),
        attempts=2,
    )


def _fetch_sina(code: str, start_date: str, end_date: str):
    df = call_with_retry(lambda: ak.stock_zh_a_daily(symbol=to_ak_symbol(code), adjust="qfq"), attempts=2)
    if df is None or df.empty:
        return None
    df = normalize_daily_date_column(df)
    if df is None:
        raise ValueError("missing date column")
    ds = df["date"].astype(str).str[:10]
    return df.loc[(ds >= start_date) & (ds <= end_date)].copy()


def _fetch_em(code: str, start_date: str, end_date: str):
    return call_with_retry(
        lambda: ak.stock_zh_a_hist(symbol=code, period="daily", start_date=start_date.replace("-", ""),
                                   end_date=end_date.replace("-", ""), adjust="qfq"),
        attempts=3,
    )


def _extra(name: str):
    def fetch(code: str, start_date: str, end_date: str):
        from src.collectors import extra_sources

        return getattr(extra_sources, f"fetch_daily_{name}")(code, start_date, end_date)
    return fetch


# 配置名 → (返回的来源标记, 取数函数)；来源标记决定 records_from_daily_df 的解析方式
DAILY_SOURCES = {
    "tencent": ("tx", _fetch_tx),
    "sina": ("daily", _fetch_sina),
    "eastmoney": ("em", _fetch_em),
    "baostock": ("baostock", _extra("baostock")),
    "pytdx": ("pytdx", _extra("pytdx")),
    "efinance": ("efinance", _extra("efinance")),
    "tushare": ("tushare", _extra("tushare")),
}


def daily_source_order() -> list[str]:
    from src.config_loader import load_config

    configured = (load_config().get("data_sources") or {}).get("daily_history") or []
    order = [name for name in configured if name in DAILY_SOURCES]
    return order or list(DAILY_SOURCES)


def fetch_daily_df_with_fallback(code: str, start_date: str, end_date: str, order: list[str] | None = None):
    """按顺序尝试各数据源，返回 (来源标记, DataFrame)；全部失败时抛 RuntimeError（含各源的错误）"""
    errors: list[str] = []
    for name in order or daily_source_order():
        label, fetch = DAILY_SOURCES[name]
        try:
            df = fetch(code, start_date, end_date)
        except Exception as e:
            errors.append(f"{label}: {e}")
            continue
        if df is not None and not df.empty:
            return label, df
        errors.append(f"{label} empty")
    raise RuntimeError(" | ".join(errors))


def records_from_daily_df(code: str, name: str, source: str, df) -> list[dict]:
    records: list[dict] = []

    if source == "em":
        for _, row in df.iterrows():
            records.append(
                {
                    "code": code,
                    "name": name,
                    "trade_date": str(pick(row, ["日期", "trade_date"], ""))[:10],
                    "open": safe_float(pick(row, ["开盘", "open"], 0)),
                    "close": safe_float(pick(row, ["收盘", "close"], 0)),
                    "high": safe_float(pick(row, ["最高", "high"], 0)),
                    "low": safe_float(pick(row, ["最低", "low"], 0)),
                    "volume": safe_float(pick(row, ["成交量", "volume"], 0)),
                    "amount": safe_float(pick(row, ["成交额", "amount"], 0)),
                    "change_pct": safe_float(pick(row, ["涨跌幅", "change_pct"], 0)),
                    "turnover": safe_float(pick(row, ["换手率", "turnover"], 0)),
                    "total_mv": safe_float(pick(row, ["总市值", "total_mv"], 0)),
                    "circ_mv": safe_float(pick(row, ["流通市值", "circ_mv"], 0)),
                }
            )
        return [r for r in records if r["trade_date"]]

    df = df.copy()
    if "date" not in df.columns:
        df = normalize_daily_date_column(df)
        if df is None:
            return records

    df["__trade_date"] = df["date"].astype(str).str[:10]
    df = df[df["__trade_date"] != ""].sort_values("__trade_date")

    prev_close = None
    for _, row in df.iterrows():
        trade_date = str(row.get("__trade_date", ""))[:10]
        if not trade_date:
            continue

        close_val = safe_float(row.get("close", 0))
        if prev_close and close_val:
            change_pct = (close_val - prev_close) / prev_close * 100
        else:
            change_pct = 0.0
        if close_val:
            prev_close = close_val

        if source == "tx" and "volume" not in df.columns:
            # 旧版 akshare 的腾讯日线只有 amount 列，实为成交量（手）
            tx_amount = safe_float(row.get("amount", 0))
            volume = tx_amount * 100 if tx_amount else 0.0
            amount = close_val * volume if (close_val and volume) else 0.0
            turnover = 0.0
        else:
            # 新版腾讯日线与新浪日线：volume=成交量（股），amount=成交额（元），turnover=换手率（小数）
            volume = safe_float(row.get("volume", 0))
            amount = safe_float(row.get("amount", 0))
            raw_turnover = safe_float(row.get("turnover", 0))
            turnover = raw_turnover * 100 if 0 < raw_turnover <= 1 else raw_turnover

        records.append(
            {
                "code": code,
                "name": name,
                "trade_date": trade_date,
                "open": safe_float(row.get("open", 0)),
                "close": close_val,
                "high": safe_float(row.get("high", 0)),
                "low": safe_float(row.get("low", 0)),
                "volume": volume,
                "amount": amount,
                "change_pct": change_pct,
                "turnover": turnover,
                "total_mv": 0.0,
                "circ_mv": 0.0,
            }
        )

    return records


# ---------- 按需补齐 ----------

_failed_at: dict[str, datetime] = {}
_failed_lock = threading.Lock()


def _history_end_date(now: datetime) -> str:
    """收盘数据 15:30 后才完整，之前只补到前一天（当天的行由实时行情采集写入）。"""
    day = now if now.hour * 100 + now.minute >= 1530 else now - timedelta(days=1)
    return day.strftime("%Y-%m-%d")


def ensure_daily_history(code: str, db_path: str, name: str = "", min_bars: int = ENSURE_MIN_BARS,
                         now: datetime | None = None) -> int:
    """本地近 150 天的日线少于 min_bars 根时联网下载，只写入缺失的交易日；返回新写入的行数。

    下载失败的股票 30 分钟内不再重试，避免界面反复打开同一只股票时一直卡在网络请求上。
    """
    bare = bare_code(code)
    if len(bare) != 6 or not bare.isdigit():
        return 0
    now = now or datetime.now()
    start = (now - timedelta(days=ENSURE_CALENDAR_DAYS)).strftime("%Y-%m-%d")
    with get_db_session(db_path) as session:
        existing = {
            d for (d,) in session.query(StockDaily.trade_date)
            .filter(StockDaily.code.in_(code_candidates(bare)), StockDaily.trade_date >= start).all()
        }
    if len(existing) >= min_bars:
        return 0
    with _failed_lock:
        failed = _failed_at.get(bare)
        if failed and now - failed < timedelta(minutes=ENSURE_RETRY_MINUTES):
            return 0

    fetch_start = (datetime.strptime(start, "%Y-%m-%d") - timedelta(days=CHANGE_LOOKBACK_DAYS)).strftime("%Y-%m-%d")
    begin = time.monotonic()
    try:
        source, df = fetch_daily_df_with_fallback(bare, fetch_start, _history_end_date(now))
    except Exception as e:
        source_health.record("个股日线", "腾讯/新浪/东方财富", False, str(e)[:200], time.monotonic() - begin)
        with _failed_lock:
            _failed_at[bare] = now
        logger.warning(f"补齐日线失败 [{bare}]: {e}")
        return 0
    source_health.record("个股日线", source, True, elapsed=time.monotonic() - begin)

    records = [r for r in records_from_daily_df(bare, name, source, df) if r["trade_date"] >= start and r["trade_date"] not in existing]
    if records:
        with get_db_session(db_path) as session:
            session.add_all(StockDaily(**r) for r in records)
        logger.info(f"已补齐 {bare} {name} 日线 {len(records)} 根（来源 {source}）")
    return len(records)
