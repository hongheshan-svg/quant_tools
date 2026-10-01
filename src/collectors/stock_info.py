"""
股票基础信息采集器
数据来源：上交所、深交所、北交所官方股票列表（通过 AKShare）
提供股票池过滤所需的上市日期
"""

from datetime import date, datetime, timedelta
from typing import Any

import akshare as ak
from loguru import logger
from sqlalchemy import func

from src.collectors.base import BaseCollector
from src.database.db import get_db_session
from src.database.models import StockInfo
from src.utils.stock_code import normalize_name

STOCK_CODE_LENGTH = 6
DATE_DIGITS_LENGTH = 8
DEFAULT_MAX_AGE_HOURS = 24


def _normalize_date(value: Any) -> str | None:
    """把 date / 'YYYY-MM-DD' / 'YYYYMMDD' 统一为 'YYYY-MM-DD'，无法识别时返回 None。"""
    if isinstance(value, date):
        return value.strftime("%Y-%m-%d")
    digits = str(value or "").strip().replace("-", "")
    if len(digits) == DATE_DIGITS_LENGTH and digits.isdigit():
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:]}"
    return None


class StockInfoCollector(BaseCollector):
    """股票基础信息采集器（代码、简称、上市日期）"""

    SOURCE_NAME = "stock_info"

    # (交易所, AKShare 函数名, 参数, 代码列, 简称列, 上市日期列)
    SOURCES = (
        ("sh", "stock_info_sh_name_code", {"symbol": "主板A股"}, "证券代码", "证券简称", "上市日期"),
        ("sh", "stock_info_sh_name_code", {"symbol": "科创板"}, "证券代码", "证券简称", "上市日期"),
        ("sz", "stock_info_sz_name_code", {"symbol": "A股列表"}, "A股代码", "A股简称", "A股上市日期"),
        ("bj", "stock_info_bj_name_code", {}, "证券代码", "证券简称", "上市日期"),
    )

    def collect(self) -> list[dict[str, Any]]:
        """采集沪深北 A 股列表；单个交易所失败不影响其他交易所。"""
        results: dict[str, dict[str, Any]] = {}
        for exchange, func_name, kwargs, code_col, name_col, date_col in self.SOURCES:
            try:
                df = getattr(ak, func_name)(**kwargs)
            except Exception as e:
                logger.warning(f"[stock_info] {func_name}{kwargs} 获取失败: {e}")
                continue
            if df is None or df.empty or code_col not in df.columns:
                logger.warning(f"[stock_info] {func_name}{kwargs} 无数据")
                continue

            for row in df.to_dict("records"):
                raw_code = str(row.get(code_col) or "").strip()
                if not raw_code.isdigit():
                    continue
                code = raw_code.zfill(STOCK_CODE_LENGTH)
                results[code] = {
                    "code": code,
                    "name": normalize_name(row.get(name_col)),
                    "exchange": exchange,
                    "list_date": _normalize_date(row.get(date_col)),
                }
        return list(results.values())

    def refresh(self, db_path: str) -> int:
        """重新采集并写库（按代码更新或新增），返回写入条数。"""
        items = self.safe_collect()
        if not items:
            return 0

        now = datetime.now()
        with get_db_session(db_path) as session:
            existing = {row.code: row for row in session.query(StockInfo).all()}
            for item in items:
                row = existing.get(item["code"])
                if row is None:
                    session.add(StockInfo(**item, updated_at=now))
                else:
                    row.name = item["name"]
                    row.exchange = item["exchange"]
                    row.list_date = item["list_date"]
                    row.updated_at = now
        logger.info(f"[stock_info] 股票基础信息已刷新: {len(items)} 条")
        return len(items)

    def refresh_if_stale(self, db_path: str, max_age_hours: float = DEFAULT_MAX_AGE_HOURS) -> int:
        """表为空或超过 max_age_hours 未刷新时重新采集，返回写入条数（无需刷新时返回 0）。"""
        with get_db_session(db_path) as session:
            last_update = session.query(func.max(StockInfo.updated_at)).scalar()
        if last_update and datetime.now() - last_update < timedelta(hours=max_age_hours):
            return 0
        return self.refresh(db_path)
