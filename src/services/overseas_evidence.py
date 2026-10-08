"""海外轻量基本面与新闻：只读采集、阶段预算、报告币种与交易币种分离。"""
from datetime import datetime, timezone
import math

from src.collectors.request_budget import bounded_call
from src.services.execution_budget import ExecutionBudget
from src.services.overseas_research import OverseasResearchService
from src.utils.stock_code import resolve_identity


def number(value):
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def fetch_part(code: str, part: str):
    import yfinance as yf
    ticker = str(int(code.split('.')[0])).zfill(4) + '.HK' if code.endswith('.HK') else code
    stock = yf.Ticker(ticker)
    if part == 'info':
        return stock.get_info()
    if part == 'statements':
        return stock.quarterly_income_stmt
    if part == 'dividends':
        return stock.dividends
    return stock.news


def financials(info, statements, dividends, identity, now):
    info = info if isinstance(info, dict) else {}
    financial_currency = str(info.get('financialCurrency') or '').upper() or None
    trading_currency = str(info.get('currency') or '').upper() or None
    limitations = ['lightweight_fundamentals', 'publication_dates_unverified']
    if trading_currency and trading_currency != identity.currency:
        limitations.append('trading_currency_mismatch')
    ratios = {field: number(info.get(key)) for field, key in {'pe_ttm': 'trailingPE', 'pe_forward': 'forwardPE', 'pb': 'priceToBook'}.items()}
    growth = {field: (number(info.get(key)) * 100 if number(info.get(key)) is not None else None)
              for field, key in {'revenue_yoy_pct': 'revenueGrowth', 'earnings_yoy_pct': 'earningsGrowth', 'gross_margin_pct': 'grossMargins'}.items()}
    statement = {'as_of': None, 'currency': financial_currency, 'revenue': None, 'net_profit': None, 'basic_eps': None, 'gross_profit': None}
    if statements is not None and hasattr(statements, 'columns'):
        columns = sorted([column for column in statements.columns if hasattr(column, 'date') and column.date() <= now.date()], reverse=True)
        if columns:
            column = columns[0]
            statement['as_of'] = column.date().isoformat()
            for field, names in {'revenue': ('Total Revenue', 'Operating Revenue'), 'net_profit': ('Net Income', 'Net Income Common Stockholders'),
                                 'basic_eps': ('Basic EPS', 'Diluted EPS'), 'gross_profit': ('Gross Profit',)}.items():
                for name in names:
                    if name in statements.index:
                        statement[field] = number(statements.loc[name, column])
                        if statement[field] is not None:
                            break
    if not financial_currency:
        limitations.append('unknown_financial_currency')
    events = []
    if dividends is not None and hasattr(dividends, 'items'):
        for stamp, value in dividends.items():
            cash = number(value)
            if hasattr(stamp, 'date') and stamp.date() <= now.date() and cash is not None and cash > 0:
                events.append({'ex_date': stamp.date().isoformat(), 'cash_per_share': cash, 'currency': trading_currency})
    missing = [field for field, value in {**ratios, **growth, **{k: statement[k] for k in ('revenue', 'net_profit', 'basic_eps')}}.items() if value is None]
    meaningful = any(value is not None for value in ratios.values()) or statement['revenue'] is not None or bool(events)
    return {'status': 'partial' if meaningful else 'missing', 'source': 'Yahoo Finance', 'as_of': statement['as_of'],
            'fetched_at': now.isoformat(), 'profile': {key: str(info.get(key) or '')[:200] for key in ('longName', 'sector', 'industry', 'country')},
            'valuation': {**ratios, 'market_cap': number(info.get('marketCap')) if trading_currency == identity.currency else None,
                          'currency': trading_currency, 'observed_at': now.isoformat()},
            'growth': growth, 'financial_report': statement,
            'dividends': sorted(events, key=lambda e: e['ex_date'], reverse=True)[:12],
            'missing_fields': missing, 'limitations': limitations}


def news_rows(payload, now):
    from urllib.parse import urlsplit
    items = []
    for row in payload if isinstance(payload, list) else []:
        content = row.get('content', row) if isinstance(row, dict) else {}
        if not isinstance(content, dict):
            continue
        title = str(content.get('title') or '').strip()[:300]
        source = content.get('provider') or {}
        source = source.get('displayName', '') if isinstance(source, dict) else str(source)
        stamp = content.get('pubDate') or row.get('providerPublishTime')
        try:
            published = datetime.fromtimestamp(stamp, timezone.utc) if isinstance(stamp, (int, float)) else datetime.fromisoformat(str(stamp).replace('Z', '+00:00'))
            if published.tzinfo is None or published > now:
                continue
        except (ValueError, TypeError, OSError):
            continue
        url = (content.get('canonicalUrl') or {}).get('url', '') if isinstance(content.get('canonicalUrl'), dict) else ''
        url = url or content.get('link') or row.get('link') or ''
        try:
            parsed = urlsplit(str(url))
        except ValueError:
            continue
        if not title or parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
            continue
        items.append({'title': title, 'source': source or content.get('publisher') or 'Yahoo Finance',
                      'date': published.isoformat(), 'url': url,
                      'summary': str(content.get('summary') or content.get('description') or '')[:1200]})
    return sorted(items, key=lambda item: item['date'], reverse=True)[:10]


