"""台湾上市／上柜三大法人日资料；股数口径，来源独立限流、缓存及熔断。"""
from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
from datetime import date, datetime, timezone
import re
import threading
import time
from zoneinfo import ZoneInfo

import requests

URLS = {'TWSE': 'https://www.twse.com.tw/rwd/zh/fund/T86',
        'TPEx': 'https://www.tpex.org.tw/openapi/v1/tpex_3insti_daily_trading'}
TWSE_COLUMNS = {'foreign_net': '外陸資買賣超股數(不含外資自營商)', 'trust_net': '投信買賣超股數',
                'dealer_net': '自營商買賣超股數', 'total_net': '三大法人買賣超股數'}
TPEX_COLUMNS = {'foreign_net': 'Foreign Investors include Mainland Area Investors (Foreign Dealers excluded)-Difference',
                'trust_net': 'SecuritiesInvestmentTrustCompanies-Difference',
                'dealer_net': 'Dealers-Difference', 'total_net': 'TotalDifference'}


def parse_date(raw, *, minguo=False) -> str | None:
    text = str(raw or '').strip().replace('/', '').replace('-', '')
    if not text.isascii() or not text.isdigit() or len(text) != (7 if minguo else 8):
        return None
    try:
        year = int(text[:-4]) + (1911 if minguo else 0)
        return date(year, int(text[-4:-2]), int(text[-2:])).isoformat()
    except ValueError:
        return None


def shares(raw) -> int | None:
    if isinstance(raw, bool):
        return None
    text = str(raw).strip()
    if not re.fullmatch(r'[+-]?(?:[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)', text):
        return None
    # 股数须为整数，不截断小数、不接受 NaN/Infinity，也不把缺失变成 0。
    return int(text.replace(',', '')) if len(text) < 30 else None


def parse_table(payload, market: str, requested: str, now: datetime) -> dict[str, dict]:
    """按列名取值；资料日期必须明确且等于请求日。"""
    if market == 'TWSE':
        if not isinstance(payload, dict) or payload.get('stat') != 'OK':
            return {}
        fields, data = payload.get('fields'), payload.get('data')
        if not isinstance(fields, list) or not all(isinstance(f, str) for f in fields) or not isinstance(data, list):
            return {}
        if len(fields) != len(set(fields)):
            return {}
        day = parse_date(payload.get('date'))
        rows = [(dict(zip(fields, row)), day) for row in data if isinstance(row, list) and len(row) == len(fields)]
    else:
        rows = [(row, parse_date(row.get('Date'), minguo=True)) for row in payload if isinstance(row, dict)] if isinstance(payload, list) else []
    today = now.astimezone(ZoneInfo('Asia/Taipei')).date().isoformat()
    columns = TWSE_COLUMNS if market == 'TWSE' else TPEX_COLUMNS
    result = {}
    for row, day in rows:
        code = str(row.get('證券代號' if market == 'TWSE' else 'SecuritiesCompanyCode') or '').strip()
        if day != requested or not day or day > today or not re.fullmatch(r'[0-9A-Z]{4,8}', code):
            continue
        values = {key: shares(row.get(field)) for key, field in columns.items()}
        if any(values[key] is None for key in ('foreign_net', 'trust_net', 'dealer_net')):
            continue
        result[code] = {**values, 'as_of': day, 'source': market, 'source_url': URLS[market], 'unit': 'shares',
                        'fetched_at': now.isoformat(), 'status': 'available' if values['total_net'] is not None else 'partial',
                        'limitations': [] if values['total_net'] is not None else ['missing_official_total'],
                        'license': 'OGDL-1.0', 'license_url': 'https://data.gov.tw/license'}
    return result


class TaiwanInstitutionalFetcher:
    def __init__(self, *, ttl=900., min_interval=1.8, clock=time.monotonic, sleep=time.sleep):
        self.ttl, self.min_interval, self.clock, self.sleep = ttl, min_interval, clock, sleep
        self.locks = {market: threading.Lock() for market in URLS}
        self.tables = {market: OrderedDict() for market in URLS}
        self.failures = dict.fromkeys(URLS, 0)
        self.open_until = dict.fromkeys(URLS, 0.)
        self.last_request = dict.fromkeys(URLS, -float('inf'))

    def request(self, market, day):
        response = requests.get(URLS[market], params={'response': 'json', 'date': day.replace('-', ''),
                                'selectType': 'ALLBUT0999'} if market == 'TWSE' else None,
                                timeout=8, headers={'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json'})
        response.raise_for_status()
        return response.json()

    def fetch(self, code: str, day: str | None, *, now: datetime | None = None) -> dict:
        match = re.fullmatch(r'([0-9A-Z]{4,8})\.(TW|TWO)', str(code))
        if not match:
            return {'status': 'not_supported', 'limitations': ['taiwan_only']}
        market = 'TWSE' if match[2] == 'TW' else 'TPEx'
        day = parse_date(day)
        now = now or datetime.now(timezone.utc)
        empty = {'status': 'missing', 'source': market, 'source_url': URLS[market], 'unit': 'shares',
                 'requested_date': day, 'limitations': ['no_matching_day_or_symbol']}
        if day is None or day > now.astimezone(ZoneInfo('Asia/Taipei')).date().isoformat():
            return {**empty, 'limitations': ['unknown_or_future_trading_day']}
        # 固定两把锁，同时合并同日全市场请求，锁及缓存不会随证券数量增长。
        with self.locks[market]:
            cached = self.tables[market].get(day)
            if cached and self.clock() - cached[0] < self.ttl:
                return deepcopy(cached[1].get(match[1], empty))
            if self.clock() < self.open_until[market]:
                return {**empty, 'status': 'fetch_failed', 'limitations': ['circuit_open']}
            pause = self.min_interval - (self.clock() - self.last_request[market])
            if pause > 0:
                self.sleep(pause)
            self.last_request[market] = self.clock()
            try:
                payload = self.request(market, day)
                table = parse_table(payload, market, day, now)
            except Exception:
                self.failures[market] += 1
                if self.failures[market] >= 3:
                    self.open_until[market] = self.clock() + 300
                return {**empty, 'status': 'fetch_failed', 'limitations': ['source_unavailable']}
            self.failures[market] = 0
            self.open_until[market] = 0.
            if table:
                self.tables[market][day] = (self.clock(), table)
                self.tables[market].move_to_end(day)
                while len(self.tables[market]) > 16:
                    self.tables[market].popitem(last=False)
            return deepcopy(table.get(match[1], empty))


FETCHER = TaiwanInstitutionalFetcher()


def fetch(code: str) -> dict:
    from src.services.overseas_market import phase
    return FETCHER.fetch(code, phase('TW')['effective_daily_bar_date'])


def quality(block: dict, expected_day: str | None) -> dict:
    """旧缓存只作历史证据，刷新失败不改变原资料日期及采集时刻。"""
    if block.get('as_of') and (not expected_day or block['as_of'] != expected_day):
        return {**block, 'status': 'stale', 'limitations': list(dict.fromkeys([*block.get('limitations', []), 'different_trading_day']))}
    return block
