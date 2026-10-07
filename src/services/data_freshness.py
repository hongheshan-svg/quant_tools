"""日线时效契约：观测日期和取得时刻分开，历史查询不冒充实时行情。"""

from datetime import datetime
from zoneinfo import ZoneInfo

from src import trading_calendar
from src.services import market_phase


def iso_timestamp(value) -> str | None:
    """只接收明确的日期时间，日期和报告创建时间不能替代来源观测时刻。"""
    if isinstance(value, datetime):
        return value.isoformat()
    if not isinstance(value, str) or not any(c in value for c in ("T", " ")):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).isoformat()
    except ValueError:
        return None


def daily_quality(trade_date: str | None, fetched_at=None, *, phase: dict | None = None,
                  allow_partial: bool = False, historical: bool = False) -> dict:
    phase = phase if phase is not None else market_phase.current_phase()
    expected = phase.get("effective_daily_bar_date")
    result = {"status": "available", "trade_date": trade_date, "expected_date": expected,
              "fetched_at": iso_timestamp(fetched_at), "is_partial_bar": False, "limitations": []}
    if historical:
        result["mode"] = "historical"
        return result
    try:
        day = datetime.strptime(str(trade_date), "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return {**result, "status": "missing", "limitations": ["invalid_quote_date"]}
    # 无完整交易日上下文时保留未知边界，不以入库时间证明新鲜。
    if not expected:
        return result
    today = str(phase.get("now") or "")[:10]
    reasons = []
    if str(trade_date) > (today or expected) or not trading_calendar.is_trade_day(day):
        reasons.append("invalid_quote_date")
    elif str(trade_date) < expected:
        reasons.append("stale_quote")
    elif str(trade_date) > expected:
        result["is_partial_bar"] = True
        if not allow_partial or not phase.get("is_partial_bar"):
            reasons.append("incomplete_daily_bar")
    stamp = result["fetched_at"]
    if stamp and not result["is_partial_bar"] and str(trade_date) == expected:
        acquired = datetime.fromisoformat(stamp)
        if acquired.tzinfo:
            acquired = acquired.astimezone(ZoneInfo("Asia/Shanghai")).replace(tzinfo=None)
        # 用来源取得时刻校验，避免盘中日线在收盘后被当成完整日线复用。
        if acquired.strftime("%Y-%m-%d") == str(trade_date) and acquired.hour * 60 + acquired.minute < 15 * 60:
            reasons.append("cached_before_close")
    if reasons:
        result.update(status="stale", limitations=reasons)
    elif result["is_partial_bar"]:
        result.update(status="partial", limitations=["incomplete_daily_bar"])
    return result
