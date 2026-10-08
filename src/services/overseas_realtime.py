"""海外近实时快照单独缓存，供应商时间与采集时间分离，不混作完整日线。"""
from datetime import datetime, timezone

from src.services.overseas_evidence import number
from src.utils.stock_code import resolve_identity


def fetch_quote(code: str) -> dict:
    import yfinance as yf
    ticker = str(int(code.split('.')[0])).zfill(4) + '.HK' if code.endswith('.HK') else code
    return yf.Ticker(ticker).get_info()


def normalize(info: dict, code: str, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    identity = resolve_identity(code)
    timestamp = number(info.get('regularMarketTime'))
    try:
        stamp = datetime.fromtimestamp(timestamp, timezone.utc) if timestamp is not None else None
    except (ValueError, OSError, OverflowError):
        stamp = None
    price = number(info.get('regularMarketPrice'))
    price = price if price is not None and price > 0 else None
    return quality({'code': identity.code, 'price': price, 'currency': str(info.get('currency') or '').upper(),
                    'expected_currency': identity.currency, 'change_pct': number(info.get('regularMarketChangePercent')),
                    'provider_timestamp': stamp.isoformat() if stamp else None, 'fetched_at': now.isoformat(),
                    'source': 'Yahoo Finance', 'delay_minutes': number(info.get('exchangeDataDelayedBy')),
                    'regular_session_only': True}, now)


def quality(quote: dict, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    reasons = []
    if number(quote.get('price')) is None or quote['price'] <= 0:
        reasons.append('missing_price')
    if quote.get('currency') != quote.get('expected_currency') or not quote.get('currency'):
        reasons.append('currency_unverified')
    age = None
    try:
        stamp = datetime.fromisoformat(quote['provider_timestamp'])
        if stamp.tzinfo is None:
            raise ValueError('naive timestamp')
        age = (now - stamp).total_seconds()
        if age < -60:
            reasons.append('future_timestamp')
        elif age > 15 * 60:
            reasons.append('stale_quote')
    except (ValueError, TypeError, KeyError):
        reasons.append('unknown_provider_time')
    return {**quote, 'age_seconds': age, 'status': 'partial' if reasons else 'available', 'limitations': reasons,
            'price': None if 'currency_unverified' in reasons else quote.get('price'),
            'is_realtime_verified': False}  # 免费源可能有延时；available 只表示有效且在时效窗口内
