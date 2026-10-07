"""海外交易日历与行情质量，不使用 A 股时钟或工作日兜底证明完整性。"""
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
import math

MARKETS = {"US": ("XNYS", "America/New_York"), "HK": ("XHKG", "Asia/Hong_Kong"),
           "JP": ("XTKS", "Asia/Tokyo"), "KR": ("XKRX", "Asia/Seoul"), "TW": ("XTAI", "Asia/Taipei")}


def phase(market: str, now: datetime | None = None) -> dict:
    calendar_name, tz = MARKETS[market]
    now = now or datetime.now(timezone.utc)
    now = now.replace(tzinfo=ZoneInfo(tz)) if now.tzinfo is None else now.astimezone(ZoneInfo(tz))
    result = {"market": market, "timezone": tz, "now": now.isoformat(), "calendar_verified": False,
              "phase": "unknown", "effective_daily_bar_date": None, "is_partial_bar": False}
    try:
        import exchange_calendars as xcals
        import pandas as pd
        calendar = xcals.get_calendar(calendar_name)
        day = pd.Timestamp(now.date())
        session = calendar.date_to_session(day, direction="previous")
        opened, closed = calendar.session_open(session), calendar.session_close(session)
        stamp = pd.Timestamp(now)
        live = opened <= stamp < closed
        if stamp < closed:
            completed = calendar.previous_session(session)
        else:
            completed = session
        phase_name = "intraday" if live else "pre_market" if stamp < opened else "post_market"
        if live and calendar.is_break_minute(stamp.floor('min')):
            phase_name = "midday_break"
        elif session.date() != now.date():
            phase_name = "non_trading_day"
        return {**result, "calendar_verified": True, "phase": phase_name,
                "effective_daily_bar_date": completed.strftime("%Y-%m-%d"), "is_partial_bar": bool(live),
                "trade_date": session.strftime("%Y-%m-%d"), "market_close": closed.isoformat()}
    except Exception:
        return result


def quote_quality(quote: dict, market: str, now: datetime | None = None) -> dict:
    state = phase(market, now)
    reasons = []
    if not state["calendar_verified"]:
        reasons.append("calendar_unverified")
    expected = state["effective_daily_bar_date"]
    day = quote.get("trade_date")
    if not day:
        reasons.append("missing_quote")
    elif expected and day < expected:
        reasons.append("stale_quote")
    elif expected and day > expected:
        reasons.append("incomplete_daily_bar" if state["is_partial_bar"] and day == state["trade_date"] else "invalid_quote_date")
    value = quote.get("close")
    if not isinstance(value, (float, int)) or not math.isfinite(value) or value <= 0:
        reasons.append("invalid_price")
    try:
        fetched = datetime.fromisoformat(quote.get("fetched_at", ""))
        moment = datetime.fromisoformat(state["now"])
        if fetched.tzinfo is None or fetched > moment + timedelta(minutes=1):
            reasons.append("invalid_fetch_time")
        elif day and expected and day <= expected and state['calendar_verified']:
            import exchange_calendars as xcals
            import pandas as pd
            calendar = xcals.get_calendar(MARKETS[market][0])
            if not calendar.is_session(day):
                reasons.append("invalid_quote_date")
            elif pd.Timestamp(fetched) < calendar.session_close(day):
                reasons.append("incomplete_daily_bar")
    except (ValueError, TypeError):
        reasons.append("unknown_fetch_time")
    return {"status": "available" if not reasons else "partial", "limitations": reasons, "phase": state}
