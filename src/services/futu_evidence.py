"""Futu OpenD 港股只读基本面。保留报告币种、来源，Yahoo 只补同口径缺项。"""
from copy import deepcopy
from datetime import datetime, timezone
import re

from src.services.overseas_evidence import number


def fetch(config: dict, code: str) -> dict:
    cfg = (config.get('integrations') or {}).get('futu') or {}
    host = cfg.get('host', '127.0.0.1')
    if host not in ('127.0.0.1', 'localhost', '::1'):
        raise ValueError('Futu OpenD 只允许本机连接')
    from futu import OpenQuoteContext, RET_OK
    context = OpenQuoteContext(host=host, port=int(cfg.get('port', 11111)))
    symbol = 'HK.' + code.split('.')[0].zfill(5)
    payload, failed = {}, []
    operations = {
        'profile': lambda: context.get_company_profile(symbol),
        'income': lambda: context.get_financials_statements(symbol, statement_type=1, num=8),
        'dividends': lambda: context.get_corporate_actions_dividends(symbol),
        'valuation': lambda: context.get_market_snapshot([symbol]),
    }
    try:
        for key, operation in operations.items():
            try:
                status, value = operation()
                if status != RET_OK:
                    failed.append(key)
                else:
                    payload[key] = value
            except Exception:
                failed.append(key)
    finally:
        context.close()
    payload['failed_blocks'] = failed
    return normalize(payload)


def currency(value) -> str | None:
    text = str(value or '').strip().upper()
    return text if re.fullmatch(r'[A-Z]{3}', text) else None


def normalize(payload: dict, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    profile = payload.get('profile')
    names = {str(row.get('name')): str(row.get('value') or '') for _, row in profile.iterrows()} if hasattr(profile, 'iterrows') else {}
    output = {'profile': {key: next((names[n] for n in options if names.get(n)), '') for key, options in
                         {'longName': ('公司名称', 'Company Name'), 'sector': ('所属行业', 'Industry'), 'country': ('所属国家', 'Country')}.items()},
              'fetched_at': now.isoformat(), 'source': 'Futu OpenD', 'failed_blocks': payload.get('failed_blocks', [])}
    reports = (payload.get('income') or {}).get('report_list', [])
    reports = [r for r in reports if isinstance(r, dict) and re.fullmatch(r'\d{4}-\d{2}-\d{2}', str(r.get('date_time_str', ''))[:10])
               and str(r['date_time_str'])[:10] <= now.date().isoformat()]
    if reports:
        latest = max(reports, key=lambda r: str(r['date_time_str']))
        items = {item.get('display_name'): item for item in latest.get('item_list', []) if isinstance(item, dict)}
        report = {'as_of': str(latest['date_time_str'])[:10], 'currency': currency(latest.get('currency_code') or latest.get('currency_info'))}
        growth = {}
        fields = {'revenue': ('营业总收入', '营业额'), 'net_profit': ('归属母公司净利润', '归属普通股股东净利润', '净利润'),
                  'basic_eps': ('基本每股收益',), 'gross_profit': ('毛利',)}
        for key, options in fields.items():
            item = next((items[name] for name in options if name in items), {})
            report[key] = number(item.get('data'))
            if key in ('revenue', 'net_profit'):
                growth['revenue_yoy_pct' if key == 'revenue' else 'earnings_yoy_pct'] = number(item.get('yoy'))
        growth['gross_margin_pct'] = report['gross_profit'] / report['revenue'] * 100 if report['gross_profit'] is not None and report['revenue'] else None
        output.update(financial_report=report, growth=growth)
    valuation = payload.get('valuation')
    if hasattr(valuation, 'empty') and not valuation.empty:
        row = valuation.iloc[0]
        output['valuation'] = {'pe_ttm': number(row.get('pe_ttm_ratio')), 'pb': number(row.get('pb_ratio'))}
    events = []
    for event in (payload.get('dividends') or {}).get('dividend_list', []):
        day = str(event.get('ex_date') or event.get('ex_dividend_date') or '')[:10]
        cash = number(event.get('dividend_per_share'))
        if re.fullmatch(r'\d{4}-\d{2}-\d{2}', day) and day <= now.date().isoformat() and cash is not None and cash > 0:
            events.append({'ex_date': day, 'cash_per_share': cash, 'currency': currency(event.get('currency'))})
    output['dividends'] = sorted(events, key=lambda event: event['ex_date'], reverse=True)[:12]
    return output


def merge(yahoo: dict, futu: dict) -> dict:
    result = deepcopy(yahoo)
    sources = {f'{block}.{key}': 'Yahoo Finance' for block in ('profile', 'valuation', 'financial_report', 'growth')
               for key, value in (yahoo.get(block) or {}).items() if value is not None and value != ''}
    changed = False
    for block in ('profile', 'valuation'):
        for key, value in (futu.get(block) or {}).items():
            if value is not None and value != '':
                result[block][key] = value
                sources[f'{block}.{key}'] = 'Futu OpenD'
                changed = True
    report = futu.get('financial_report') or {}
    old = yahoo.get('financial_report') or {}
    if report.get('as_of') and (not old.get('as_of') or report['as_of'] >= old['as_of']) and any(report.get(key) is not None for key in ('revenue', 'net_profit', 'basic_eps')):
        same = report.get('currency') and report.get('currency') == old.get('currency') and report['as_of'] == old.get('as_of')
        result['financial_report'] = {key: (old.get(key) if value is None and same else value) for key, value in report.items()}
        for key, value in report.items():
            if value is not None:
                sources['financial_report.' + key] = 'Futu OpenD'
            elif not same:
                sources.pop('financial_report.' + key, None)
        # 不混用不同报告期的同比指标。
        result['growth'] = dict(futu.get('growth') or {})
        for key, value in result['growth'].items():
            sources.pop('growth.' + key, None)
            if value is not None: sources['growth.' + key] = 'Futu OpenD'
        changed = True
    if futu.get('dividends'):
        result['dividends'] = futu['dividends']
        sources['dividends'] = 'Futu OpenD'
        changed = True
    result['failed_blocks'] = list(result.get('failed_blocks', [])) + ['futu:' + key for key in futu.get('failed_blocks', [])]
    if changed:
        result.update(source='Futu OpenD / Yahoo Finance', status='partial', field_sources=sources)
        result['as_of'] = result['financial_report'].get('as_of')
        metrics = {**result['valuation'], **result['growth'], **{key: result['financial_report'].get(key) for key in ('revenue', 'net_profit', 'basic_eps')}}
        result['missing_fields'] = [key for key, value in metrics.items() if value is None]
        if result['financial_report'].get('currency'):
            result['limitations'] = [x for x in result['limitations'] if x != 'unknown_financial_currency']
        elif 'unknown_financial_currency' not in result['limitations']:
            result['limitations'].append('unknown_financial_currency')
    return result
