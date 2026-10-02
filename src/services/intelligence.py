"""资讯源/条目持久化与范围查询；继续复用已有 RSS/Atom/NewsNow 采集器。"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import and_, or_

from src.database.db import get_db_session
from src.database.models import FinanceNews, IntelligenceItem, IntelligenceSource, StockInfo
from src.utils.redaction import redact_text
from src.utils.stock_code import diagnosis_code, name_variants


def safe_link(value: str | None) -> str:
    """订阅内容的链接只允许不含凭据的网页地址。"""
    try:
        parsed = urlparse(value or "")
        return redact_text(value) if parsed.scheme in ("http", "https") and parsed.hostname and not parsed.username and not parsed.password else ""
    except ValueError:
        return ""


class IntelligenceService:
    def __init__(self, config: dict):
        self.config = config
        self.db_path = (config.get("database") or {}).get("sqlite_path", "data/quant.db")

    @staticmethod
    def _source(row) -> dict:
        return {"id": row.id, "name": row.name, "url": row.url, "enabled": bool(row.enabled), "symbol": row.symbol,
                "market": row.market, "sector": row.sector, "managed_by_config": bool(row.managed_by_config),
                "last_fetched_at": row.last_fetched_at.isoformat() if row.last_fetched_at else None,
                "last_error": redact_text(row.last_error)}

    def sources(self) -> list[dict]:
        configured = (self.config.get("intelligence") or {}).get("sources") or []
        with get_db_session(self.db_path) as session:
            urls = set()
            for source in configured:
                url = str(source.get("url") or "").strip()
                if not url:
                    continue
                urls.add(url)
                row = session.query(IntelligenceSource).filter_by(url=url).first()
                if row is None:
                    row = IntelligenceSource(url=url)
                    session.add(row)
                row.name, row.enabled, row.managed_by_config = source.get("name") or url, source.get("enabled", True), True
                row.symbol = diagnosis_code(source["symbol"]) if source.get("symbol") else None
                row.market, row.sector = source.get("market") or "CN", source.get("sector")
            for row in session.query(IntelligenceSource).filter_by(managed_by_config=True):
                if row.url not in urls:
                    row.enabled = False
            session.flush()
            return [self._source(r) for r in session.query(IntelligenceSource).order_by(IntelligenceSource.id)]

    def save_source(self, data: dict, source_id: int | None = None) -> dict:
        parsed = urlparse(data["url"])
        if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("资讯源必须使用不含凭据的 HTTP/HTTPS 地址")
        with get_db_session(self.db_path) as session:
            row = session.get(IntelligenceSource, source_id) if source_id else session.query(IntelligenceSource).filter_by(url=data["url"]).first()
            if source_id and row is None:
                raise KeyError(source_id)
            if row is None:
                row = IntelligenceSource()
                session.add(row)
            if row.managed_by_config:
                raise ValueError("此源由 RSS 设置管理，请在原设置中修改")
            for key in ("url", "name", "enabled", "market", "sector"):
                setattr(row, key, data.get(key, True if key == "enabled" else "CN" if key == "market" else None))
            row.symbol = diagnosis_code(data["symbol"]) if data.get("symbol") else None
            session.flush()
            return self._source(row)

    def ingest(self, items: list[dict], source: dict | None = None) -> int:
        from src.collectors.rss import _parse_date
        added = 0
        with get_db_session(self.db_path) as session:
            for item in items:
                title = str(item.get("title") or "").strip()
                if not title:
                    continue
                label = item.get("source") or (source or {}).get("name") or "rss"
                fingerprint = hashlib.sha256(f"{label}\0{item.get('url', '')}\0{title}".encode()).hexdigest()
                if session.query(IntelligenceItem.id).filter_by(fingerprint=fingerprint).first():
                    continue
                published = item.get("published") or item.get("news_time")
                if isinstance(published, str):
                    published = _parse_date(published)
                session.add(IntelligenceItem(fingerprint=fingerprint, source_id=(source or {}).get("id"), source=label,
                                             title=title, summary=item.get("summary") or item.get("content") or "", url=safe_link(item.get("url")),
                                             symbol=(source or {}).get("symbol"), market=(source or {}).get("market") or "CN",
                                             sector=(source or {}).get("sector"), published_at=published))
                added += 1
        return added

    def record_fetch(self, source_id: int, error=None) -> None:
        with get_db_session(self.db_path) as session:
            row = session.get(IntelligenceSource, source_id)
            if row is not None:
                row.last_fetched_at, row.last_error = datetime.now(), redact_text(error, 300) if error else ""

    def fetch_source(self, source_id: int) -> dict:
        sources = self.sources()
        source = next((s for s in sources if s["id"] == source_id), None)
        if source is None:
            raise KeyError(source_id)
        from src.collectors.rss import RSSCollector, save_items
        collector = RSSCollector(self.config)
        try:
            _, raw = collector._fetch_feed(source["url"])
            items = [{**i, "source": source["name"]} for i in raw[:int((self.config.get("intelligence") or {}).get("max_items_per_source", 30))]]
            count = self.ingest(items, source)
            save_items([{**i, "content": i.get("summary"), "news_time": i.get("published"), "category": source["name"]} for i in items], self.db_path)
            error = ""
        except Exception as exc:
            count, error = 0, redact_text(exc, 300)
        finally:
            collector.close()
        self.record_fetch(source_id, error)
        return {"source_id": source_id, "added": count, "error": error}

    def items(self, *, symbol: str | None = None, market: str | None = None, sector: str | None = None,
              limit: int = 30, days: int = 7) -> list[dict[str, Any]]:
        symbol = diagnosis_code(symbol) if symbol else None
        with get_db_session(self.db_path) as session:
            query = session.query(IntelligenceItem).filter(IntelligenceItem.collected_at >= datetime.now() - timedelta(days=days))
            if symbol:
                name = session.query(StockInfo.name).filter_by(code=symbol).scalar() or ""
                from src.services.fund_registry import INDEX_CODES
                name = name or INDEX_CODES.get(symbol, {}).get("name", "")
                terms = [symbol, *name_variants(name)]
                query = query.filter(or_(IntelligenceItem.symbol == symbol, and_(IntelligenceItem.symbol.is_(None), or_(*[IntelligenceItem.title.contains(t) for t in terms]))))
            if market:
                query = query.filter(IntelligenceItem.market == market)
            if sector:
                query = query.filter(IntelligenceItem.sector == sector)
            records = [{"id": r.id, "source_id": r.source_id, "source": r.source, "title": r.title, "summary": r.summary,
                        "url": safe_link(r.url), "symbol": r.symbol, "market": r.market, "sector": r.sector,
                        "published_at": r.published_at.isoformat() if r.published_at else None, "collected_at": r.collected_at.isoformat()}
                       for r in query.order_by(IntelligenceItem.collected_at.desc()).limit(limit)]
            # 既有 FinanceNews 不做破坏性搬迁，提供标的历史兼容读取。
            if symbol and not sector and (not market or market == "CN") and len(records) < limit:
                known = {(r["source"], r["title"]) for r in records}
                legacy = session.query(FinanceNews).filter(FinanceNews.collected_at >= datetime.now() - timedelta(days=days),
                                                        or_(*[FinanceNews.title.contains(t) for t in terms])).order_by(FinanceNews.collected_at.desc()).limit(limit)
                for row in legacy:
                    if (row.source, row.title) not in known:
                        records.append({"id": f"legacy:{row.id}", "source_id": None, "source": row.source, "title": row.title,
                                        "summary": row.content, "url": safe_link(row.url), "symbol": symbol, "market": "CN", "sector": None,
                                        "published_at": row.news_time.isoformat() if row.news_time else None, "collected_at": row.collected_at.isoformat()})
            return records[:limit]
