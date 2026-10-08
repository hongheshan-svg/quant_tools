"""海外、宏观、本地生成与新增通知模式的离线协议验收。"""
from datetime import datetime, timezone
import hashlib
import hmac
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import httpx
import pytest

from tests.test_api import env  # noqa: F401
from tests.test_decision_signals import db_path  # noqa: F401
from src.services.overseas_research import OverseasResearchService
from src.services.overseas_portfolio import OverseasPortfolioService, parse_csv
from src.services import overseas_market
from src.utils.stock_code import resolve_identity, code_candidates


def cfg(path):
    return {'database': {'sqlite_path': path}}


@pytest.mark.parametrize('raw,code,market,currency', [
    ('HK00700', '00700.HK', 'HK', 'HKD'), ('700.HK', '00700.HK', 'HK', 'HKD'),
    ('US.AAPL', 'AAPL', 'US', 'USD'), ('BRK.B', 'BRK-B', 'US', 'USD'),
    ('7203.T', '7203.T', 'JP', 'JPY'), ('005930.KS', '005930.KS', 'KR', 'KRW'),
    ('035720.KQ', '035720.KQ', 'KR', 'KRW'), ('2330.TW', '2330.TW', 'TW', 'TWD'),
    ('JP:7203', '7203.T', 'JP', 'JPY'), ('000001', '000001', 'CN', 'CNY'),
])
def test_international_identity_does_not_lose_market(raw, code, market, currency):
    identity = resolve_identity(raw)
    assert (identity.code, identity.market, identity.currency) == (code, market, currency)
    assert code in code_candidates(raw)


def test_overseas_calendars_handle_timezone_holiday_and_incomplete_close():
    utc = datetime(2026, 9, 30, 18, tzinfo=timezone.utc)
    state = overseas_market.phase('US', utc)
    assert state['is_partial_bar'] and state['effective_daily_bar_date'] == '2026-09-29'
    assert overseas_market.phase('US', utc.astimezone(__import__('zoneinfo').ZoneInfo('Asia/Taipei'))) == state
    assert overseas_market.phase('HK', datetime(2026, 10, 1, 16))['effective_daily_bar_date'] == '2026-09-30'
    for market in ('JP', 'KR', 'TW'):
        assert overseas_market.phase(market, datetime(2026, 9, 30, 22, tzinfo=timezone.utc))['calendar_verified']


@pytest.fixture
def overseas_clock(monkeypatch):
    from src.services import overseas_research
    real = overseas_market.phase
    now = datetime(2026, 9, 30, 22, tzinfo=timezone.utc)
    monkeypatch.setattr(overseas_market, 'phase', lambda market, moment=None: real(market, moment or now))
    monkeypatch.setattr(overseas_research, 'phase', lambda market, moment=None: real(market, moment or now))
    return now


def bars():
    return [{'code': 'AAPL', 'trade_date': '2026-09-30', 'open': 199., 'high': 202., 'low': 198., 'close': 200.,
             'volume': 1000., 'source': 'fixture', 'price_adjustment': 'raw', 'currency': 'USD',
             'fetched_at': '2026-09-30T22:00:00+00:00'}]


def test_overseas_cache_quality_and_fallback(db_path, monkeypatch, overseas_clock):
    service = OverseasResearchService(cfg(db_path))
    monkeypatch.setattr(service, '_yahoo', lambda identity: (_ for _ in ()).throw(RuntimeError('offline')))
    monkeypatch.setattr(service, '_longbridge', lambda identity: bars())
    snapshot = service.refresh('AAPL')
    assert snapshot['quote']['close'] == 200 and snapshot['quality']['status'] == 'available'
    assert snapshot['context_pack']['subject']['currency'] == 'USD'
    assert [r['status'] for r in snapshot['attempts']] == ['fetch_failed', 'available']
    monkeypatch.setattr(service, '_longbridge', lambda identity: [])
    failed = service.refresh('AAPL')
    assert failed['refresh_status'] == 'fetch_failed' and failed['quote']['fetched_at'] == bars()[0]['fetched_at']


def test_missing_overseas_calendar_never_certifies_quote(monkeypatch, overseas_clock):
    import exchange_calendars
    monkeypatch.setattr(exchange_calendars, 'get_calendar', lambda name: (_ for _ in ()).throw(ValueError('unavailable')))
    quality = overseas_market.quote_quality(bars()[0], 'US')
    assert quality['status'] == 'partial' and 'calendar_unverified' in quality['limitations']


