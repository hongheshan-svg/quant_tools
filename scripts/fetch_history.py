from __future__ import annotations

import argparse
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from pathlib import Path
from threading import Lock

import akshare as ak
from loguru import logger
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.collectors.em_client import get_em_client  # noqa: E402
from src.database.models import (  # noqa: E402
    Base,
    DragonTigerBoard,
    LimitUpStock,
    StockDaily,
)

A_SHARE_OPEN_DATE = "1990-12-19"
DEFAULT_START = A_SHARE_OPEN_DATE
SLEEP_INTERVAL = 0.3
BATCH_SIZE = 500

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

def get_db_engine():
    db_path = ROOT / "data" / "quant.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
        execution_options={"isolation_level": "AUTOCOMMIT"},
    )
    Base.metadata.create_all(engine)
    return engine


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


def safe_int(value, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(value)
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
    if code.startswith("92"):
        return f"bj{code}"
    if code.startswith(("6", "9")):
        return f"sh{code}"
    if code.startswith(("4", "8")):
        return f"bj{code}"
    return f"sz{code}"


def get_trade_dates(start_date: str, end_date: str) -> list[str]:
    try:
        df = ak.tool_trade_date_hist_sina()
        col = df.columns[0]
        dates = df[col].astype(str).str[:10].tolist()
        return [d for d in dates if start_date <= d <= end_date]
    except Exception as e:
        logger.error(f"failed to get trade dates: {e}")
        return []


def get_latest_date_in_db(engine, table_cls, code_col: str | None, code: str | None) -> str | None:
    with Session(engine) as s:
        try:
            if code_col and code:
                row = s.execute(
                    text(f"SELECT MAX(trade_date) FROM {table_cls.__tablename__} WHERE {code_col}=:code"),
                    {"code": code},
                ).fetchone()
            else:
                row = s.execute(text(f"SELECT MAX(trade_date) FROM {table_cls.__tablename__}")).fetchone()
            return row[0] if row and row[0] else None
        except Exception:
            return None


def has_any_rows(engine, table_name: str) -> bool:
    """Check whether table has at least one row."""
    with Session(engine) as s:
        try:
            row = s.execute(text(f"SELECT 1 FROM {table_name} LIMIT 1")).fetchone()
            return row is not None
        except Exception:
            return False


def bulk_insert_ignore(engine, table_cls, records: list[dict]):
    if not records:
        return
    with engine.begin() as conn:
        for i in range(0, len(records), BATCH_SIZE):
            batch = records[i: i + BATCH_SIZE]
            conn.execute(
                text(
                    f"INSERT OR IGNORE INTO {table_cls.__tablename__} "
                    f"({', '.join(batch[0].keys())}) "
                    f"VALUES ({', '.join(':' + k for k in batch[0])})"
                ),
                batch,
            )


def fetch_stock_list() -> list[dict]:
    try:
        df = ak.stock_info_a_code_name()
        return [{"code": str(row["code"]), "name": str(row["name"])} for _, row in df.iterrows()]
    except Exception as e:
        logger.error(f"failed to get stock list: {e}")
        return []


def fetch_daily_df_with_fallback(code: str, start_date: str, end_date: str):
    start_compact = start_date.replace("-", "")
    end_compact = end_date.replace("-", "")
    symbol = to_ak_symbol(code)
    errors: list[str] = []

    try:
        df_tx = call_with_retry(
            lambda: ak.stock_zh_a_hist_tx(
                symbol=symbol,
                start_date=start_compact,
                end_date=end_compact,
                adjust="qfq",
            ),
            attempts=2,
        )
        if df_tx is not None and not df_tx.empty:
            return "tx", df_tx
        errors.append("tx empty")
    except Exception as e:
        errors.append(f"tx: {e}")

    try:
        df_daily = call_with_retry(
            lambda: ak.stock_zh_a_daily(symbol=symbol, adjust="qfq"),
            attempts=2,
        )
        if df_daily is not None and not df_daily.empty:
            df_daily = normalize_daily_date_column(df_daily)
            if df_daily is None:
                errors.append("daily: missing date column")
            else:
                ds = df_daily["date"].astype(str).str[:10]
                df_daily = df_daily.loc[(ds >= start_date) & (ds <= end_date)].copy()
                if not df_daily.empty:
                    return "daily", df_daily
                errors.append("daily empty")
        else:
            errors.append("daily empty")
    except Exception as e:
        errors.append(f"daily: {e}")

    try:
        df_em = call_with_retry(
            lambda: ak.stock_zh_a_hist(
                symbol=code,
                period="daily",
                start_date=start_compact,
                end_date=end_compact,
                adjust="qfq",
            ),
            attempts=3,
        )
        if df_em is not None and not df_em.empty:
            return "em", df_em
        errors.append("em empty")
    except Exception as e:
        errors.append(f"em: {e}")

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

        if source == "tx":
            tx_amount = safe_float(row.get("amount", 0))
            volume = tx_amount * 100 if tx_amount else 0.0
            amount = close_val * volume if (close_val and volume) else 0.0
            turnover = 0.0
        else:
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


def fetch_daily_ohlcv(engine, start_date: str, end_date: str, resume: bool = True, workers: int = 8):
    stocks = fetch_stock_list()
    if not stocks:
        logger.error("stock list empty, skip daily")
        return

    total = len(stocks)
    failed: list[str] = []
    failed_lock = Lock()
    counter = [0]
    counter_lock = Lock()

    def process_stock(stock: dict) -> None:
        code = stock["code"]
        name = stock["name"]

        with counter_lock:
            counter[0] += 1
            idx = counter[0]

        actual_start = start_date
        if resume:
            latest = get_latest_date_in_db(engine, StockDaily, "code", code)
            if latest and latest >= start_date:
                next_day = (datetime.strptime(latest, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
                if next_day > end_date:
                    logger.debug(f"[{idx}/{total}] {code} {name} already up-to-date")
                    return
                actual_start = next_day

        try:
            source, df = fetch_daily_df_with_fallback(code, actual_start, end_date)
            time.sleep(SLEEP_INTERVAL)

            if df is None or df.empty:
                logger.debug(f"[{idx}/{total}] {code} {name} no data")
                return

            records = records_from_daily_df(code, name, source, df)
            if not records:
                logger.debug(f"[{idx}/{total}] {code} {name} no valid records")
                return

            bulk_insert_ignore(engine, StockDaily, records)
            logger.info(f"[{idx}/{total}] {code} {name} inserted {len(records)} rows | source={source}")

        except Exception as e:
            logger.warning(f"[{idx}/{total}] {code} {name} failed: {e}")
            with failed_lock:
                failed.append(code)
            time.sleep(SLEEP_INTERVAL)

    logger.info(f"starting daily download with {workers} workers")
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(process_stock, s): s for s in stocks}
        for future in as_completed(futures):
            exc = future.exception()
            if exc:
                s = futures[future]
                logger.warning(f"unhandled exception for {s['code']}: {exc}")

    if failed:
        log_path = ROOT / "failed_stocks.log"
        log_path.write_text("\n".join(failed), encoding="utf-8")
        logger.warning(f"failed stocks written to {log_path}, count={len(failed)}")

    logger.info("daily history collection done")


def fmt_hhmm(v) -> str:
    try:
        s = str(v)
        if ":" in s:
            return s[:5]
        s = s.zfill(6)
        return f"{s[:2]}:{s[2:4]}"
    except Exception:
        return ""


def fetch_limit_up_history(engine, start_date: str, end_date: str, resume: bool = True):
    effective_start = max(start_date, "2019-01-01")
    trade_dates = get_trade_dates(effective_start, end_date)
    if not trade_dates:
        logger.error("no trade dates for limit_up")
        return

    if resume:
        latest = get_latest_date_in_db(engine, LimitUpStock, None, None)
        if latest:
            trade_dates = [d for d in trade_dates if d > latest]
            if not trade_dates:
                logger.info("limit_up already up-to-date")
                return

    total = len(trade_dates)
    em = get_em_client()

    for idx, td in enumerate(trade_dates, 1):
        try:
            df = em.stock_zt_pool_em(date=td.replace("-", ""))
            time.sleep(SLEEP_INTERVAL)
            if df is None or df.empty:
                logger.debug(f"[{idx}/{total}] {td} limit_up empty")
                continue

            records: list[dict] = []
            for _, row in df.iterrows():
                code = str(pick(row, ["代码", "证券代码", "c"], "")).strip()
                if not code:
                    continue

                continuous_days = safe_int(pick(row, ["连板数", "连续涨停天数", "lbc"], 1), 1)
                records.append(
                    {
                        "code": code,
                        "name": str(pick(row, ["名称", "证券简称", "n"], "")),
                        "trade_date": td,
                        "close": safe_float(pick(row, ["最新价", "最新价格", "p"], 0)),
                        "change_pct": safe_float(pick(row, ["涨跌幅", "涨跌幅(%)", "zdp"], 0)),
                        "limit_up_type": "连板" if continuous_days > 1 else "首板",
                        "continuous_days": continuous_days,
                        "seal_amount": safe_float(pick(row, ["封板资金", "封单资金", "fund"], 0)),
                        "seal_ratio": 0.0,
                        "first_limit_time": fmt_hhmm(pick(row, ["首次封板时间", "首次涨停时间", "fbt"], "")),
                        "last_limit_time": fmt_hhmm(pick(row, ["最后封板时间", "lbt"], "")),
                        "open_count": safe_int(pick(row, ["炸板次数", "开板次数", "zbc"], 0), 0),
                        "sector": str(pick(row, ["所属行业", "hybk"], "")),
                        "reason": "",
                        "circ_mv": safe_float(pick(row, ["流通市值", "ltsz"], 0)),
                    }
                )

            bulk_insert_ignore(engine, LimitUpStock, records)
            logger.info(f"[{idx}/{total}] {td} limit_up inserted {len(records)} rows")

        except Exception as e:
            logger.warning(f"[{idx}/{total}] {td} limit_up failed: {e}")
            time.sleep(SLEEP_INTERVAL)

    logger.info("limit_up history collection done")


def fetch_dragon_tiger(engine, start_date: str, end_date: str, resume: bool = True):
    actual_start = start_date
    if resume:
        latest = get_latest_date_in_db(engine, DragonTigerBoard, None, None)
        if latest and latest >= start_date:
            next_day = (datetime.strptime(latest, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
            if next_day > end_date:
                logger.info("dragon_tiger already up-to-date")
                return
            actual_start = next_day

    start_year = int(actual_start[:4])
    end_year = int(end_date[:4])
    em = get_em_client()

    for year in range(start_year, end_year + 1):
        seg_start = max(actual_start, f"{year}-01-01")
        seg_end = min(end_date, f"{year}-12-31")
        if seg_start > seg_end:
            continue

        try:
            df = em.stock_lhb_detail_em(start_date=seg_start, end_date=seg_end)
            time.sleep(SLEEP_INTERVAL)
            if df is None or df.empty:
                logger.debug(f"dragon_tiger {year} empty")
                continue

            records: list[dict] = []
            for _, row in df.iterrows():
                code = str(pick(row, ["代码", "证券代码", "SECURITY_CODE", "c"], "")).strip()
                if not code:
                    continue

                trade_date_raw = str(pick(row, ["TRADE_DATE", "交易日", "日期"], ""))
                trade_date = trade_date_raw[:10]
                if not trade_date:
                    continue

                records.append(
                    {
                        "code": code,
                        "name": str(pick(row, ["名称", "证券简称", "SECURITY_NAME_ABBR", "n"], "")),
                        "trade_date": trade_date,
                        "reason": str(pick(row, ["解读", "上榜原因", "EXPLAIN", "EXPLANATION"], "")),
                        "buy_seat": None,
                        "sell_seat": None,
                        "buy_total": safe_float(pick(row, ["买入额", "BILLBOARD_BUY_AMT"], 0)),
                        "sell_total": safe_float(pick(row, ["卖出额", "BILLBOARD_SELL_AMT"], 0)),
                        "net_amount": safe_float(pick(row, ["净额", "BILLBOARD_NET_AMT"], 0)),
                    }
                )

            bulk_insert_ignore(engine, DragonTigerBoard, records)
            logger.info(f"dragon_tiger {year} ({seg_start}~{seg_end}) inserted {len(records)} rows")

        except Exception as e:
            logger.warning(f"dragon_tiger {year} failed: {e}")
            time.sleep(SLEEP_INTERVAL)

    logger.info("dragon_tiger history collection done")


def main():
    parser = argparse.ArgumentParser(description="A-share history bulk collection")
    parser.add_argument(
        "--mode",
        choices=["all", "daily", "limit_up", "dragon_tiger"],
        default="all",
        help="collection mode (default: all)",
    )
    parser.add_argument("--start-date", default=DEFAULT_START, help="start date YYYY-MM-DD")
    parser.add_argument("--end-date", default=date.today().strftime("%Y-%m-%d"), help="end date YYYY-MM-DD")
    parser.add_argument(
        "--force-full",
        action="store_true",
        help="ignore resume logic and backfill full range",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=8,
        help="number of concurrent download threads (default: 8)",
    )
    args = parser.parse_args()

    if args.start_date < A_SHARE_OPEN_DATE:
        logger.warning(f"start-date earlier than market open, adjusted to {A_SHARE_OPEN_DATE}")
        args.start_date = A_SHARE_OPEN_DATE

    resume = not args.force_full
    logger.info(f"history collection start | mode={args.mode} | {args.start_date} ~ {args.end_date}")

    engine = get_db_engine()
    daily_resume = resume

    # 股票日线默认策略：
    # 1) 首次（stock_daily 空表）自动全量回补
    # 2) 后续自动增量追加
    # 3) --force-full 仍可强制全量
    if args.mode in ("all", "daily") and not args.force_full:
        if has_any_rows(engine, StockDaily.__tablename__):
            daily_resume = True
            logger.info("stock_daily detected: use incremental append mode")
        else:
            daily_resume = False
            logger.info("stock_daily empty: run first full bootstrap")

    if args.mode in ("all", "daily"):
        logger.info("=== collect daily ===")
        fetch_daily_ohlcv(engine, args.start_date, args.end_date, resume=daily_resume, workers=args.workers)

    if args.mode in ("all", "limit_up"):
        logger.info("=== collect limit_up ===")
        fetch_limit_up_history(engine, args.start_date, args.end_date, resume=resume)

    if args.mode in ("all", "dragon_tiger"):
        logger.info("=== collect dragon_tiger ===")
        fetch_dragon_tiger(engine, args.start_date, args.end_date, resume=resume)

    logger.info("all collection tasks done")


if __name__ == "__main__":
    main()
