"""多市场复盘：按交易所已完成交易日采集，地区隔离缓存，缺数据时保留质量标记。"""
from __future__ import annotations

from datetime import datetime, timezone
from dataclasses import asdict, is_dataclass
import json

from src.collectors.request_budget import bounded_call
from src.services.execution_budget import ExecutionBudget
from src.services.overseas_evidence import number
from src.services.overseas_market import MARKETS, phase
from src.services.overseas_research import OverseasResearchService
from src.services.report_language import language_directive, report_language

REGIONS = ('CN', 'HK', 'US', 'JP', 'KR', 'TW')
INDICES = {
    'HK': (('^HSI', '恒生指数'), ('^HSCE', '恒生中国企业指数')),
    'US': (('^DJI', 'Dow Jones'), ('^GSPC', 'S&P 500'), ('^IXIC', 'Nasdaq Composite')),
    'JP': (('^N225', 'Nikkei 225'), ('^TOPX', 'TOPIX')),
    'KR': (('^KS11', 'KOSPI'), ('^KQ11', 'KOSDAQ')),
    'TW': (('^TWII', '台湾加权指数'),),
}
NEWS_QUERIES = {'HK': '香港股市 恒生 收盘', 'US': 'US stock market Wall Street closing recap',
                'JP': '日本株 日経平均 TOPIX 市場概況', 'KR': '한국 증시 코스피 코스닥 마감', 'TW': '台股 加權指數 收盤'}
LABELS = {
    'zh': ('趋势结构', '市场情绪', '主要主题', '关注方向', '回避方向', '观察要点', '数据边界', '仅供研究，不构成投资建议。'),
    'en': ('Trend', 'Sentiment', 'Main themes', 'Focus', 'Avoid', 'Watch points', 'Data limitations', 'For research only; not investment advice.'),
    'ko': ('추세 구조', '시장 심리', '주요 테마', '관심 대상', '회피 대상', '관찰 포인트', '데이터 한계', '연구 목적이며 투자 조언이 아닙니다.'),
}


def regions(value=None) -> list[str]:
    """兼容上游 both 和逗号列表；默认 CN，未知值拒绝。"""
    if value is None:
        return ['CN']
    if isinstance(value, str):
        value = ['CN', 'HK', 'US', 'JP', 'KR'] if value.strip().lower() == 'both' else value.split(',')
    if not isinstance(value, list) or not value:
        raise ValueError('复盘地区必须是非空列表或逗号分隔字符串')
    result = list(dict.fromkeys(str(item).strip().upper() for item in value))
    if any(item not in REGIONS for item in result):
        raise ValueError('复盘地区仅支持 CN、HK、US、JP、KR、TW')
    return result


def fetch_index(symbol: str, region: str, expected: str) -> dict:
    import yfinance as yf
    frame = yf.Ticker(symbol).history(period='1mo', interval='1d', auto_adjust=False, timeout=8, raise_errors=True)
    rows = []
    for stamp, row in frame.iterrows():
        day = stamp.tz_convert(MARKETS[region][1]).date().isoformat() if stamp.tzinfo else stamp.date().isoformat()
        close = number(row.get('Close'))
        if day <= expected and close is not None and close > 0:
            rows.append((day, close))
    rows.sort()
    if not rows:
        return {'symbol': symbol, 'status': 'missing', 'close': None}
    day, close = rows[-1]
    return {'symbol': symbol, 'status': 'available' if day == expected else 'stale', 'trade_date': day, 'close': close,
            'change_pct': round((close / rows[-2][1] - 1) * 100, 3) if len(rows) > 1 else None,
            'unit': 'points', 'source': 'Yahoo Finance', 'fetched_at': datetime.now(timezone.utc).isoformat()}


