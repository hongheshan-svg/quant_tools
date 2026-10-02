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
import math
from datetime import datetime, timedelta

import akshare as ak
import pandas as pd
from loguru import logger

from src.collectors.source_chain import fetch_with_fallback
from src.database.db import get_db_session
from src.database.models import StockDaily
from src.utils.stock_code import bare_code, code_candidates, normalize_name, prefixed_code

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
        if not math.isfinite(num):
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


def _fetch_tickflow(code: str, start_date: str, end_date: str):
    from src.collectors.paid_market import fetch_daily_tickflow
    return fetch_daily_tickflow(code, start_date, end_date)


# 配置名 → (返回的来源标记, 取数函数)；来源标记决定 records_from_daily_df 的解析方式
DAILY_SOURCES = {
    "tencent": ("tx", _fetch_tx),
    "sina": ("daily", _fetch_sina),
    "eastmoney": ("em", _fetch_em),
    "baostock": ("baostock", _extra("baostock")),
    "pytdx": ("pytdx", _extra("pytdx")),
    "efinance": ("efinance", _extra("efinance")),
    "tushare": ("tushare", _extra("tushare")),
    "tickflow": ("tickflow", _fetch_tickflow),
}


def daily_source_order() -> list[str]:
    from src.config_loader import load_config

    from src.services.data_source_settings import source_order
    return source_order(load_config(), "daily_history")


def fetch_daily_df_with_fallback(code: str, start_date: str, end_date: str, order: list[str] | None = None):
    """按顺序尝试各数据源，返回 (来源标记, DataFrame)；全部失败时抛 RuntimeError（含各源的错误）"""
    from src.config_loader import load_config
    from src.services.data_source_settings import source_configured

    config = load_config()
    from src.collectors.request_budget import configure_policy
    configure_policy(config)

    def validated(label, fetch):
        frame = fetch(code, start_date, end_date)
        if frame is None or frame.empty:
            return None
        frame = normalize_daily_date_column(frame)
        if frame is None:
            raise ValueError("日线缺少日期列")
        frame["date"] = pd.to_datetime(frame["date"], format="%Y-%m-%d", errors="coerce").dt.strftime("%Y-%m-%d")
        frame = frame.dropna(subset=["date"]).drop_duplicates("date", keep="last")
        records = records_from_daily_df(code, "", label, frame)
        valid_days = {r["trade_date"] for r in records if start_date <= r["trade_date"] <= end_date and
                      all(r[k] > 0 and math.isfinite(r[k]) for k in ("open", "close", "high", "low")) and
                      r["low"] <= min(r["open"], r["close"]) <= max(r["open"], r["close"]) <= r["high"]}
        if not valid_days:
            raise ValueError("日线日期或价格无效")
        return frame.loc[frame["date"].isin(valid_days)].copy()

    sources = [(DAILY_SOURCES[n][0], lambda n=n: validated(*DAILY_SOURCES[n]))
               for n in dict.fromkeys(order or daily_source_order()) if n in DAILY_SOURCES and source_configured(config, n)]
    result = fetch_with_fallback("个股日线", sources, cache_key=f"{code}:{start_date}:{end_date}", count_empty_failures=False)
    if result.ok:
        return result.source, result.data
    raise RuntimeError(" | ".join(f"{name}: {error}" for name, error in result.errors.items()) or "没有可用的已配置数据源")


def normalize_volume_unit(volume: float, amount: float, close: float) -> float:
    """按「成交额/(收盘价×成交量)」判断成交量单位并统一为股。

    比值应接近 1：约 0.01 说明成交量被多乘了 100，除回去；约 100 说明还是「手」，乘 100；其他原样返回。
    """
    if not (volume > 0 and amount > 0 and close > 0):
        return volume
    ratio = amount / (close * volume)
    if 0.005 <= ratio <= 0.02:
        return volume / 100
    if 50 <= ratio <= 200:
        return volume * 100
    return volume


def records_from_daily_df(code: str, name: str, source: str, df) -> list[dict]:
    name = normalize_name(name)
    records: list[dict] = []
    adjustment = df.attrs.get("price_adjustment") or ("none" if source in {"tushare", "pytdx"} else "forward")

    def tagged(items: list[dict]) -> list[dict]:
        return [{**item, "source": source, "price_adjustment": adjustment} for item in items]

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
        return tagged([r for r in records if r["trade_date"]])

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
            if source == "tx":
                # 腾讯日线的成交量单位随 akshare 版本和板块（688/689）不一致，按成交额校验
                volume = normalize_volume_unit(volume, amount, close_val)
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

    return tagged(records)


