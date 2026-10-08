"""本轮页面验收数据；只由专用隔离测试服务显式加载，所有外部调用用本地替身。"""
from datetime import datetime, timezone


def seed(app):
    from src.services import macro_tools, codex_runtime, social_sentiment
    from src.services.overseas_research import OverseasResearchService
    from src.services.overseas_market import phase, MARKETS
    from src.analyzers.llm_client import LLMClient
    from src.services.overseas_portfolio import OverseasPortfolioService
    import exchange_calendars as xcals

    def bars(identity):
        day = phase(identity.market)['effective_daily_bar_date']
        calendar = xcals.get_calendar(MARKETS[identity.market][0])
        sessions = calendar.sessions_window(day, -70)
        return [{'code': identity.code, 'name': identity.code, 'trade_date': stamp.strftime('%Y-%m-%d'),
                 'open': 190 + i, 'close': 192 + i, 'high': 194 + i, 'low': 189 + i, 'volume': 100000,
                 'source': '隔离验收行情', 'currency': identity.currency, 'price_adjustment': 'raw',
                 'fetched_at': datetime.now(timezone.utc).isoformat()} for i, stamp in enumerate(sessions)]

    OverseasResearchService._yahoo = lambda self, identity: bars(identity)
    OverseasResearchService._longbridge = lambda self, identity: []
    from src.services import overseas_evidence
    import pandas as pd
    def evidence(code, part):
        if part == 'info':
            return {'longName': '隔离验收公司', 'currency': 'USD', 'financialCurrency': 'USD', 'trailingPE': 25, 'priceToBook': 3}
        if part == 'statements':
            return pd.DataFrame({pd.Timestamp('2026-06-30'): [1000000, 200000]}, index=['Total Revenue', 'Net Income'])
        if part == 'dividends':
            return pd.Series(dtype=float)
        return [{'title': '隔离验收公司财报', 'providerPublishTime': datetime.now(timezone.utc).timestamp() - 60,
                 'publisher': '验收新闻源', 'link': 'https://example.com/report'}]
    overseas_evidence.fetch_part = evidence
    from src.services import overseas_market_review
    overseas_market_review.fetch_index = lambda symbol, region, expected: {
        'symbol': symbol, 'status': 'available', 'trade_date': expected, 'close': 31200, 'change_pct': 1.25,
        'unit': 'points', 'source': '隔离验收指数', 'fetched_at': datetime.now(timezone.utc).isoformat()}
    service = OverseasResearchService(app.state.pipeline.config)
    original_fx = OverseasResearchService.fx_rate

    def fx(self, currency, *, refresh=False):
        if refresh:
            self.save_cache('overseas:fx:' + currency, {'rate': 7.1, 'currency': currency,
                'as_of': datetime.now(timezone.utc).date().isoformat(), 'source': '隔离验收汇率'})
        return original_fx(self, currency)

    OverseasResearchService.fx_rate = fx
    service.refresh('AAPL')
    service.refresh('HK00700')
    LLMClient.chat_json = lambda *args, **kwargs: {'score': 64, 'action': 'watch', 'confidence': '低',
        'headline': '隔离验收区域复盘', 'trend': '指数按各自市场交易日统计。', 'emotion': '保持观察', 'main_lines': '市场证据待跟踪',
        'focus': ['下一交易日指数变化'], 'avoid': [], 'watch_points': ['公开新闻与波动变化'],
        'one_sentence': '隔离验收研究：使用原币行情，缺少新闻与财务证据。', 'core_logic': '合成行情用于界面验证。'}
    macro_tools.execute = lambda config, name, arguments: {'status': 'available', 'operation': name,
        'source': '隔离验收宏观服务', 'fetched_at': datetime.now(timezone.utc).isoformat(), 'arguments': arguments}
    social_sentiment.social_context = lambda *args: {'status': 'partial', 'source': '隔离验收 Adanos',
        'channels': {'reddit': {'status': 'available'}, 'x': {'status': 'fetch_failed'}, 'polymarket': {'status': 'unavailable'}}}
    OverseasPortfolioService.futu_symbols = lambda self: {'codes': ['AAPL', '00700.HK'], 'source': '隔离验收 Futu'}

    class Runtime:
        def __init__(self, config, folder, event):
            self.event, self.calls = event, [{'name': 'watchlist', 'success': True}]
        def run(self, question, history):
            if '取消' in question:
                for _ in range(100):
                    if self.event.wait(.1):
                        raise RuntimeError('本地模型会话已取消')
            return f'隔离验收本地回答，已恢复 {len(history)} 条历史消息。'
        def close(self):
            pass
    codex_runtime.AppServer = Runtime