def test_overseas_import_is_atomic_idempotent_and_does_not_sum_currencies(db_path, monkeypatch, overseas_clock):
    service = OverseasPortfolioService(cfg(db_path))
    text = 'symbol,quantity,average_cost,currency\nAAPL,10,180,USD\nHK00700,100,300,HKD'
    assert service.import_csv(text, '券商', '2026-09-30')['saved'] == 0
    assert service.portfolio()['positions'] == []
    for _ in range(2):
        assert service.import_csv(text, '券商', '2026-09-30', preview=False)['saved'] == 2
    portfolio = service.portfolio()
    assert len(portfolio['positions']) == 2 and portfolio['market_value_cny'] is None
    assert portfolio['positions'][0]['market_value'] is None
    service.research.save_cache('overseas:AAPL', {'bars': bars()})
    hk = [{**bars()[0], 'code': '00700.HK', 'currency': 'HKD', 'close': 400}]
    service.research.save_cache('overseas:00700.HK', {'bars': hk})
    monkeypatch.setattr(service.research, 'fx_rate', lambda currency, **kw: {'status': 'available', 'rate': 7.1 if currency == 'USD' else .91})
    assert service.portfolio()['market_value_cny'] == 50600
    assert not service.import_csv(text + '\nAAPL,-1,3,USD', '券商', '2026-09-30', preview=False)['ok']
    assert len(service.portfolio()['positions']) == 2
    with pytest.raises(ValueError, match='早于'):
        service.import_csv(text, '券商', '2026-09-29', preview=False)


@pytest.mark.parametrize('text', ['symbol,quantity,currency\nAAPL,NaN,USD', 'symbol,quantity,currency\nAAPL,2,HKD',
    'symbol,quantity\nAAPL,1\nAAPL,2', '证券代码,持仓数量,市场\n600519,100,CN'])
def test_broker_csv_rejects_unsafe_or_ambiguous_rows(text):
    assert not parse_csv(text, 'x', '2026-09-30')['ok']


def test_fx_quality_rejects_future_and_stale_rates(db_path):
    service = OverseasResearchService(cfg(db_path))
    for day in ('2000-01-01', '2099-01-01'):
        service.save_cache('overseas:fx:USD', {'rate': 7.2, 'as_of': day})
        assert service.fx_rate('USD')['status'] == 'unavailable'


def test_futu_read_only_closes_context_and_filters_short(db_path, monkeypatch):
    import pandas as pd
    calls = []
    class Context:
        def __init__(self, **kwargs): calls.append(kwargs)
        def get_acc_list(self): return 0, pd.DataFrame([{'acc_id': 7, 'trd_env': 'REAL'}, {'acc_id': 8, 'trd_env': 'SIMULATE'}])
        def position_list_query(self, **kwargs):
            calls.append(kwargs)
            return 0, pd.DataFrame([{'code': 'US.AAPL', 'qty': 2, 'position_side': 'LONG'},
                {'code': 'HK.00700', 'qty': 100, 'position_side': 'LONG'}, {'code': 'US.TSLA', 'qty': 5, 'position_side': 'SHORT'}])
        def close(self): calls.append('closed')
    monkeypatch.setitem(sys.modules, 'futu', SimpleNamespace(OpenSecTradeContext=Context, TrdEnv=SimpleNamespace(REAL='REAL'), TrdMarket=SimpleNamespace(NONE='NONE'), RET_OK=0))
    result = OverseasPortfolioService(cfg(db_path)).futu_symbols()
    assert result['codes'] == ['00700.HK', 'AAPL'] and result['skipped'] == 1
    assert calls[-1] == 'closed' and calls[1]['acc_id'] == 7


def test_macro_full_catalogue_preserves_schemas_and_hides_credentials(monkeypatch):
    from src.services import macro_tools
    from src.services.chat_tools import native_tool_schemas, ChatTools
    from fxmacrodata_public import list_operations
    schemas = {s['function']['name']: s['function']['parameters'] for s in native_tool_schemas()}
    assert len(macro_tools.catalogue()) == 72
    assert all(schemas['fxmacrodata_' + op.name] == op.input_schema for op in list_operations())
    calls = []
    class Client:
        def __init__(self, **kwargs): calls.append(kwargs)
        def execute(self, name, args): return SimpleNamespace(as_dict=lambda: {'source': 'official', 'timestamp': 'date', 'value': 'secret-fixture'})
        def close(self): calls.append('closed')
    monkeypatch.setattr('fxmacrodata_public.FXMacroDataClient', Client)
    result = ChatTools({'integrations': {'fxmacrodata': {'api_key': 'secret-fixture'}}}).call('fxmacrodata_health', {})
    assert 'secret-fixture' not in result and 'official' in result and calls[-1] == 'closed'
    assert macro_tools.execute({}, 'health', {'api_key': 'untrusted'})['status'] == 'invalid'