# ---------- 按需补齐 ----------

_failed_at: dict[str, datetime] = {}
_failed_lock = threading.Lock()


def _history_end_date(now: datetime) -> str:
    """收盘数据 15:30 后才完整，之前只补到前一天（当天的行由实时行情采集写入）。"""
    day = now if now.hour * 100 + now.minute >= 1530 else now - timedelta(days=1)
    return day.strftime("%Y-%m-%d")


def ensure_daily_history(code: str, db_path: str, name: str = "", min_bars: int = ENSURE_MIN_BARS,
                         now: datetime | None = None, refresh: bool = False) -> int:
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
    if len(existing) >= min_bars and not refresh:
        return 0
    with _failed_lock:
        failed = _failed_at.get(bare)
        if not refresh and failed and now - failed < timedelta(minutes=ENSURE_RETRY_MINUTES):
            return 0

    fetch_start = (datetime.strptime(start, "%Y-%m-%d") - timedelta(days=CHANGE_LOOKBACK_DAYS)).strftime("%Y-%m-%d")
    try:
        source, df = fetch_daily_df_with_fallback(bare, fetch_start, _history_end_date(now))
    except Exception as e:
        with _failed_lock:
            _failed_at[bare] = now
        logger.warning(f"补齐日线失败 [{bare}]: {e}")
        if refresh:
            raise RuntimeError("刷新复权历史失败，已保留原有日线") from e
        return 0

    records = [r for r in records_from_daily_df(bare, name, source, df) if r["trade_date"] >= start]
    if refresh and not records:
        raise RuntimeError("数据源没有返回有效日线，已保留原有日线")
    if records:
        import hashlib
        import json
        from src.database.models import PriceRevision
        revision = hashlib.sha256(json.dumps(records, sort_keys=True, default=str).encode()).hexdigest()
        changed = 0
        with get_db_session(db_path) as session:
            from src.strategy.data_quality import row_priority
            current = {}
            for row in session.query(StockDaily).filter(StockDaily.code.in_(code_candidates(bare)), StockDaily.trade_date >= start):
                if row.trade_date not in current or row_priority(row) > row_priority(current[row.trade_date]):
                    current[row.trade_date] = row
            for item in records:
                row = current.get(item["trade_date"])
                if row is not None and not refresh:
                    continue
                if row is None:
                    row = StockDaily(**item)
                    session.add(row)
                    changed += 1
                else:
                    fields = ("open", "high", "low", "close", "volume", "amount", "change_pct", "price_adjustment", "source")
                    before = {key: getattr(row, key) for key in fields}
                    if any(before[key] != item.get(key) for key in fields):
                        session.add(PriceRevision(code=bare, trade_date=row.trade_date,
                                                  previous_json=json.dumps(before, default=str), revision=revision))
                        for key in fields:
                            setattr(row, key, item.get(key))
                        changed += 1
                row.price_revision, row.updated_at = revision, now
        return changed
    return 0


# ---------- 科创板成交量修复 ----------

_STAR_RATIO_RANGE = (0.005, 0.02)
_STAR_MAX_CODES = 50


def _is_star_code(code: str | None) -> bool:
    """688/689 开头（含 sh 前缀、.SH 后缀的写法）"""
    raw = (code or "").strip().lower().split(".")[0]
    return bare_code(raw).startswith(("688", "689"))


def repair_star_volume(db_path: str, apply: bool = False) -> dict:
    """修复 stock_daily 里被放大 100 倍的科创板（688/689）成交量。

    判断依据：成交额/(收盘价×成交量) 落在 [0.005, 0.02]。apply 为假时只统计不写入。
    """
    checked = matched = fixed = 0
    codes: list[str] = []
    with get_db_session(db_path) as session:
        rows = session.query(StockDaily).filter(
            (StockDaily.code.like("%688%")) | (StockDaily.code.like("%689%"))
        ).all()
        for row in rows:
            if not _is_star_code(row.code):
                continue
            volume, amount, close = row.volume or 0, row.amount or 0, row.close or 0
            if not (volume > 0 and amount > 0 and close > 0):
                continue
            checked += 1
            ratio = amount / (close * volume)
            if not (_STAR_RATIO_RANGE[0] <= ratio <= _STAR_RATIO_RANGE[1]):
                continue
            matched += 1
            code = bare_code(str(row.code).lower().split(".")[0])
            if code not in codes and len(codes) < _STAR_MAX_CODES:
                codes.append(code)
            if apply:
                row.volume = volume / 100
                fixed += 1
    return {"checked": checked, "matched": matched, "fixed": fixed, "codes": codes}
