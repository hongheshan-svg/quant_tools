"""
额外数据源：baostock、通达信（pytdx）、efinance、Tushare Pro（参考 daily_stock_analysis 的 data_provider），
排在腾讯、新浪、东方财富之后作兜底，顺序由 data_sources.daily_history / data_sources.realtime 配置。

日线函数返回统一格式的 DataFrame：date(YYYY-MM-DD)、open、high、low、close、volume（股）、amount（元）、
turnover（换手率，小数；没有时为 0），交给 daily_history.records_from_daily_df 解析。
实时行情函数返回与 StockDataCollector 相同的中文列（代码、名称、最新价、涨跌幅、今开、最高、最低、成交量（股）、成交额（元）…）。

- baostock：免费，需要登录（自动完成），不支持北交所；前复权
- pytdx：通达信行情服务器，速度快；日线不复权，不支持北交所；服务器可用 data_sources.pytdx_servers 指定
- efinance：东方财富接口，东方财富不可用时它也不可用
- Tushare Pro：需要 data_sources.tushare_token；日线不复权
"""

from __future__ import annotations

import contextlib
import io
import threading
from collections.abc import Iterator
from typing import Any

import pandas as pd
from loguru import logger

from src.utils.stock_code import bare_code, exchange_of

DAILY_COLUMNS = ["date", "open", "high", "low", "close", "volume", "amount", "turnover"]

# 2026-09 实测可用的通达信行情服务器；内置列表失效时再试 pytdx 自带的列表
DEFAULT_TDX_SERVERS = [("117.34.114.17", 7709), ("117.34.114.14", 7709), ("117.34.114.15", 7709), ("117.34.114.18", 7709),
                       ("117.34.114.13", 7709), ("117.34.114.20", 7709), ("117.34.114.27", 7709), ("117.34.114.16", 7709)]
TDX_MAX_TRIES = 12
TDX_QUOTE_BATCH = 80      # get_security_quotes 每次最多 80 只
TDX_MAX_BARS = 800        # get_security_bars 每次最多 800 根


def data_source_config() -> dict[str, Any]:
    from src.config_loader import load_config

    return load_config().get("data_sources") or {}


def _daily_frame(df: pd.DataFrame, start_date: str, end_date: str) -> pd.DataFrame:
    """补齐统一列、按日期筛选排序"""
    for col in DAILY_COLUMNS:
        if col not in df.columns:
            df[col] = 0.0
    df = df[DAILY_COLUMNS].copy()
    df["date"] = df["date"].astype(str).str[:10]
    for col in DAILY_COLUMNS[1:]:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)
    df = df[(df["date"] >= start_date) & (df["date"] <= end_date) & (df["close"] > 0)]
    return df.sort_values("date").reset_index(drop=True)


# ---------- baostock ----------

_bs_lock = threading.Lock()


@contextlib.contextmanager
def _quiet() -> Iterator[None]:
    """baostock 登录、登出会往标准输出打印"""
    with contextlib.redirect_stdout(io.StringIO()):
        yield


def fetch_daily_baostock(code: str, start_date: str, end_date: str) -> pd.DataFrame:
    import baostock as bs

    exchange = exchange_of(code)
    if exchange == "bj":
        raise ValueError("baostock 不支持北交所")
    with _bs_lock:  # baostock 用模块级的单个连接，不能并发
        with _quiet():
            login = bs.login()
        if login.error_code != "0":
            raise RuntimeError(f"baostock 登录失败: {login.error_msg}")
        try:
            rs = bs.query_history_k_data_plus(f"{exchange}.{bare_code(code)}", "date,open,high,low,close,volume,amount,turn",
                                              start_date=start_date, end_date=end_date, frequency="d", adjustflag="2")
            if rs.error_code != "0":
                raise RuntimeError(f"baostock 查询失败: {rs.error_msg}")
            rows = []
            while rs.next():
                rows.append(rs.get_row_data())
        finally:
            with _quiet():
                bs.logout()
    df = pd.DataFrame(rows, columns=rs.fields)
    if df.empty:
        return df
    df["turnover"] = pd.to_numeric(df.pop("turn"), errors="coerce") / 100  # 百分比 → 小数
    return _daily_frame(df, start_date, end_date)


# ---------- 通达信（pytdx） ----------

_tdx_good: tuple[str, int] | None = None


def _parse_servers(items: list[Any]) -> list[tuple[str, int]]:
    servers = []
    for item in items or []:
        host, _, port = str(item).strip().rpartition(":")
        if host and port.isdigit():
            servers.append((host, int(port)))
    return servers


def tdx_servers() -> list[tuple[str, int]]:
    """尝试顺序：配置的服务器 → 上次连上的 → 内置列表 → pytdx 自带列表"""
    from pytdx.config.hosts import hq_hosts

    candidates = _parse_servers(data_source_config().get("pytdx_servers"))
    if _tdx_good:
        candidates.append(_tdx_good)
    candidates += DEFAULT_TDX_SERVERS + [(h[1], int(h[2])) for h in hq_hosts]
    return list(dict.fromkeys(candidates))


