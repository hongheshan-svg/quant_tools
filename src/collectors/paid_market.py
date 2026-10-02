"""可选的 A 股供应商：TickFlow 和 Tushare HTTP 兼容网关。"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import requests

from src.collectors.extra_sources import _daily_frame, data_source_config, tushare_code
from src.utils.redaction import redact_text

SHANGHAI = ZoneInfo("Asia/Shanghai")


class TushareHttpClient:
    def __init__(self, config: dict):
        self.token = str(config.get("tushare_token") or "").strip()
        self.url = str(config.get("tushare_http_url") or "https://api.tushare.pro").strip()
        self.timeout = int(config.get("request_timeout_seconds", 15))
        if not self.token:
            raise ValueError("Tushare Token 未配置")

    def query(self, api_name: str, fields: str = "", **params) -> pd.DataFrame:
        response = requests.post(self.url, json={"api_name": api_name, "token": self.token, "params": params, "fields": fields},
                                 timeout=self.timeout)
        response.raise_for_status()
        payload = response.json()
        if payload.get("code") != 0:
            # 不把服务端回显的凭据带入诊断、任务或健康记录。
            message = str(payload.get("msg") or "请求失败").replace(self.token, "[REDACTED]")
            raise RuntimeError(redact_text(message))
        data = payload.get("data") or {}
        return pd.DataFrame(data.get("items") or [], columns=data.get("fields") or [])

    def daily(self, **params) -> pd.DataFrame:
        return self.query("daily", **params)


def _tickflow_client(config: dict):
    from tickflow import TickFlow

    key = str(config.get("tickflow_api_key") or "").strip()
    if not key:
        raise ValueError("TickFlow API Key 未配置")
    return TickFlow(api_key=key, timeout=int(config.get("request_timeout_seconds", 15)), max_retries=0)


def _dates(series: pd.Series) -> pd.Series:
    """TickFlow 数字时间戳是毫秒；统一为上海交易日期，避免 UTC 跨日。"""
    numeric = pd.to_numeric(series, errors="coerce")
    if numeric.notna().all() and (numeric > 1e11).all():
        return pd.to_datetime(numeric, unit="ms", utc=True).dt.tz_convert(SHANGHAI).dt.strftime("%Y-%m-%d")
    if numeric.notna().all() and (numeric > 1e9).all() and (numeric < 1e11).all():
        return pd.to_datetime(numeric, unit="s", utc=True).dt.tz_convert(SHANGHAI).dt.strftime("%Y-%m-%d")
    return pd.to_datetime(series, errors="coerce", utc=True).dt.tz_convert(SHANGHAI).dt.strftime("%Y-%m-%d")


def fetch_daily_tickflow(code: str, start_date: str, end_date: str) -> pd.DataFrame:
    config = data_source_config()
    client = _tickflow_client(config)
    start = datetime.fromisoformat(start_date).replace(tzinfo=SHANGHAI)
    end = datetime.fromisoformat(end_date).replace(hour=23, minute=59, second=59, tzinfo=SHANGHAI)
    count = min(10000, max(30, (end - start).days + 30))
    try:
        frame = client.klines.get(tushare_code(code), period="1d", count=count,
                                  start_time=int(start.timestamp() * 1000), end_time=int(end.timestamp() * 1000),
                                  adjust=config.get("tickflow_kline_adjust", "forward"), as_dataframe=True)
        if frame is None or frame.empty:
            return pd.DataFrame()
        frame = frame.copy()
        date_col = next((c for c in ("date", "timestamp", "time", "ts") if c in frame), None)
        if date_col is None:
            raise ValueError("TickFlow 日线缺少时间")
        frame["date"] = _dates(frame[date_col])
        if len(frame) >= count and frame["date"].min() > start_date:
            raise ValueError("TickFlow 日线达到返回上限，历史区间可能不完整")
        frame["volume"] = pd.to_numeric(frame["volume"], errors="coerce") * 100  # 手 → 股
        result = _daily_frame(frame, start_date, end_date)
        result.attrs["price_adjustment"] = config.get("tickflow_kline_adjust", "forward")
        return result
    except Exception as error:
        message = str(error)
        if config.get("tickflow_api_key"):
            message = message.replace(config["tickflow_api_key"], "[REDACTED]")
        raise RuntimeError(redact_text(message)) from None
    finally:
        client.close()


def fetch_spot_tickflow(config: dict) -> pd.DataFrame:
    client = _tickflow_client(config)
    try:
        quotes = client.quotes.get(universes=["CN_Equity_A"])
        rows = []
        for quote in quotes or []:
            ext = quote.get("ext") or {}
            price, previous = quote.get("last_price"), quote.get("prev_close")
            rows.append({"代码": str(quote.get("symbol") or ""), "名称": quote.get("name", ""), "最新价": price,
                         "今开": quote.get("open"), "最高": quote.get("high"), "最低": quote.get("low"),
                         "成交量": float(quote.get("volume") or 0) * 100, "成交额": quote.get("amount"),
                         "涨跌幅": float(ext["change_pct"]) * 100 if ext.get("change_pct") is not None else
                         (float(price) / float(previous) - 1) * 100 if price and previous else None,
                         "换手率": float(ext.get("turnover_rate") or 0) * 100,
                         "行情日期": _dates(pd.Series([quote.get("timestamp") or quote.get("time")])).iloc[0]})
        return pd.DataFrame(rows)
    except Exception as error:
        message = str(error)
        if config.get("tickflow_api_key"):
            message = message.replace(config["tickflow_api_key"], "[REDACTED]")
        raise RuntimeError(redact_text(message)) from None
    finally:
        client.close()


def fetch_spot_tushare(config: dict) -> pd.DataFrame:
    frame = TushareHttpClient(config).query("rt_k", ts_code="3*.SZ,6*.SH,0*.SZ,4*.BJ,8*.BJ,9*.BJ",
                                           fields="ts_code,name,pre_close,open,high,low,close,vol,amount,trade_time")
    if frame.empty:
        return frame
    frame = frame.rename(columns={"ts_code": "代码", "name": "名称", "close": "最新价", "open": "今开",
                                  "high": "最高", "low": "最低", "pct_chg": "涨跌幅", "amount": "成交额"})
    frame["成交量"] = pd.to_numeric(frame["vol"], errors="coerce")  # rt_k 是股；daily 才是手
    previous = pd.to_numeric(frame["pre_close"], errors="coerce")
    frame["涨跌幅"] = (pd.to_numeric(frame["最新价"], errors="coerce") / previous.where(previous > 0) - 1) * 100
    if "trade_time" in frame:
        frame["行情日期"] = pd.to_datetime(frame["trade_time"], errors="coerce").dt.strftime("%Y-%m-%d")
    if "trade_date" in frame:
        frame["行情日期"] = pd.to_datetime(frame["trade_date"].astype(str), errors="coerce").dt.strftime("%Y-%m-%d")
    return frame