def test_slack_bot_text_and_image_use_same_channel(monkeypatch):
    from src.notifier.slack import SlackNotifier
    calls = []
    def post(url, **kwargs):
        calls.append((url, kwargs))
        return SimpleNamespace(status_code=200, json=lambda: {'ok': True})
    monkeypatch.setattr(httpx, 'post', post)
    monkeypatch.setattr(httpx, 'get', lambda *a, **kw: SimpleNamespace(json=lambda: {'ok': True, 'upload_url': 'https://files.slack.com/upload/x', 'file_id': 'F1'}))
    service = SlackNotifier({'notifier': {'slack': {'enabled': True, 'bot_token': 'token', 'channel_id': 'C1'}}})
    assert service.send('title', 'content') and service.send_image('title', b'png')
    assert calls[0][1]['json']['channel'] == calls[-1][1]['json']['channel_id'] == 'C1'
    assert 'headers' not in calls[1][1]  # 上传 URL 不泄漏 Bot Token


def test_feishu_lark_app_auth_and_image(monkeypatch):
    from src.notifier.feishu import FeishuNotifier
    calls = []
    def post(url, **kwargs):
        calls.append((url, kwargs))
        body = {'code': 0, 'tenant_access_token': 'tenant', 'expire': 7200, 'data': {'image_key': 'image'}}
        return SimpleNamespace(status_code=200, json=lambda: body)
    monkeypatch.setattr(httpx, 'post', post)
    service = FeishuNotifier({'notifier': {'feishu': {'enabled': True, 'app_id': 'app', 'app_secret': 'secret', 'chat_id': 'chat', 'domain': 'lark'}}})
    assert service.send('title', 'text') and service.send_image('title', b'png')
    assert all(url.startswith('https://open.larksuite.com/') for url, _ in calls)
    assert len([url for url, _ in calls if '/auth/' in url]) == 1
    assert calls[-1][1]['json']['receive_id'] == 'chat'


def test_astrbot_signature_matches_sent_payload(monkeypatch):
    from src.notifier.astrbot import AstrBotNotifier
    calls = []
    monkeypatch.setattr(httpx, 'post', lambda url, **kw: calls.append(kw) or SimpleNamespace(status_code=200))
    assert AstrBotNotifier({'notifier': {'astrbot': {'enabled': True, 'url': 'https://fixture.local', 'token': 'key'}}}).send('t', '<script>')
    request = calls[0]
    signed = request['headers']['X-Timestamp'] + '.' + json.dumps(request['json'], sort_keys=True)
    assert request['headers']['X-Signature'] == hmac.new(b'key', signed.encode(), hashlib.sha256).hexdigest()
    assert '<script>' not in request['json']['content']


def test_social_evidence_separates_failure_and_non_us(monkeypatch):
    from src.services.social_sentiment import social_context
    def get(url, **kwargs):
        if '/x/' in url: raise httpx.ReadTimeout('offline')
        data = {'ticker': 'AAPL', 'score': 1} if '/reddit/' in url else {'data': [{'ticker': 'AAPL'}, {'ticker': 'TSLA'}]}
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: data)
    monkeypatch.setattr(httpx, 'get', get)
    config = {'integrations': {'adanos': {'api_key': 'fixture'}}}
    assert social_context(config, '00700.HK')['status'] == 'not_supported'
    result = social_context(config, 'AAPL')
    assert result['status'] == 'partial' and result['channels'][1]['status'] == 'fetch_failed'
    assert result['channels'][2]['data'] == [{'ticker': 'AAPL'}]


