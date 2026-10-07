"""SQLite 行情取得时刻统一用上海墙上时间，与 A 股收盘边界一致。"""
from datetime import datetime
from zoneinfo import ZoneInfo


def quote_now() -> datetime:
    return datetime.now(ZoneInfo('Asia/Shanghai')).replace(tzinfo=None)
