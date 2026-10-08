"""多市场复盘离线验收：时区/币种、地区缓存、失败隔离、日历和 API。"""
from datetime import datetime, timezone
from types import SimpleNamespace
import json

import pandas as pd
import pytest

from tests.test_api import env, _wait  # noqa: F401
from tests.test_decision_signals import db_path  # noqa: F401
from src.services import overseas_market_review as module
from src.services.market_review import MarketReviewService
from src.collectors.news_search import SearchResult


def state(region):
    return {'market': region, 'calendar_verified': True, 'phase': 'post_market', 'effective_daily_bar_date': '2026-10-07',
            'trade_date': '2026-10-07', 'timezone': module.MARKETS[region][1], 'now': '2026-10-08T00:00:00+00:00'}


def index(symbol, region, expected):
    return {'symbol': symbol, 'status': 'available', 'trade_date': expected, 'close': 300., 'change_pct': 1.2,
            'unit': 'points', 'source': 'test', 'fetched_at': datetime.now(timezone.utc).isoformat()}


@pytest.fixture
def configured(db_path, monkeypatch):
    monkeypatch.setattr(module, 'phase', state)
    monkeypatch.setattr(module, 'fetch_index', index)
    monkeypatch.setattr('src.collectors.news_search.configured_providers', lambda config: ['test'])
    monkeypatch.setattr('src.collectors.news_search.search', lambda *a, **k: [SearchResult('区域新闻', 'https://example.com', source='测试新闻源', published='2026-10-07')])
    return {'database': {'sqlite_path': db_path}, 'report': {'language': 'ko'}}


class Model:
    def __init__(self): self.calls = []
    def chat_json(self, **kwargs):
        self.calls.append(kwargs)
        return {'headline': '시장 관찰', 'trend': '추세 확인', 'emotion': '관망', 'main_lines': '시장 뉴스', 'focus': ['지수'], 'avoid': [], 'watch_points': ['변동성']}


def test_regions_support_legacy_both_and_validate_unknown():
    assert module.regions('both') == ['CN', 'HK', 'US', 'JP', 'KR']
    assert module.regions('jp,kr,JP') == ['JP', 'KR']
    for value in ([], '', ['XX'], 1):
        with pytest.raises(ValueError): module.regions(value)


def test_regional_cache_isolates_markets_dates_and_language(configured):
    llm = Model()
    service = MarketReviewService(configured, llm)
    jp, kr = service.generate(region='JP'), service.generate(region='KR')
    assert [r['symbol'] for r in jp['indices']] == ['^N225', '^TOPX']
    assert [r['symbol'] for r in kr['indices']] == ['^KS11', '^KQ11']
    assert jp['region'] == service.get(region='JP')['region'] == 'JP'
    assert '추세 구조' in jp['markdown'] and '연구 목적' in jp['markdown']
    assert 'Korean' in llm.calls[0]['system_message']
    assert jp['quality']['status'] == 'available' and jp['stance'] == ''
    assert jp['news'][0]['source'] == '测试新闻源'
    facts = json.loads(llm.calls[0]['user_message'])
    assert facts['timezone'] == 'Asia/Tokyo' and facts['not_supported']
    service.generate(region='JP')
    assert len(llm.calls) == 2
    assert MarketReviewService({**configured, 'report': {'language': 'en'}}).get(region='JP') is None
    assert service.get('2026-10-06', region='JP') is None
    assert service.get() is None  # 未写入 A 股 MarketReview 表


def test_index_partial_failure_continues_and_records_quality(configured, monkeypatch):
    def partial(symbol, region, expected):
        if symbol == '^TOPX': raise TimeoutError('provider-token-must-not-leak')
        return index(symbol, region, expected)
    monkeypatch.setattr(module, 'fetch_index', partial)
    result = MarketReviewService(configured, Model()).generate(region='JP')
    assert result['quality']['status'] == 'partial' and '^TOPX:fetch_failed' in result['quality']['limitations']
    assert 'provider-token-must-not-leak' not in str(result)
    assert result['indices'][0]['close'] == 300.


def test_all_failed_or_stale_data_does_not_overwrite_previous_review(configured, monkeypatch):
    llm = Model()
    service = MarketReviewService(configured, llm)
    old = service.generate(region='US')
    monkeypatch.setattr(module, 'fetch_index', lambda symbol, *args: {'symbol':symbol, 'status':'stale', 'close':300., 'trade_date':'2020-01-01'})
    result = service.generate(region='US', force=True)
    assert result['error'] and len(llm.calls) == 1
    assert service.get(region='US') == old
    monkeypatch.setattr(module, 'phase', lambda region: {**state(region), 'calendar_verified':False})
    assert '日历' in service.generate(region='US')['error']