def test_extensions_api_masks_validates_and_uses_common_auth(env):
    client, app, config = env
    config['integrations'] = {'longbridge': {'app_key': 'secret-key', 'app_secret': 'secret-pass', 'access_token': 'secret-token'}}
    response = client.get('/api/v1/settings/integrations')
    assert response.status_code == 200 and 'secret-token' not in response.text
    assert response.json()['settings']['longbridge']['access_token'] == '******'
    assert client.put('/api/v1/settings/integrations', json={'runtime': {'hermes_url': 'https://remote.example/v1'}}).status_code == 400
    assert client.get('/api/v1/integrations/macro/operations').status_code == 200
    assert client.get('/api/v1/overseas/AAPL').json()['currency'] == 'USD'
    assert client.get('/api/v1/overseas/600519').status_code == 400


def test_korean_prompt_and_settings(env):
    from src.services.report_language import report_language, language_directive
    assert report_language({'report': {'language': 'ko'}}) == 'ko'
    directive = language_directive('ko', 'action=buy/watch')
    assert 'Korean' in directive and 'action=buy/watch' in directive and 'JSON key' in directive
    client, _, _ = env
    assert client.put('/api/v1/settings/report', json={'language': 'ko'}).status_code == 200


def test_trade_csv_cost_fees_dedup_and_rejects_incomplete_ledger(db_path):
    from src.database.models import OverseasTrade, OverseasHolding
    from src.database.db import get_db_session
    service = OverseasPortfolioService(cfg(db_path))
    text = 'trade_id,trade_date,symbol,side,quantity,price,fee,currency\nb1,2026-09-01,AAPL,buy,10,100,2,USD\ns1,2026-09-02,AAPL,sell,4,120,1,USD'
    preview = service.import_csv(text, '交易账户', '2026-09-30')
    assert preview['ok'] and preview['rows'][0]['quantity'] == 6 and preview['saved'] == 0
    assert preview['rows'][0]['average_cost'] == pytest.approx(100.2)
    assert service.import_csv(text, '交易账户', '2026-09-30', preview=False)['saved'] == 2
    assert service.import_csv(text, '交易账户', '2026-09-30', preview=False)['duplicates'] == 2
    with pytest.raises(ValueError, match='卖出超过'):
        service.import_csv('trade_date,symbol,side,quantity,price\n2026-09-03,AAPL,sell,9,120', '交易账户', '2026-09-30', preview=False)
    with pytest.raises(ValueError, match='不同内容'):
        service.import_csv(text.replace('10,100', '10,101'), '交易账户', '2026-09-30', preview=False)
    with get_db_session(db_path) as session:
        assert session.query(OverseasTrade).count() == 2
        assert session.query(OverseasHolding).one().quantity == 6
    with pytest.raises(ValueError, match='已有成交账本'):
        service.import_csv('symbol,quantity\nAAPL,1', '交易账户', '2026-09-30', preview=False)


def test_overseas_outcome_uses_us_holiday_not_china_calendar(db_path):
    from src.database.db import get_db_session
    from src.services.outcome_engine import OutcomeEngine
    service = OverseasResearchService(cfg(db_path))
    rows = [{**bars()[0], 'trade_date': day, 'close': close, 'fetched_at': '2026-09-08T22:00:00+00:00'} for day, close in [('2026-09-04', 100), ('2026-09-08', 110)]]
    service.save_cache('overseas:AAPL', {'bars': rows})
    with get_db_session(db_path) as session:
        result = OutcomeEngine(cfg(db_path)).evaluate(session, 'diagnosis', 71, 'AAPL', '2026-09-04', 1,
            now=datetime(2026, 9, 9, 8, tzinfo=timezone.utc), horizons=(1,))[0]
        assert result.status == 'evaluated' and result.end_date == '2026-09-08' and result.return_pct == 10
        assert json.loads(result.data_quality_json)['calendar_verified']


def test_macro_api_preserves_validation_and_external_client_does_not_resubmit(env, monkeypatch):
    from integrations.assistant_client import ResearchClient
    client, _, _ = env
    response = client.post('/api/v1/integrations/macro/execute', json={'operation': 'health', 'arguments': {'base_url': 'untrusted'}})
    assert response.json()['status'] == 'invalid'
    requests = []
    class Client:
        def request(self, method, path, **kwargs):
            requests.append((method, path))
            return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {'status': 'done', 'result': {'action': 'watch'}})
    external = ResearchClient('http://unused', client=Client())
    assert external.wait('task')['action'] == 'watch' and requests == [('GET', 'tasks/task')]


