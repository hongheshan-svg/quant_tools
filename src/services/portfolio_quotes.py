"""国内持仓现价：个股与 ETF 使用各自行情表，有限值与时效独立于成本估值。"""
import math
from datetime import datetime
from zoneinfo import ZoneInfo
from src.database.models import StockDaily, FundDaily
from src.utils.stock_code import bare_code, code_candidates


def latest_quote(session, code):
    bare = bare_code(code)
    model = FundDaily if len(bare) == 6 and bare[0] in '15' else StockDaily
    rows = session.query(model).filter(model.code.in_(code_candidates(code)), model.close > 0).order_by(model.trade_date.desc()).limit(5).all()
    return next((row for row in rows if math.isfinite(row.close)), None)


def price_quality(day, fetched_at, phase=None):
    """盘中未收盘日线可以提供现价；不把它用于完整日线策略或回撤。"""
    from src.services.data_freshness import daily_quality
    from src.services.market_phase import current_phase
    phase = phase if phase is not None else current_phase()
    quality = daily_quality(day, fetched_at, phase=phase, allow_partial=True)
    if not phase.get('effective_daily_bar_date') or not phase.get('is_partial_bar'):
        return quality
    if day != str(phase.get('now') or '')[:10]:
        return {**quality, 'status': 'stale', 'limitations': ['quote_not_from_current_session']}
    if quality['status'] != 'partial': return quality
    if not quality['fetched_at']:
        return {**quality, 'status': 'stale', 'limitations': ['unknown_quote_fetch_time']}
    now = datetime.fromisoformat(phase['now'])
    fetched = datetime.fromisoformat(quality['fetched_at'])
    if now.tzinfo: now = now.astimezone(ZoneInfo('Asia/Shanghai')).replace(tzinfo=None)
    if fetched.tzinfo: fetched = fetched.astimezone(ZoneInfo('Asia/Shanghai')).replace(tzinfo=None)
    age = (now - fetched).total_seconds()
    future = age < -60
    # 午休允许使用上午收盘快照，也允许午休内重新取得快照；未来抓取时刻仍无效。
    if (11, 30) <= (now.hour, now.minute) < (13, 0):
        last_trade = now.replace(hour=11, minute=30, second=0, microsecond=0)
        age = max(0, (last_trade - fetched).total_seconds())
    if age > 1800 or future:
        return {**quality, 'status': 'stale', 'limitations': ['stale_intraday_quote']}
    return {**quality, 'status': 'available', 'mode': 'intraday_quote', 'limitations': ['partial_bar_used_as_quote']}