def test_yahoo_indices_use_local_date_and_completed_bar(monkeypatch):
    frame = pd.DataFrame({'Close':[90, 100, 110, 9000]}, index=pd.DatetimeIndex(['2026-10-05T22:00:00Z', '2026-10-06T22:00:00Z', '2026-10-07T22:00:00Z', '2026-10-08T22:00:00Z']))
    monkeypatch.setattr('yfinance.Ticker', lambda symbol: SimpleNamespace(history=lambda **kwargs: frame))
    result = module.fetch_index('^N225', 'JP', '2026-10-07')
    assert result['trade_date'] == '2026-10-07' and result['close'] == 100 and result['change_pct'] == pytest.approx(11.111)
    assert result['unit'] == 'points' and 'currency' not in result


def test_api_region_settings_generation_and_validation(env, monkeypatch):
    client, app, config = env
    monkeypatch.setattr(module, 'phase', state)
    monkeypatch.setattr(module, 'fetch_index', index)
    monkeypatch.setattr('src.collectors.news_search.configured_providers', lambda config: [])
    monkeypatch.setattr('src.analyzers.llm_client.LLMClient.chat_json', Model().chat_json)
    assert client.get('/api/v1/settings/market-review').json()['regions'] == ['CN']
    response = client.put('/api/v1/settings/market-review', json={'enabled':True, 'regions':['jp','kr']})
    assert response.status_code == 200 and response.json()['regions'] == ['JP', 'KR']
    assert app.state.pipeline.config['market_review']['regions'] == 'jp,kr'
    assert client.put('/api/v1/settings/market-review', json={'enabled':True,'regions':[]}).status_code == 422
    assert client.get('/api/v1/market/review?region=EU').status_code == 422
    task = client.post('/api/v1/market/review?region=JP').json()
    result = _wait(client, task)
    assert result['status'] == 'done' and result['result']['region'] == 'JP'
    assert client.get('/api/v1/market/review?region=JP').json()['region'] == 'JP'
    assert client.get('/api/v1/market/review?region=KR').json() is None
    assert client.get('/api/v1/market/review').json() is None
    from src.services import report_image
    captured = []
    monkeypatch.setattr(report_image, 'render_markdown_image', lambda title, text, **kw: captured.append((title,text)) or b'png')
    assert client.get('/api/v1/market/review/image?region=JP').status_code == 200
    assert 'JP' in captured[0][0] and '^N225' in captured[0][1]


def test_scheduler_cn_holiday_keeps_foreign_markets_independent(monkeypatch):
    from src import scheduler
    monkeypatch.setattr(scheduler, '_skip_non_trade_day', lambda *args: True)
    monkeypatch.setattr('src.services.overseas_market.phase', state)
    calls, pushed = [], []
    def generate(self, region='CN'):
        calls.append(region)
        if region == 'US': raise TimeoutError('test')
        return {'region':region, 'markdown':'区域复盘 ' + region}
    monkeypatch.setattr(MarketReviewService, 'generate', generate)
    monkeypatch.setattr('src.notifier.broadcast', lambda *args, **kw: pushed.append(args))
    scheduler._run_daily_report({'market_review':{'regions':'cn,us,jp,kr'}})
    assert calls == ['US','JP','KR'] and len(pushed) == 1
    assert 'JP' in pushed[0][2] and 'KR' in pushed[0][2]
    calls.clear()
    monkeypatch.setattr('src.services.overseas_market.phase', lambda region: {**state(region), 'phase':'non_trading_day'})
    scheduler._run_daily_report({'market_review':{'regions':'jp'}})
    assert not calls


def test_config_validation_accepts_lists_and_rejects_invalid_region():
    from src.services.config_check import check_config
    good = {'market_review':{'enabled':True,'regions':['jp','kr']}}
    assert not any(i['path'].startswith('market_review') for i in check_config(good, raw=good)['issues'])
    bad = {'market_review':{'regions':'cn,moon'}}
    assert any(i['path'] == 'market_review.regions' and i['level'] == 'error' for i in check_config(bad, raw=bad)['issues'])