def test_overseas_diagnosis_is_saved_and_has_structured_currency(db_path, monkeypatch, overseas_clock):
    from src.analyzers.llm_client import LLMClient
    service = OverseasResearchService(cfg(db_path))
    service.save_cache('overseas:AAPL', {'bars': bars()})
    monkeypatch.setattr(service, 'refresh', lambda code, **kwargs: service.snapshot(code))
    monkeypatch.setattr('src.services.social_sentiment.social_context', lambda *args: {'status': 'unavailable'})
    monkeypatch.setattr(LLMClient, '__init__', lambda self, config: None)
    monkeypatch.setattr(LLMClient, 'chat_json', lambda *args: {'score': 65, 'action': 'watch', 'one_sentence': '测试研究'})
    result = service.diagnose('AAPL', force=True)
    assert result['diagnosis_id'] and result['currency'] == 'USD' and result['research_only']
    assert result['structured_report']['subject']['currency'] == 'USD'


def test_overseas_intraday_cache_never_becomes_complete_after_close():
    quote = {**bars()[0], 'fetched_at': '2026-09-30T18:00:00+00:00'}
    quality = overseas_market.quote_quality(quote, 'US', datetime(2026, 9, 30, 23, tzinfo=timezone.utc))
    assert quality['status'] == 'partial' and 'incomplete_daily_bar' in quality['limitations']
    holiday = overseas_market.phase('US', datetime(2026, 9, 7, 18, tzinfo=timezone.utc))
    assert holiday['phase'] == 'non_trading_day' and holiday['effective_daily_bar_date'] == '2026-09-04'
    lunch = overseas_market.phase('HK', datetime(2026, 9, 30, 4, 30, tzinfo=timezone.utc))
    assert lunch['phase'] == 'midday_break' and lunch['is_partial_bar']


def test_overseas_symbols_never_enter_domestic_order_mapping():
    from src.trading.order_mapper import signal_to_order_intent
    for code in ['AAPL', '00700.HK', '005930.KS']:
        assert signal_to_order_intent(signal_id=1, signal_date='2026-09-30', code=code, name=code,
            side='buy', signal_type='research', signal_strength=.8, close_price=100) is None


def test_watchlist_and_direct_diagnosis_use_overseas_data(db_path, monkeypatch, overseas_clock):
    from src.services.watchlist import WatchlistService
    from src.services.stock_diagnosis import StockDiagnosisService
    config = cfg(db_path)
    research = OverseasResearchService(config)
    research.save_cache('overseas:AAPL', {'bars': bars()})
    watchlist = WatchlistService(config)
    assert watchlist.add('AAPL')['ok']
    row = next(row for row in watchlist.overview() if row['code'] == 'AAPL')
    assert row['currency'] == 'USD' and row['close'] == 200 and row['quote_quality']['status'] == 'available'
    calls = []
    monkeypatch.setattr(OverseasResearchService, 'diagnose', lambda self, code, force=False: calls.append((code, force)) or {'code': code})
    assert StockDiagnosisService(config).diagnose('US:AAPL', force=True)['code'] == 'AAPL'
    assert calls == [('AAPL', True)]


@pytest.mark.parametrize('modern', [False, True])
def test_longbridge_supports_both_packaged_sdk_factories(db_path, monkeypatch, modern):
    calls = []
    class Config:
        def __init__(self, key, secret, token):
            calls.append(('legacy', key, secret, token))
    if modern:
        Config.from_apikey = staticmethod(lambda *args: calls.append(('modern', *args)) or object())
    class Context:
        def __init__(self, config): pass
        def history_candlesticks_by_offset(self, **kwargs):
            calls.append(kwargs)
            return [SimpleNamespace(timestamp=datetime(2026, 9, 30, 20, tzinfo=timezone.utc),
                open=100, close=101, high=102, low=99, volume=1000)]
        def close(self): calls.append('closed')
    monkeypatch.setitem(sys.modules, 'longbridge.openapi', SimpleNamespace(Config=Config, QuoteContext=Context,
        Period=SimpleNamespace(Day='day'), AdjustType=SimpleNamespace(NoAdjust='raw')))
    config = {**cfg(db_path), 'integrations': {'longbridge': {'app_key': 'k', 'app_secret': 's', 'access_token': 't'}}}
    rows = OverseasResearchService(config)._longbridge(resolve_identity('AAPL'))
    assert rows[0]['close'] == 101 and rows[0]['currency'] == 'USD'
    assert calls[0][0] == ('modern' if modern else 'legacy')
    assert calls[1]['symbol'] == 'AAPL.US' and calls[1]['adjust_type'] == 'raw' and calls[-1] == 'closed'
