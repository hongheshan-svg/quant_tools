"""海外入口贯通：证券名称/导入、基本面币种、失败与超时，不访问外网。"""
import math
from datetime import datetime, timezone
import threading
import time

import pandas as pd
import pytest

from tests.test_decision_signals import db_path  # noqa: F401
from src.services.overseas_evidence import OverseasEvidenceService, financials, news_rows
from src.services.overseas_research import OverseasResearchService
from src.services.stock_search import StockSearch
from src.services.watchlist import WatchlistService, extract_tokens
from src.utils.stock_code import resolve_identity


@pytest.mark.parametrize('query,code,currency', [('苹果','AAPL','USD'),('Toyota','7203.T','JPY'),('삼성전자','005930.KS','KRW'),
    ('三星电子','005930.KS','KRW'),('台積電','2330.TW','TWD'),('腾讯','00700.HK','HKD'),('aapl','AAPL','USD')])
def test_overseas_names_and_aliases_resolve_without_network(db_path, query, code, currency):
    StockSearch.reset()
    row = StockSearch(db_path).search(query)[0]
    assert row['code'] == code and row['currency'] == currency
    assert WatchlistService({'database': {'sqlite_path': db_path}}).resolve(query)[0] == code


def test_import_preserves_korean_suffix_instead_of_splitting_as_a_share():
    assert extract_tokens('symbol,quantity\n005930.KS,600000\nAAPL,100\n')[0] == ['005930.KS', 'AAPL']
    assert extract_tokens('AAPL 005930.KS 600519 HK00700')[0] == ['AAPL', '005930.KS', '600519', '00700.HK']


def test_financial_report_uses_report_currency_not_trading_currency():
    now = datetime(2026, 10, 8, tzinfo=timezone.utc)
    statements = pd.DataFrame({pd.Timestamp('2026-06-30'): [1000., 200., 2.], pd.Timestamp('2027-01-01'): [9999., 9999., 9999.]},
                               index=['Total Revenue', 'Net Income', 'Basic EPS'])
    dividends = pd.Series([1.5], index=pd.DatetimeIndex(['2026-07-01']))
    info = {'financialCurrency':'CNY','currency':'HKD','trailingPE':math.nan,'priceToBook':2,'marketCap':5000,'revenueGrowth':.1}
    result = financials(info, statements, dividends, resolve_identity('00700.HK'), now)
    assert result['financial_report']['currency'] == 'CNY' and result['financial_report']['revenue'] == 1000
    assert result['valuation']['currency'] == result['dividends'][0]['currency'] == 'HKD'
    assert result['valuation']['pe_ttm'] is None and result['growth']['revenue_yoy_pct'] == 10
    assert result['as_of'] == '2026-06-30' and 'publication_dates_unverified' in result['limitations']
    mismatch = financials({**info,'currency':'USD'}, statements, dividends, resolve_identity('00700.HK'), now)
    assert mismatch['valuation']['market_cap'] is None and 'trading_currency_mismatch' in mismatch['limitations']


def test_news_preserves_dates_sources_and_rejects_future_or_unsafe_links():
    now = datetime(2026, 10, 8, tzinfo=timezone.utc)
    base = {'title':'季度报告','pubDate':'2026-10-07T12:00:00Z','provider':{'displayName':'公司披露'},'canonicalUrl':{'url':'https://example.com/report'}}
    result = news_rows([{'content':base}, {'content':{**base,'pubDate':'2027-01-01T00:00:00Z'}},
                       {'content':{**base,'canonicalUrl':{'url':'javascript:alert(1)'}}}], now)
    assert len(result) == 1 and result[0]['source'] == '公司披露' and result[0]['date'].startswith('2026-10-07')


def test_failed_refresh_retains_old_evidence_and_freshness(db_path, monkeypatch):
    config = {'database': {'sqlite_path': db_path}}
    service = OverseasEvidenceService(config)
    old = {'status':'partial','source':'Yahoo Finance','fetched_at':'2020-01-01T00:00:00+00:00',
           'financial_report':{'currency':'USD','revenue':100}}
    service.store.save_cache('overseas:evidence:AAPL', {'fundamentals':old})
    monkeypatch.setattr('src.services.overseas_evidence.fetch_part', lambda *args: (_ for _ in ()).throw(RuntimeError('secret-provider-error')))
    result = service.refresh('AAPL')
    assert result['fundamentals']['status'] == 'stale' and result['fundamentals']['refresh_status'] == 'fetch_failed'
    assert result['fundamentals']['fetched_at'] == old['fetched_at'] and result['fundamentals']['financial_report']['revenue'] == 100
    assert 'secret-provider-error' not in str(result)


def test_timeout_never_late_writes_result_into_cache(db_path, monkeypatch):
    release = threading.Event()
    config = {'database': {'sqlite_path': db_path}, 'data_sources': {'stage_timeout_seconds': .05}}
    service = OverseasEvidenceService(config)
    def fetch(code, part):
        if part == 'info': release.wait(2); return {'currency':'USD','trailingPE':25}
        return None
    monkeypatch.setattr('src.services.overseas_evidence.fetch_part', fetch)
    before = time.monotonic()
    try:
        result = service.refresh('AAPL')
        assert time.monotonic() - before < .8
        assert 'info' in result['fundamentals']['failed_blocks']
    finally: release.set()
    time.sleep(.02)
    stored = OverseasResearchService(config).cached('overseas:evidence:AAPL')
    assert stored['fundamentals']['valuation']['pe_ttm'] is None
