"""近实时行情与 Futu 轻量财务的离线契约。"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import sys

import pandas as pd
import pytest

from tests.test_decision_signals import db_path  # noqa: F401
from src.services.overseas_market import phase
from src.services.overseas_realtime import normalize, quality
from src.services.overseas_research import OverseasResearchService
from src.services.overseas_evidence import OverseasEvidenceService, financials
from src.services import futu_evidence
from src.utils.stock_code import resolve_identity

NOW = datetime(2026, 10, 8, 14, tzinfo=timezone.utc)


def info(now=NOW):
    return {'regularMarketPrice': 200., 'regularMarketTime': now.timestamp() - 60, 'currency': 'USD', 'regularMarketChangePercent': 2.1, 'exchangeDataDelayedBy': 15}


def test_live_quote_uses_provider_time_not_fetch_time_and_keeps_delay_metadata():
    quote = normalize(info(), 'AAPL', NOW)
    assert quote['status'] == 'available' and quote['age_seconds'] == 60 and quote['delay_minutes'] == 15
    assert not quote['is_realtime_verified'] and quote['provider_timestamp'] != quote['fetched_at']
    assert quality(quote, NOW + timedelta(hours=1))['status'] == 'partial'
    assert quality(quote, NOW + timedelta(hours=1))['fetched_at'] == quote['fetched_at']
    assert normalize({**info(), 'currency':'HKD'}, 'AAPL', NOW)['price'] is None
    assert 'unknown_provider_time' in normalize({**info(), 'regularMarketTime':None}, 'AAPL', NOW)['limitations']
    assert 'future_timestamp' in normalize({**info(), 'regularMarketTime': (NOW + timedelta(hours=1)).timestamp()}, 'AAPL', NOW)['limitations']


def test_live_cache_does_not_replace_daily_quote_or_newer_provider_time(db_path, monkeypatch):
    service = OverseasResearchService({'database':{'sqlite_path':db_path}})
    now = datetime.now(timezone.utc)
    monkeypatch.setattr('src.services.overseas_realtime.fetch_quote', lambda code: info(now))
    service.save_cache('overseas:AAPL', {'bars': []})
    first = service.refresh_realtime('AAPL')
    assert first['status'] == 'available'
    snapshot = service.snapshot('AAPL')
    assert snapshot['quote'] == {} and snapshot['realtime_quote']['price'] == 200
    assert snapshot['context_pack']['blocks']['realtime']['status'] == 'available'
    monkeypatch.setattr('src.services.overseas_realtime.fetch_quote', lambda code: info(now - timedelta(hours=1)))
    old = service.refresh_realtime('AAPL')
    assert old['refresh_status'] == 'fetch_failed' and old['provider_timestamp'] == first['provider_timestamp']
    assert service.realtime('AAPL')['fetched_at'] == first['fetched_at']


@pytest.mark.parametrize('market,minute_before,minute_at', [('JP',24,25),('KR',19,20)])
def test_jp_kr_closing_auction_does_not_complete_daily_bar(market, minute_before, minute_at):
    before = phase(market, datetime(2026, 9, 30, 15, minute_before))
    auction = phase(market, datetime(2026, 9, 30, 15, minute_at))
    closed = phase(market, datetime(2026, 9, 30, 15, 30))
    assert before['phase'] == 'intraday' and auction['phase'] == 'closing_auction'
    assert auction['is_partial_bar'] and auction['effective_daily_bar_date'] == before['effective_daily_bar_date']
    assert closed['phase'] == 'post_market' and closed['effective_daily_bar_date'] == '2026-09-30'


def futu_report(currency='CNY', day='2026-06-30'):
    return {'financial_report':{'as_of':day, 'currency':currency, 'revenue':1000., 'net_profit':200., 'basic_eps':None, 'gross_profit':300.},
            'profile':{'longName':'公司名称'}, 'valuation':{'pb':2.}, 'growth':{'revenue_yoy_pct':10., 'earnings_yoy_pct':20., 'gross_margin_pct':30.},
            'dividends':[], 'failed_blocks':[]}


def yahoo_report(currency='USD'):
    statement = pd.DataFrame({pd.Timestamp('2026-06-30'):[800.,160.,5.]}, index=['Total Revenue','Net Income','Basic EPS'])
    return financials({'financialCurrency':currency,'currency':'HKD','trailingPE':15}, statement, None, resolve_identity('00700.HK'), NOW)


def test_futu_merge_never_fills_financials_across_currencies_or_periods():
    value = futu_evidence.merge(yahoo_report(), futu_report())
    assert value['financial_report']['currency'] == 'CNY' and value['financial_report']['basic_eps'] is None
    assert value['financial_report']['revenue'] == 1000 and value['valuation']['pe_ttm'] == 15
    assert value['field_sources']['financial_report.revenue'] == 'Futu OpenD'
    assert value['field_sources']['valuation.pe_ttm'] == 'Yahoo Finance'
    same = futu_evidence.merge(yahoo_report('CNY'), futu_report())
    assert same['financial_report']['basic_eps'] == 5 and same['field_sources']['financial_report.basic_eps'] == 'Yahoo Finance'
    earlier = futu_evidence.merge(yahoo_report(), futu_report(day='2026-03-31'))
    assert earlier['financial_report']['revenue'] == 800
    unknown = futu_evidence.merge(yahoo_report(), futu_report(currency=None, day='2026-09-30'))
    assert unknown['financial_report']['currency'] is None and 'unknown_financial_currency' in unknown['limitations']


def test_futu_quote_context_is_read_only_closes_and_isolates_failed_blocks(monkeypatch):
    calls = []
    class QuoteContext:
        def __init__(self, **kwargs): calls.append(kwargs)
        def get_company_profile(self, code):
            assert code == 'HK.00700'
            return 0, pd.DataFrame([{'name':'公司名称','value':'港股测试公司'}])
        def get_financials_statements(self, code, **kwargs):
            return 0, {'report_list':[{'date_time_str':'2026-06-30', 'currency_code':'CNY', 'item_list':[{'display_name':'营业额','data':1000,'yoy':10}]}]}
        def get_corporate_actions_dividends(self, code): raise RuntimeError('private SDK error')
        def get_market_snapshot(self, codes): return 0, pd.DataFrame([{'pe_ttm_ratio':25, 'pb_ratio':2}])
        def close(self): calls.append('closed')
    monkeypatch.setitem(sys.modules, 'futu', SimpleNamespace(OpenQuoteContext=QuoteContext, RET_OK=0))
    result = futu_evidence.fetch({}, '00700.HK')
    assert calls[-1] == 'closed' and result['profile']['longName'] == '港股测试公司'
    assert result['financial_report']['currency'] == 'CNY' and result['financial_report']['revenue'] == 1000
    assert result['failed_blocks'] == ['dividends'] and 'private SDK error' not in str(result)


def test_evidence_uses_futu_only_when_hk_and_enabled(db_path, monkeypatch):
    calls = []
    monkeypatch.setattr('src.services.futu_evidence.fetch', lambda config, code: calls.append(code) or futu_report())
    monkeypatch.setattr('src.services.overseas_evidence.fetch_part', lambda *args: None)
    base = {'database':{'sqlite_path':db_path}}
    OverseasEvidenceService(base).refresh('00700.HK')
    enabled = {**base, 'integrations':{'futu':{'fundamentals_enabled':True}}}
    OverseasEvidenceService(enabled).refresh('AAPL')
    assert calls == []
    value = OverseasEvidenceService(enabled).refresh('00700.HK')
    assert calls == ['00700.HK'] and value['fundamentals']['financial_report']['currency'] == 'CNY'
    assert value['fundamentals']['status'] == 'partial'


def test_futu_setting_accepts_boolean_only():
    from src.services.integration_settings import merge_settings, metadata
    assert merge_settings({}, {'futu':{'fundamentals_enabled':True}})['futu']['fundamentals_enabled']
    group = next(group for group in metadata() if group['key'] == 'futu')
    assert next(field for field in group['fields'] if field['key'] == 'fundamentals_enabled')['type'] == 'checkbox'
    with pytest.raises(ValueError): merge_settings({}, {'futu':{'fundamentals_enabled':'true'}})
