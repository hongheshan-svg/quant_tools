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

from src.collectors.daily_history import (  # noqa: E402,F401  日线下载与解析（测试和旧调用方仍从本模块引用）
    CHANGE_LOOKBACK_DAYS,
    RETRYABLE_ERROR_KEYWORDS,
    call_with_retry,
    fetch_daily_df_with_fallback,
    is_retryable_exception,
    normalize_daily_date_column,
    pick,
    records_from_daily_df,
    safe_float,
    to_ak_symbol,
)
from src.collectors.em_client import get_em_client  # noqa: E402
from src.collectors.limit_up_reasons import fetch_ths_limit_up_reasons  # noqa: E402
from src.database.db import _auto_migrate  # noqa: E402
from src.database.models import (  # noqa: E402
    Base,
    DragonTigerBoard,
    LimitUpStock,
    StockDaily,
)

A_SHARE_OPEN_DATE = "1990-12-19"
DEFAULT_START = A_SHARE_OPEN_DATE
GAP_TOLERANCE_DAYS = 10  # stored history starting within this many days after --start-date counts as covered
SLEEP_INTERVAL = 0.3
BATCH_SIZE = 500


def get_db_engine():
    db_path = ROOT / "data" / "quant.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
        execution_options={"isolation_level": "AUTOCOMMIT"},
    )
    Base.metadata.create_all(engine)
    _auto_migrate(engine)  # 老数据库补齐新增列（如 limit_up_stock.concepts）
    return engine