class OverseasEvidenceService:
    def __init__(self, config):
        self.config = config
        self.store = OverseasResearchService(config)

    def refresh(self, code: str) -> dict:
        identity = resolve_identity(code)
        if identity.market == 'CN':
            raise ValueError('海外证据入口不接受 A 股')
        deadline = ExecutionBudget.from_config(self.config)
        seconds = min(20., float((self.config.get('data_sources') or {}).get('stage_timeout_seconds', 20)))
        def fetch(part):
            try:
                if part == 'futu':
                    from src.services.futu_evidence import fetch as fetch_futu
                    value = bounded_call(lambda: fetch_futu(self.config, identity.code), min(12., deadline.remaining()),
                                         quarantine_key=('overseas_evidence', 'futu'))
                elif part == 'institution':
                    from src.collectors.tw_institutional import fetch as fetch_institution
                    value = bounded_call(lambda: fetch_institution(identity.code), min(10., deadline.remaining()),
                                         quarantine_key=('overseas_institution', identity.code.rsplit('.', 1)[-1]))
                else:
                    value = bounded_call(lambda: fetch_part(identity.code, part), min(8., deadline.remaining()),
                                     quarantine_key=('overseas_evidence', part))
                return part, value, None
            except Exception:
                return part, None, 'fetch_failed'
        parts = ['info', 'statements', 'dividends', 'news']
        if identity.market == 'TW':
            parts.append('institution')
        if identity.market == 'HK' and ((self.config.get('integrations') or {}).get('futu') or {}).get('fundamentals_enabled'):
            parts.append('futu')
        values = {key: (value, error) for key, value, error in deadline.parallel(fetch, parts, seconds=seconds)}
        now = datetime.now(timezone.utc)
        result = financials(*(values.get(key, (None, None))[0] for key in ('info', 'statements', 'dividends')), identity, now)
        failed = [key for key in ('info', 'statements', 'dividends') if key not in values or values[key][1]]
        result['failed_blocks'] = failed
        if failed:
            result['limitations'].extend('fetch_failed:' + key for key in failed)
            if result['status'] == 'missing': result['status'] = 'fetch_failed'
        if 'futu' in parts:
            if values.get('futu', (None, 'fetch_failed'))[0]:
                from src.services.futu_evidence import merge
                result = merge(result, values['futu'][0])
            else:
                result['failed_blocks'].append('futu')
                result['limitations'].append('fetch_failed:futu')
        news = news_rows(values.get('news', (None, None))[0], now)
        news_failed = 'news' not in values or bool(values['news'][1])
        evidence = {'fundamentals': result, 'news': {'status': 'fetch_failed' if news_failed else 'available' if news else 'missing',
                    'source': 'Yahoo Finance', 'items': news, 'fetched_at': now.isoformat()}}
        previous = self.cached(identity.code)
        if identity.market == 'TW':
            institution = values.get('institution', (None, 'fetch_failed'))[0]
            evidence['institution'] = institution or {'status': 'fetch_failed', 'limitations': ['source_unavailable']}
        for key, block in evidence.items():
            if (block['status'] == 'fetch_failed' or key == 'institution' and block['status'] == 'missing') and previous.get(key):
                evidence[key] = {**previous[key], 'refresh_status': block['status']}
        self.store.save_cache('overseas:evidence:' + identity.code, evidence)
        return evidence

    def cached(self, code: str) -> dict:
        result = self.store.cached('overseas:evidence:' + code)
        if result.get('institution'):
            from src.collectors.tw_institutional import quality
            from src.services.overseas_market import phase
            result['institution'] = quality(result['institution'], phase('TW')['effective_daily_bar_date'])
        for key, hours in [('fundamentals', 24), ('news', 6)]:
            block = result.get(key)
            if not block:
                continue
            try:
                stamp = datetime.fromisoformat(block['fetched_at'])
                age = (datetime.now(timezone.utc) - stamp).total_seconds()
                if age < 0 or age > hours * 3600:
                    block['status'] = 'stale'
            except (KeyError, ValueError, TypeError):
                block['status'] = 'partial'
        return result
