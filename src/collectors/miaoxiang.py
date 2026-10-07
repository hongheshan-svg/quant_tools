"""妙想补充源：仅 A 股个股资金流和筹码；不进入日线或全市场行情链路。"""

from copy import deepcopy
from datetime import datetime
import hashlib
import math
import re
import threading
import time

import httpx

from src.collectors.request_budget import bounded_call
from src.utils.stock_code import bare_code

URL = "https://mkapi2.dfcfs.com/finskillshub/api/claw/query"
_admission = threading.Lock()
_cache_lock = threading.Lock()
_cache = {}


def number(value, *, money=False):
    if isinstance(value, bool) or value is None:
        return None
    text = str(value).strip().replace(",", "")
    if money and ("%" in text or "％" in text):
        return None
    match = re.fullmatch(r"([+-]?\d+(?:\.\d+)?)\s*(亿|万)?\s*(元|人民币|CNY|RMB|%|％)?", text, re.I)
    if not match:
        return None
    if match[2] and not money:
        return None
    result = float(match[1]) * ({"亿": 1e8, "万": 1e4}.get(match[2], 1) if money else 1)
    return result if math.isfinite(result) else None


def table_rows(table):
    mapping = table.get("nameMap") or {}
    if isinstance(mapping, list):
        mapping = {str(i): value for i, value in enumerate(mapping)}
    values = table.get("table") or {}
    heads = values.get("headName") or []
    rows = [{} for _ in heads]
    for key, column in values.items():
        if key == "headName" or not isinstance(column, list):
            continue
        for index, value in enumerate(column):
            if index >= len(rows):
                rows.append({})
            rows[index][str(mapping.get(str(key), key))] = value
    for index, head in enumerate(heads):
        rows[index]["date"] = str(head)[:10]
    return rows


class MiaoxiangClient:
    def __init__(self, config):
        self.key = str((config.get("data_sources") or {}).get("miaoxiang_api_key") or "").strip()

    def query(self, code, dataset):
        code = bare_code(code)
        if not self.key or not re.fullmatch(r"[03689]\d{5}", code) or dataset not in ("flow", "chips"):
            return None
        cache_key = (hashlib.sha256(self.key.encode()).hexdigest(), code, dataset)
        with _cache_lock:
            cached = _cache.get(cache_key)
            if cached and time.monotonic() - cached[0] < 300:
                return deepcopy(cached[1])
        def request():
            if not _admission.acquire(blocking=False):
                raise TimeoutError("妙想查询仍在执行，本次补充跳过")
            try:
                query = f"{code} 近10日每日主力净流入资金" if dataset == "flow" else f"{code} 获利比例 平均成本 90%筹码集中度 70%筹码集中度"
                response = httpx.post(URL, headers={"apikey": self.key}, json={"toolQuery": query}, timeout=10)
                response.raise_for_status()
                payload = response.json()
                if type(payload.get("status")) is not int or payload["status"] != 0:
                    raise ValueError("妙想查询返回失败状态")
                node = payload.get("data") or {}
                tables = ((node.get("data") or {}).get("searchDataResultDTO") or {}).get("dataTableDTOList") or node.get("dataTableDTOList")
                if not isinstance(tables, list) or not tables:
                    raise ValueError("妙想没有返回指标表格")
                return [row for table in tables if isinstance(table, dict) for row in table_rows(table)]
            finally:
                _admission.release()
        rows = bounded_call(request, 10, quarantine_key=("miaoxiang", dataset))
        rows = [row for row in rows if _valid_date(row.get("date"))]
        rows.sort(key=lambda row: row["date"], reverse=True)
        fetched = datetime.now().isoformat()
        if dataset == "flow":
            labels = ("主力净流入", "主力净流入资金", "主力净流入金额")
            rows = [row for row in rows if any(label in row for label in labels)]
            series = [(row["date"], number(next(row[label] for label in labels if label in row), money=True)) for row in rows]
            if not series or series[0][1] is None:
                raise ValueError("妙想最新资金流缺失或金额单位无效")
            def window(size):
                values = [value for _, value in series[:size]]
                return sum(values) if len(values) == size and all(value is not None for value in values) and len({day for day, _ in series[:size]}) == size else None
            result = {"code": code, "trade_date": series[0][0], "net_inflow": series[0][1], "net_ratio": None,
                      "inflow_5d": window(5), "inflow_10d": window(10), "unit": "CNY"}
        else:
            rows = [row for row in rows if number(row.get("平均成本")) is not None]
            if not rows or number(rows[0]["平均成本"]) <= 0:
                raise ValueError("妙想筹码平均成本缺失")
            row = rows[0]
            result = {"date": row["date"], "avg_cost": number(row["平均成本"]), "profit_ratio": number(row.get("获利比例")),
                      "concentration_90": number(row.get("90%筹码集中度")), "concentration_70": number(row.get("70%筹码集中度")),
                      "cost_90_low": None, "cost_90_high": None}
        result.update(source="妙想", provider_timestamp=None, fetched_at=fetched, status="partial")
        with _cache_lock:
            if len(_cache) >= 500:
                _cache.pop(next(iter(_cache)))
            _cache[cache_key] = (time.monotonic(), result)
        return deepcopy(result)


def _valid_date(value):
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d").date()
        return parsed <= datetime.now().date()
    except (TypeError, ValueError):
        return False