class OverseasMarketReviewService:
    def __init__(self, config: dict, llm=None):
        self.config, self._llm = config, llm
        self.store = OverseasResearchService(config)

    def get(self, region: str, trade_date: str | None = None) -> dict | None:
        regions([region])
        language = report_language(self.config)
        result = self.store.cached(f'market-review:{region}:{language}:{trade_date or "latest"}')
        return result or None

    def generate(self, region: str, force: bool = False) -> dict:
        if region not in INDICES:
            raise ValueError('不支持的海外复盘地区')
        state = phase(region)
        day = state.get('effective_daily_bar_date')
        if not state['calendar_verified'] or not day:
            return {'region': region, 'error': '交易所日历不可用，无法确定已完成交易日'}
        if not force:
            existing = self.get(region, day)
            if (existing and all(row.get('status') == 'available' for row in existing.get('indices', []))
                    and existing.get('news_status') in ('available', 'not_configured')):
                return existing
        deadline = ExecutionBudget.from_config(self.config)
        def collect(item):
            symbol, name = item
            try:
                result = bounded_call(lambda: fetch_index(symbol, region, day), min(9., deadline.remaining()),
                                      quarantine_key=('market_review_index', symbol))
            except Exception:
                result = {'symbol': symbol, 'status': 'fetch_failed', 'close': None}
            return {**result, 'name': name}
        collected = deadline.parallel(collect, INDICES[region], seconds=15)
        by_symbol = {row['symbol']: row for row in collected}
        indices = [by_symbol.get(symbol, {'symbol': symbol, 'name': name, 'status': 'fetch_failed', 'close': None}) for symbol, name in INDICES[region]]
        if not any(row['status'] == 'available' for row in indices):
            return {'region': region, 'trade_date': day, 'indices': indices, 'error': '没有该交易日的有效指数行情，保留已有复盘'}
        from src.collectors.news_search import configured_providers, search
        news, news_status = [], 'not_configured'
        if configured_providers(self.config):
            try:
                news = bounded_call(lambda: search(NEWS_QUERIES[region], self.config, max_results=5, days=2),
                                    min(8., deadline.remaining()), quarantine_key=('market_review_news', region))
                news = [asdict(item) if is_dataclass(item) else item for item in news]
                news_status = 'available' if news else 'missing'
            except Exception:
                news_status = 'fetch_failed'
        missing = [row['symbol'] + ':' + row['status'] for row in indices if row['status'] != 'available']
        if news_status != 'available':
            missing.append('news:' + news_status)
        language = report_language(self.config)
        facts = {'region': region, 'trade_date': day, 'timezone': state['timezone'], 'indices': indices,
                 'news': news, 'limitations': missing,
                 'not_supported': ['A-share market breadth', 'northbound flow', 'limit-up statistics', 'sector capital flow']}
        prompt = ('只根据提供的地区、已完成交易日指数和新闻写复盘。指数单位是点，禁止虚构资金流、板块涨跌和市场宽度，'
                  '不使用其他市场规则或新闻代替本市场证据。新闻是采集时的背景，不能证明属于行情日期。'
                  '缺失和过期的数据必须披露。输出 JSON，字段 headline, trend, emotion, main_lines 为字符串，'
                  'focus, avoid, watch_points 为字符串数组。只给研究观察，不给具体买卖仓位。' + language_directive(language))
        if self._llm is None:
            from src.analyzers.llm_client import LLMClient
            self._llm = LLMClient(self.config.get('llm', {}))
        try:
            raw = self._llm.chat_json(user_message=json.dumps(facts, ensure_ascii=False, default=str), system_message=prompt)
        except Exception:
            raw = None
        if not isinstance(raw, dict) or not str(raw.get('headline') or '').strip():
            return {'region': region, 'trade_date': day, 'error': 'AI 未返回有效复盘，保留已有结果'}
        result = {key: str(raw.get(key) or '') for key in ('headline', 'trend', 'emotion', 'main_lines')}
        for key in ('focus', 'avoid', 'watch_points'):
            value = raw.get(key)
            result[key] = [str(x) for x in value if x] if isinstance(value, list) else []
        result.update(region=region, language=language, trade_date=day, created_at=datetime.now(timezone.utc).isoformat(),
                      stance='', position='', phase=state, indices=indices, news=news, news_status=news_status,
                      quality={'status': 'partial' if missing else 'available', 'limitations': missing}, research_only=True)
        result['markdown'] = render(result)
        self.store.save_cache(f'market-review:{region}:{language}:{day}', result)
        self.store.save_cache(f'market-review:{region}:{language}:latest', result)
        return result


def render(result: dict) -> str:
    labels = LABELS[result['language']]
    lines = [f"## {result['region']} · {result['trade_date']} · {result['headline']}"]
    for row in result['indices']:
        change = f"{row['change_pct']:+.2f}%" if row.get('change_pct') is not None else '—'
        close = f"{row['close']:,.2f}" if row.get('close') is not None else '—'
        lines.append(f"- {row['name']} ({row['symbol']}): {close} / {change} · {row.get('trade_date', '—')} · {row['status']} · {row.get('source', '—')}")
    for label, key in zip(labels, ('trend', 'emotion', 'main_lines', 'focus', 'avoid', 'watch_points')):
        value = result[key]
        if value:
            lines.append(f'**{label}**\n' + ('\n'.join('- ' + line for line in value) if isinstance(value, list) else value))
    if result['quality']['limitations']:
        lines.append(f"**{labels[6]}**: " + ', '.join(result['quality']['limitations']))
    lines.append(labels[7])
    return '\n\n'.join(lines)
