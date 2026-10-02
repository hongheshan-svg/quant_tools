"""季度财务与现金分红，单位显式保留；失败时读取上次成功快照。"""

from __future__ import annotations

import json
import math
import re
import hashlib
import time
from datetime import date, datetime, timedelta

from src.database.db import get_db_session
from src.database.models import ResearchCache, FinancialSnapshot
from src.collectors.request_budget import configure_policy, bounded_call, POLICY
from src.utils.redaction import redact_text
from src.utils.stock_code import resolve_identity

METRICS = {
    "revenue": ("营业总收入", "营业收入"), "net_profit": ("归母净利润", "归属于母公司股东的净利润"),
    "operating_cash_flow": ("经营活动产生的现金流量净额", "经营现金流量净额"),
    "revenue_yoy": ("营业总收入同比增长率", "营业收入同比增长率", "营业总收入增长率"),
    "profit_yoy": ("归母净利润同比增长率", "净利润同比增长率", "净利润增长率"),
    "roe": ("净资产收益率", "加权净资产收益率"), "gross_margin": ("销售毛利率", "毛利率"),
}


def number(value):
    try:
        text = str(value).replace(",", "").replace("%", "").strip()
        multiplier = 1e8 if text.endswith("亿") else 1e4 if text.endswith("万") else 1
        value = float(text.rstrip("亿万")) * multiplier
        return value if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None


def financial_reports(frame, today: date | None = None) -> list[dict]:
    """Sina 宽表（指标 × 报告期）及兼容行表；不把发布日期当报告期。"""
    today = today or date.today()
    records = []
    if frame is None or frame.empty:
        return records
    if "指标" in frame:
        for column in frame.columns:
            raw = str(column).replace("-", "")
            if not re.fullmatch(r"\d{8}", raw):
                continue
            records.append({"report_date": f"{raw[:4]}-{raw[4:6]}-{raw[6:]}",
                            **{str(row["指标"]): row[column] for _, row in frame.iterrows()}})
    else:
        records = frame.to_dict("records")
    normalized = []
    for row in records:
        raw_date = str(row.get("report_date") or row.get("日期") or row.get("报告期") or "")[:10]
        try:
            report_date = date.fromisoformat(raw_date)
        except ValueError:
            continue
        if report_date > today:
            continue
        payload = {"report_date": raw_date, "currency": "CNY", "amount_unit": "元", "ratio_unit": "%"}
        publication = row.get("publication_date") or row.get("公告日期") or row.get("发布日期")
        if publication:
            try:
                published = date.fromisoformat(str(publication)[:10])
                if published > today:
                    continue
                payload["publication_date"] = published.isoformat()
            except ValueError:
                pass
        for metric, labels in METRICS.items():
            payload[metric] = next((number(value) for label in labels for key, value in row.items()
                                    if str(key).replace("(%)", "").replace("（%）", "") == label and number(value) is not None), None)
        if any(payload[key] is not None for key in METRICS):
            normalized.append(payload)
    return sorted(normalized, key=lambda row: row["report_date"], reverse=True)[:8]


def dividend_events(frame, today: date | None = None) -> dict:
    today = today or date.today()
    events = []
    for row in frame.to_dict("records") if frame is not None else []:
        try:
            ex_date = date.fromisoformat(str(row.get("除权除息日"))[:10])
        except ValueError:
            continue
        cash_per_ten = number(row.get("派息"))
        if "实施" not in str(row.get("进度", "")) or ex_date > today or cash_per_ten is None or cash_per_ten <= 0:
            continue
        events.append({"ex_dividend_date": str(ex_date), "cash_per_share": cash_per_ten / 10,
                       "currency": "CNY", "tax_basis": "pre_tax"})
    events.sort(key=lambda row: row["ex_dividend_date"], reverse=True)
    cutoff = str(today - timedelta(days=365))
    return {"events": events[:8], "ttm_cash_per_share": sum(row["cash_per_share"] for row in events if row["ex_dividend_date"] >= cutoff)}


class QuarterlyFundamentals:
    def __init__(self, config: dict):
        self.db_path = (config.get("database") or {}).get("sqlite_path", "data/quant.db")
        self.config = config

    def get(self, code: str, refresh: bool = True) -> dict:
        identity = resolve_identity(code)
        if identity.kind != "stock":
            return {"status": "not_supported", "reports": [], "dividend": {}}
        key = f"fundamentals:{identity.code}"
        with get_db_session(self.db_path) as session:
            cache = session.get(ResearchCache, key)
            previous = json.loads(cache.payload_json) if cache else None
            fresh = cache is not None and cache.updated_at >= datetime.now() - timedelta(days=1)
        if previous and fresh:
            return previous
        if not refresh:
            return {**(previous or {"reports": [], "dividend": {}}), "status": "stale" if previous else "missing"}
        import akshare as ak
        configure_policy(self.config)
        expires = time.monotonic() + POLICY.get().stage_seconds
        errors, reports, dividend = [], [], {}
        from src.collectors.source_chain import fetch_with_fallback, source_health
        result = fetch_with_fallback("季度基本面", [
            ("sina", lambda: financial_reports(ak.stock_financial_abstract(symbol=identity.code))),
            ("indicator", lambda: financial_reports(ak.stock_financial_analysis_indicator(symbol=identity.code, start_year=str(date.today().year - 2)))),
        ], cache_key=identity.code, count_empty_failures=False, stage_timeout_seconds=max(0, expires - time.monotonic()))
        reports = result.data or []
        errors.extend(result.errors.values())
        try:
            dividend = dividend_events(bounded_call(lambda: ak.stock_history_dividend_detail(symbol=identity.code, indicator="分红", date=""),
                                                   min(POLICY.get().request_seconds, expires - time.monotonic())))
            source_health.record("分红事件", "新浪", True)
        except Exception as error:
            errors.append(redact_text(error, 160))
            source_health.record("分红事件", "新浪", False, redact_text(error, 160))
        if not reports:
            return {**(previous or {"reports": [], "dividend": dividend}), "status": "stale" if previous else "fetch_failed", "errors": errors or ["未获得有效季度指标"]}
        payload = {"status": "partial" if errors else "available", "reports": reports, "dividend": dividend or (previous or {}).get("dividend", {}),
                   "source": "新浪财经/AkShare", "provider": result.source, "as_of": reports[0]["report_date"], "collected_at": datetime.now().isoformat(), "errors": errors,
                   "limitations": ["财务金额为报告期累计数，非单季度差分；分红为税前现金口径"]}
        with get_db_session(self.db_path) as session:
            row = session.get(ResearchCache, key)
            if row is None:
                row = ResearchCache(key=key)
                session.add(row)
            row.payload_json, row.updated_at = json.dumps(payload, ensure_ascii=False), datetime.now()
            fingerprint = hashlib.sha256((identity.code + row.payload_json).encode()).hexdigest()
            if not session.query(FinancialSnapshot.id).filter_by(fingerprint=fingerprint).first():
                session.add(FinancialSnapshot(code=identity.code, payload_json=row.payload_json,
                                              collected_at=row.updated_at, fingerprint=fingerprint))
        return payload
