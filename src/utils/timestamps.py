"""SQLite 行情取得时刻统一用上海墙上时间，与 A 股收盘边界一致。"""
from datetime import datetime
from zoneinfo import ZoneInfo


def market_time(value: datetime | None = None, timezone: str = 'Asia/Shanghai') -> datetime:
    """市场墙上时间；无时区旧输入按市场时间解释，有时区输入转换后再去时区。"""
    zone = ZoneInfo(timezone)
    value = value or datetime.now(zone)
    return value.astimezone(zone).replace(tzinfo=None) if value.tzinfo else value


def quote_now() -> datetime:
    return market_time()