@contextlib.contextmanager
def tdx_connection() -> Iterator[Any]:
    global _tdx_good
    from pytdx.hq import TdxHq_API

    api = TdxHq_API()
    for host, port in tdx_servers()[:TDX_MAX_TRIES]:
        try:
            if api.connect(host, port, time_out=2):
                _tdx_good = (host, port)
                break
        except Exception as e:
            logger.debug(f"通达信 {host}:{port} 连接失败: {e}")
    else:
        raise ConnectionError("通达信行情服务器都连不上（可在 data_sources.pytdx_servers 指定）")
    try:
        yield api
    finally:
        with contextlib.suppress(Exception):
            api.disconnect()


def tdx_market(code: str) -> int:
    exchange = exchange_of(code)
    if exchange == "bj":
        raise ValueError("通达信标准行情不支持北交所")
    return 1 if exchange == "sh" else 0


def fetch_daily_pytdx(code: str, start_date: str, end_date: str) -> pd.DataFrame:
    days = (pd.Timestamp.today() - pd.Timestamp(start_date)).days
    count = min(max(days * 5 // 7 + 10, 30), TDX_MAX_BARS)
    with tdx_connection() as api:
        bars = api.get_security_bars(9, tdx_market(code), bare_code(code), 0, count)
    if not bars:
        return pd.DataFrame()
    df = pd.DataFrame(bars)
    df["date"] = df["datetime"].astype(str).str[:10]
    df["volume"] = pd.to_numeric(df["vol"], errors="coerce") * 100  # 手 → 股
    return _daily_frame(df, start_date, end_date)


def fetch_spot_pytdx(codes: list[str], names: dict[str, str] | None = None) -> pd.DataFrame:
    """全市场实时行情（按传入的代码批量查询，跳过北交所）；通达信不返回名称，由 names 提供"""
    pairs = []
    for code in dict.fromkeys(bare_code(c) for c in codes):
        with contextlib.suppress(ValueError):
            pairs.append((tdx_market(code), code))
    rows: list[dict[str, Any]] = []
    with tdx_connection() as api:
        for i in range(0, len(pairs), TDX_QUOTE_BATCH):
            for q in api.get_security_quotes(pairs[i:i + TDX_QUOTE_BATCH]) or []:
                price, last_close = q.get("price") or 0, q.get("last_close") or 0
                if price <= 0:
                    continue
                rows.append({
                    "代码": q["code"], "名称": (names or {}).get(q["code"], ""), "最新价": price, "今开": q.get("open"), "最高": q.get("high"), "最低": q.get("low"),
                    "涨跌幅": (price / last_close - 1) * 100 if last_close else None,
                    "成交量": (q.get("vol") or 0) * 100, "成交额": q.get("amount"),
                })
    return pd.DataFrame(rows)


# ---------- efinance ----------

def fetch_daily_efinance(code: str, start_date: str, end_date: str) -> pd.DataFrame:
    import efinance as ef

    df = ef.stock.get_quote_history(bare_code(code), beg=start_date.replace("-", ""), end=end_date.replace("-", ""), klt=101, fqt=1)
    if df is None or df.empty:
        return pd.DataFrame()
    df = df.rename(columns={"日期": "date", "开盘": "open", "收盘": "close", "最高": "high", "最低": "low", "成交额": "amount"})
    df["volume"] = pd.to_numeric(df["成交量"], errors="coerce") * 100          # 手 → 股
    df["turnover"] = pd.to_numeric(df.get("换手率", 0), errors="coerce") / 100  # 百分比 → 小数
    return _daily_frame(df, start_date, end_date)


def fetch_spot_efinance() -> pd.DataFrame:
    import efinance as ef

    df = ef.stock.get_realtime_quotes()
    if df is None or df.empty:
        return pd.DataFrame()
    df = df.rename(columns={"股票代码": "代码", "股票名称": "名称", "动态市盈率": "市盈率"})
    df["成交量"] = pd.to_numeric(df["成交量"], errors="coerce") * 100  # 手 → 股
    return df


# ---------- Tushare Pro ----------

def tushare_code(code: str) -> str:
    return f"{bare_code(code)}.{exchange_of(code).upper()}"


def fetch_daily_tushare(code: str, start_date: str, end_date: str, pro=None) -> pd.DataFrame:
    if pro is None:
        token = str(data_source_config().get("tushare_token") or "").strip()
        if not token:
            raise RuntimeError("没有配置 data_sources.tushare_token")
        import tushare as ts

        pro = ts.pro_api(token)
    df = pro.daily(ts_code=tushare_code(code), start_date=start_date.replace("-", ""), end_date=end_date.replace("-", ""))
    if df is None or df.empty:
        return pd.DataFrame()
    df = df.rename(columns={"trade_date": "date"})
    df["date"] = pd.to_datetime(df["date"].astype(str)).dt.strftime("%Y-%m-%d")
    df["volume"] = pd.to_numeric(df["vol"], errors="coerce") * 100        # 手 → 股
    df["amount"] = pd.to_numeric(df["amount"], errors="coerce") * 1000    # 千元 → 元
    return _daily_frame(df, start_date, end_date)