def safe_int(value, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(value)
    except Exception:
        return default


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


def get_date_range_in_db(engine, table_cls, code: str) -> tuple[str | None, str | None]:
    """(earliest, latest) trade_date stored for one code."""
    with Session(engine) as s:
        try:
            row = s.execute(
                text(f"SELECT MIN(trade_date), MAX(trade_date) FROM {table_cls.__tablename__} WHERE code=:code"),
                {"code": code},
            ).fetchone()
            return (row[0], row[1]) if row else (None, None)
        except Exception:
            return None, None


def resume_start(earliest: str | None, latest: str | None, start_date: str, fill_gaps: bool) -> str:
    """Where incremental daily download should start for one code.

    Normally continue after the latest stored day. With fill_gaps (an explicit --start-date), stocks whose stored
    history starts well after start_date are fetched from start_date again: the daily quote collection stores
    only the latest day for every stock, which would otherwise make every stock look up to date.
    """
    if not latest or latest < start_date:
        return start_date
    if fill_gaps and earliest:
        limit = (datetime.strptime(start_date, "%Y-%m-%d") + timedelta(days=GAP_TOLERANCE_DAYS)).strftime("%Y-%m-%d")
        if earliest > limit:
            return start_date
    return (datetime.strptime(latest, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")


def bulk_upsert_daily(engine, records: list[dict]):
    """写入日线；已存在的行用下载的数据覆盖行情字段（修复旧版本写错单位的数据），换手率、市值只在新值有效时覆盖。"""
    if not records:
        return
    cols = list(records[0].keys())
    keep_if_empty = {"turnover", "total_mv", "circ_mv", "name"}
    updates = ", ".join(
        f"{c}=CASE WHEN excluded.{c} IS NULL OR excluded.{c} IN (0, '') THEN stock_daily.{c} ELSE excluded.{c} END"
        if c in keep_if_empty else f"{c}=excluded.{c}"
        for c in cols if c not in ("code", "trade_date")
    )
    sql = text(
        f"INSERT INTO stock_daily ({', '.join(cols)}) VALUES ({', '.join(':' + c for c in cols)}) "
        f"ON CONFLICT(code, trade_date) DO UPDATE SET {updates}"
    )
    with engine.begin() as conn:
        for i in range(0, len(records), BATCH_SIZE):
            conn.execute(sql, records[i: i + BATCH_SIZE])


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


def fetch_daily_ohlcv(engine, start_date: str, end_date: str, resume: bool = True, workers: int = 8, fill_gaps: bool = False,
                      overwrite: bool = False):
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
            actual_start = resume_start(*get_date_range_in_db(engine, StockDaily, code), start_date, fill_gaps)
            if actual_start > end_date:
                logger.debug(f"[{idx}/{total}] {code} {name} already up-to-date")
                return

        try:
            # 多取几天：涨跌幅按前一日收盘价计算，第一天也要有前收
            lookback_start = (datetime.strptime(actual_start, "%Y-%m-%d") - timedelta(days=CHANGE_LOOKBACK_DAYS)).strftime("%Y-%m-%d")
            source, df = fetch_daily_df_with_fallback(code, lookback_start, end_date)
            time.sleep(SLEEP_INTERVAL)

            if df is None or df.empty:
                logger.debug(f"[{idx}/{total}] {code} {name} no data")
                return

            records = [r for r in records_from_daily_df(code, name, source, df) if r["trade_date"] >= actual_start]
            if not records:
                logger.debug(f"[{idx}/{total}] {code} {name} no valid records")
                return

            if overwrite:
                bulk_upsert_daily(engine, records)
            else:
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


def fetch_limit_up_reasons_safe(trade_date: str) -> dict[str, str]:
    """同花顺涨停原因（题材标签），失败时返回空字典，不影响涨停池回补。"""
    try:
        return fetch_ths_limit_up_reasons(trade_date)
    except Exception as e:
        logger.warning(f"{trade_date} limit_up concepts failed: {e}")
        return {}


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
            concepts_by_code = fetch_limit_up_reasons_safe(td)

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
                        "reason": concepts_by_code.get(code, ""),
                        "concepts": concepts_by_code.get(code, ""),
                        "circ_mv": safe_float(pick(row, ["流通市值", "ltsz"], 0)),
                    }
                )

            bulk_insert_ignore(engine, LimitUpStock, records)
            logger.info(f"[{idx}/{total}] {td} limit_up inserted {len(records)} rows")

        except Exception as e:
            logger.warning(f"[{idx}/{total}] {td} limit_up failed: {e}")
            time.sleep(SLEEP_INTERVAL)

    logger.info("limit_up history collection done")


def fetch_limit_up_concepts(engine, start_date: str, end_date: str) -> int:
    """给已入库但缺少题材标签的涨停记录补齐同花顺涨停原因，返回更新的行数。"""
    with Session(engine) as session:
        dates = [
            d for (d,) in session.query(LimitUpStock.trade_date)
            .filter(LimitUpStock.trade_date >= start_date, LimitUpStock.trade_date <= end_date)
            .filter((LimitUpStock.concepts.is_(None)) | (LimitUpStock.concepts == ""))
            .distinct().order_by(LimitUpStock.trade_date).all()
        ]
    updated = 0
    for idx, td in enumerate(dates, 1):
        reasons = fetch_limit_up_reasons_safe(td)
        time.sleep(SLEEP_INTERVAL)
        if not reasons:
            continue
        with Session(engine) as session:
            rows = session.query(LimitUpStock).filter(LimitUpStock.trade_date == td).all()
            for row in rows:
                concepts = reasons.get(str(row.code)[-6:])
                if concepts and not row.concepts:
                    row.concepts = concepts
                    if not row.reason or "连板 |" in row.reason or row.reason == row.sector:
                        row.reason = concepts
                    updated += 1
            session.commit()
        logger.info(f"[{idx}/{len(dates)}] {td} limit_up concepts updated")
    logger.info(f"limit_up concepts backfill done: {updated} rows")
    return updated


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
        choices=["all", "daily", "limit_up", "concepts", "dragon_tiger"],
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
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="daily: re-download the range and overwrite existing rows (repairs volume/amount written by older versions)",
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
    # 3) --force-full 仍可强制全量；--overwrite 重新下载并覆盖已有行（修复旧版本写错的成交量/成交额）
    if args.mode in ("all", "daily") and (args.force_full or args.overwrite):
        daily_resume = False
    elif args.mode in ("all", "daily"):
        if has_any_rows(engine, StockDaily.__tablename__):
            daily_resume = True
            logger.info("stock_daily detected: use incremental append mode")
        else:
            daily_resume = False
            logger.info("stock_daily empty: run first full bootstrap")

    if args.mode in ("all", "daily"):
        logger.info("=== collect daily ===")
        # 显式指定 --start-date 时补齐该日期之后缺失的历史（日常采集只存了最新一天的行情）
        fetch_daily_ohlcv(engine, args.start_date, args.end_date, resume=daily_resume, workers=args.workers,
                          fill_gaps=args.start_date != DEFAULT_START, overwrite=args.overwrite)

    if args.mode in ("all", "limit_up"):
        logger.info("=== collect limit_up ===")
        fetch_limit_up_history(engine, args.start_date, args.end_date, resume=resume)

    if args.mode in ("all", "concepts"):
        logger.info("=== backfill limit_up concepts ===")
        fetch_limit_up_concepts(engine, args.start_date, args.end_date)

    if args.mode in ("all", "dragon_tiger"):
        logger.info("=== collect dragon_tiger ===")
        fetch_dragon_tiger(engine, args.start_date, args.end_date, resume=resume)

    logger.info("all collection tasks done")


if __name__ == "__main__":
    main()
