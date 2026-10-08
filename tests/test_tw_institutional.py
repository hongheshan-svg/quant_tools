"""台湾官方法人资料解析、缓存、故障隔离与研究消费，离线验收。"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import threading
import time

import pytest

from src.collectors import tw_institutional as tw
from src.services.overseas_evidence import OverseasEvidenceService
from src.services.overseas_research import OverseasResearchService
from src.services.research_artifact import build_context_pack
from tests.test_decision_signals import db_path  # noqa: F401
from tests.test_api import env  # noqa: F401

NOW = datetime(2026, 10, 8, 12, tzinfo=timezone.utc)
DAY = '2026-09-30'


def table(day='20260930'):
    return {'stat': 'OK', 'date': day, 'fields': ['證券代號', *tw.TWSE_COLUMNS.values()],
            'data': [['2330', '-1,234', '0', '234', '-1,000'], ['2317', '500', '100', '-50', '550']]}


def tpex(day='1150930'):
    return [{'SecuritiesCompanyCode': '6488', 'Date': day, **dict(zip(tw.TPEX_COLUMNS.values(), ['-1234', '0', '234', '-1000']))}]


def test_column_names_not_positions_units_and_negative_numbers():
    payload = table()
    payload['fields'].reverse()
    for row in payload['data']: row.reverse()
    row = tw.parse_table(payload, 'TWSE', DAY, NOW)['2330']
    assert (row['foreign_net'], row['trust_net'], row['dealer_net'], row['total_net']) == (-1234, 0, 234, -1000)
    assert row['unit'] == 'shares' and row['as_of'] == DAY and row['source'] == 'TWSE'


@pytest.mark.parametrize('raw', ['--', '', 'NaN', 'inf', '1.2', '1,23', True, None])
def test_invalid_number_is_missing_not_zero(raw):
    assert tw.shares(raw) is None


def test_missing_total_is_not_fabricated_and_required_field_missing_rejected():
    payload = table()
    payload['data'][0][-1] = '--'
    row = tw.parse_table(payload, 'TWSE', DAY, NOW)['2330']
    assert row['total_net'] is None and row['status'] == 'partial'
    payload['fields'][1] = 'unknown-column'
    assert not tw.parse_table(payload, 'TWSE', DAY, NOW)


@pytest.mark.parametrize('day', ['', '20260230', '20260929', '20270930'])
def test_unknown_wrong_or_future_date_is_not_certified(day):
    assert not tw.parse_table(table(day), 'TWSE', DAY, NOW)
    assert tw.parse_table(tpex('1150930'), 'TPEx', DAY, NOW)['6488']['as_of'] == DAY
    assert not tw.parse_table(tpex('1150929'), 'TPEx', DAY, NOW)
    assert tw.parse_date('1150230', minguo=True) is None


def test_whole_market_cache_coalesces_parallel_stocks(monkeypatch):
    fetcher = tw.TaiwanInstitutionalFetcher(min_interval=0)
    calls = []
    def request(*args):
        calls.append(args)
        time.sleep(.03)
        return table()
    monkeypatch.setattr(fetcher, 'request', request)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda code: fetcher.fetch(code, DAY, now=NOW), ['2330.TW', '2317.TW'] * 4))
    assert len(calls) == 1 and all(row['status'] == 'available' for row in results)
    results[0]['foreign_net'] = 999
    assert fetcher.fetch('2330.TW', DAY, now=NOW)['foreign_net'] == -1234


def test_circuit_breaker_separates_sources_and_recovers(monkeypatch):
    tick = [1000.]
    fetcher = tw.TaiwanInstitutionalFetcher(min_interval=0, clock=lambda: tick[0])
    calls = []
    def request(market, day):
        calls.append(market)
        if market == 'TWSE': raise RuntimeError('provider-secret')
        return tpex()
    monkeypatch.setattr(fetcher, 'request', request)
    for _ in range(4):
        result = fetcher.fetch('2330.TW', DAY, now=NOW)
        assert result['status'] == 'fetch_failed' and 'provider-secret' not in str(result)
    assert calls == ['TWSE'] * 3
    assert fetcher.fetch('6488.TWO', DAY, now=NOW)['status'] == 'available'
    tick[0] += 301
    monkeypatch.setattr(fetcher, 'request', lambda *args: table())
    assert fetcher.fetch('2330.TW', DAY, now=NOW)['status'] == 'available'


def test_empty_not_cached_and_requests_throttled(monkeypatch):
    tick, sleeps = [1000.], []
    def sleep(seconds): sleeps.append(seconds); tick[0] += seconds
    fetcher = tw.TaiwanInstitutionalFetcher(clock=lambda: tick[0], sleep=sleep)
    calls = []
    monkeypatch.setattr(fetcher, 'request', lambda *args: calls.append(args) or {})
    assert fetcher.fetch('2330.TW', DAY, now=NOW)['status'] == 'missing'
    assert fetcher.fetch('2330.TW', DAY, now=NOW)['status'] == 'missing'
    assert len(calls) == 2 and sleeps == [1.8]
    assert fetcher.fetch('2330', DAY, now=NOW)['status'] == 'not_supported'
    assert fetcher.fetch('AAPL', DAY, now=NOW)['status'] == 'not_supported'
    assert len(calls) == 2


def fixture_evidence(db_path, monkeypatch):
    config = {'database': {'sqlite_path': db_path}}
    monkeypatch.setattr('src.services.overseas_evidence.fetch_part', lambda *args: None)
    monkeypatch.setattr('src.services.overseas_market.phase', lambda *args: {'effective_daily_bar_date': DAY})
    row = tw.parse_table(table(), 'TWSE', DAY, NOW)['2330']
    monkeypatch.setattr(tw, 'fetch', lambda code: row)
    service = OverseasEvidenceService(config)
    return config, service, row


def test_evidence_refresh_retains_timestamp_and_reaches_context(db_path, monkeypatch):
    config, service, row = fixture_evidence(db_path, monkeypatch)
    result = service.refresh('2330.TW')
    assert result['institution']['foreign_net'] == -1234
    pack = build_context_pack({'code': '2330.TW', 'institution': result['institution']})
    assert pack['blocks']['institution']['items']['trust_net']['value'] == 0
    assert pack['blocks']['flow']['status'] == 'not_supported'
    monkeypatch.setattr(tw, 'fetch', lambda code: {'status': 'fetch_failed'})
    failed = service.refresh('2330.TW')['institution']
    assert failed['fetched_at'] == row['fetched_at'] and failed['refresh_status'] == 'fetch_failed'
    monkeypatch.setattr('src.services.overseas_market.phase', lambda *args: {'effective_daily_bar_date': '2026-10-01'})
    assert service.cached('2330.TW')['institution']['status'] == 'stale'
    assert 'institution' not in service.refresh('AAPL')


def test_institution_stage_timeout_cannot_late_write(db_path, monkeypatch):
    config, service, row = fixture_evidence(db_path, monkeypatch)
    config['data_sources'] = {'stage_timeout_seconds': .03}
    release = threading.Event()
    monkeypatch.setattr(tw, 'fetch', lambda _: release.wait(2) and row)
    try:
        result = service.refresh('2330.TW')
        assert result['institution']['status'] == 'fetch_failed'
    finally:
        release.set()
    time.sleep(.03)
    assert service.store.cached('overseas:evidence:2330.TW')['institution']['status'] == 'fetch_failed'


@pytest.mark.parametrize('lang,heading', [('zh','三大法人净买卖超'),('en','Institutional net flows'),('ko','3대 기관 순매수·순매도')])
def test_diagnosis_prompt_reports_and_download_preserve_institution(env, monkeypatch, lang, heading):
    client, _, config = env
    from src.services.report_language import report_language
    config['report'] = {'language': lang}
    # 实际设置使用 report.language。
    assert report_language(config) == lang
    institution = tw.parse_table(table(), 'TWSE', DAY, NOW)['2330']
    pack = build_context_pack({'code': '2330.TW', 'institution': institution})
    snapshot = {'name': '台积电', 'quote': {'trade_date': DAY}, 'quality': {'status': 'available', 'limitations': []}, 'context_pack': pack}
    monkeypatch.setattr(OverseasResearchService, 'refresh', lambda *args, **kwargs: snapshot)
    monkeypatch.setattr('src.services.social_sentiment.social_context', lambda *args: {})
    prompts = []
    def chat(self, data, prompt):
        prompts.append((json.loads(data), prompt))
        return {'score': 60, 'action': 'watch', 'core_logic': 'fixture research', 'risk_warning': 'fixture risk'}
    monkeypatch.setattr('src.analyzers.llm_client.LLMClient.chat_json', chat)
    result = OverseasResearchService(config).diagnose('2330.TW', force=True)
    assert '股数' in prompts[0][1] and '北向资金' in prompts[0][1]
    assert prompts[0][0]['research']['context_pack']['blocks']['institution']['items']['foreign_net']['value'] == -1234
    from src.services.stock_diagnosis import render_markdown
    rendered = render_markdown(result)
    assert heading in rendered and '-1,234' in rendered and 'fixture research' in rendered and 'TWD' in rendered
    response = client.get(f"/api/v1/stocks/diagnoses/{result['diagnosis_id']}/markdown")
    assert response.status_code == 200 and heading in response.text and '数据不完整' not in response.text
