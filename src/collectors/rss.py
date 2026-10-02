"""
RSS / Atom 资讯源采集器
用户在 intelligence.sources 里配置任意 RSS 2.0、Atom、RSS 1.0（RDF）订阅地址，
可用 RSSHub 等工具为财经媒体生成订阅。只用标准库 xml.etree 和 beautifulsoup4 解析。
"""

import json
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Any

from bs4 import BeautifulSoup
from loguru import logger

from src.collectors.base import BaseCollector
from src.collectors.source_chain import source_health

SOURCE_NAME = "rss"
NEWSNOW_BASE = "https://newsnow.busiyi.world/api/s?id="
# 资讯源模板（设置页「从模板添加」）：NewsNow 聚合的财经快讯（JSON 接口）和全球市场 RSS。
# 不收 NewsNow 的雪球热门股票：条目是股票名而不是资讯，进舆情分析只会添噪声
TEMPLATES: list[dict[str, str]] = [
    {"id": "newsnow-cls-hot", "name": "财联社热门", "url": NEWSNOW_BASE + "cls-hot",
     "description": "NewsNow 聚合的财联社热门资讯，适合大盘和题材热点"},
    {"id": "newsnow-wallstreetcn-quick", "name": "华尔街见闻快讯", "url": NEWSNOW_BASE + "wallstreetcn-quick",
     "description": "NewsNow 聚合的华尔街见闻快讯，宏观、商品和市场事件"},
    {"id": "newsnow-jin10", "name": "金十数据", "url": NEWSNOW_BASE + "jin10",
     "description": "NewsNow 聚合的金十实时财经消息，全球宏观和外盘事件"},
    {"id": "newsnow-gelonghui", "name": "格隆汇事件", "url": NEWSNOW_BASE + "gelonghui",
     "description": "NewsNow 聚合的格隆汇事件资讯，A 股异动和港股、中概股"},
    {"id": "marketwatch-top", "name": "MarketWatch", "url": "https://feeds.content.dowjones.io/public/rss/mw_topstories",
     "description": "MarketWatch 头条（英文 RSS），全球市场背景"},
]
SUMMARY_MAX_LEN = 500
DATASET = "rss"


def _local(tag: Any) -> str:
    """去掉命名空间，返回标签本地名"""
    if not isinstance(tag, str):
        return ""
    return tag.rsplit("}", 1)[-1].lower()


def _children(node: ET.Element, name: str) -> list[ET.Element]:
    return [c for c in node if _local(c.tag) == name]


def _child_text(node: ET.Element, *names: str) -> str:
    """按顺序取第一个非空子标签的文本"""
    for name in names:
        for child in _children(node, name):
            text = "".join(child.itertext()).strip()
            if text:
                return text
    return ""


def _strip_html(text: str) -> str:
    """去掉 HTML 标签，压缩空白，截断到 500 字"""
    if not text:
        return ""
    plain = BeautifulSoup(text, "html.parser").get_text(" ", strip=True)
    plain = " ".join(plain.split())
    return plain[:SUMMARY_MAX_LEN]


def _parse_date(text: str) -> datetime | None:
    """解析 RFC 822 / ISO 8601 日期，统一转成本地时区的无时区时间"""
    text = (text or "").strip()
    if not text:
        return None
    dt = None
    try:
        dt = parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError):
        dt = None
    if dt is None:
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00").replace("z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is not None:
        dt = dt.astimezone().replace(tzinfo=None)
    return dt


def _entry_url(node: ET.Element) -> str:
    """RSS 取 link 文本；Atom 取 rel=alternate 或第一个 href"""
    first_href = ""
    for link in _children(node, "link"):
        href = (link.get("href") or "").strip()
        if href:
            if (link.get("rel") or "alternate") == "alternate":
                return href
            first_href = first_href or href
            continue
        text = (link.text or "").strip()
        if text:
            return text
    if first_href:
        return first_href
    guid = _child_text(node, "guid", "id")
    return guid if guid.startswith(("http://", "https://")) else ""


def parse_feed(text: str) -> tuple[str, list[dict]]:
    """解析订阅内容，返回（频道标题, 条目列表）。XML 非法时抛 ValueError。"""
    raw = text.lstrip("\ufeff").strip() if isinstance(text, str) else text
    probe = raw if isinstance(raw, str) else raw.decode("utf-8", "ignore")
    if "<!ENTITY" in probe.upper():  # 防实体膨胀攻击
        raise ValueError("订阅内容包含实体定义，已拒绝解析")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as e:
        raise ValueError(f"订阅内容不是合法的 XML: {e}") from e

    kind = _local(root.tag)
    if kind == "feed":  # Atom
        channel, nodes = root, _children(root, "entry")
    elif kind in ("rss", "rdf"):
        channels = _children(root, "channel")
        channel = channels[0] if channels else root
        # RSS 2.0 的 item 在 channel 内；RSS 1.0 的 item 与 channel 同级
        nodes = _children(channel, "item") or _children(root, "item")
    elif kind == "channel":
        channel, nodes = root, _children(root, "item")
    else:
        raise ValueError("不是 RSS / Atom 订阅（找不到 rss、feed 或 RDF 根节点）")

    title = _child_text(channel, "title")
    items: list[dict] = []
    for node in nodes:
        item_title = _strip_html(_child_text(node, "title"))
        if not item_title:
            continue
        summary = _strip_html(_child_text(node, "description", "summary", "encoded", "content"))
        published = _parse_date(_child_text(node, "pubdate", "published", "updated", "date"))
        items.append({"title": item_title, "url": _entry_url(node), "summary": summary, "published": published})
    return title, items


def _enabled_sources(config: dict) -> list[dict]:
    """intelligence.sources 中启用且有效的源"""
    cfg = (config or {}).get("intelligence") or {}
    if (config.get("database") or {}).get("sqlite_path"):
        from src.services.intelligence import IntelligenceService
        try:
            return [s for s in IntelligenceService(config).sources() if s["enabled"]]
        except Exception as error:
            logger.warning(f"读取持久化资讯源失败，使用配置源: {error}")
    result = []
    for src in cfg.get("sources") or []:
        if not isinstance(src, dict) or src.get("enabled", True) is False:
            continue
        url = str(src.get("url") or "").strip()
        if not url:
            continue
        result.append({"name": str(src.get("name") or url).strip(), "url": url})
    return result


def _ms_time(value: Any) -> datetime | None:
    """NewsNow 的毫秒时间戳"""
    try:
        return datetime.fromtimestamp(float(value) / 1000) if value else None
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def parse_newsnow(payload: Any) -> tuple[str, list[dict]]:
    """解析 NewsNow 接口（{"id", "items": [{title, url, pubDate | extra.date}]}）。格式不对时抛 ValueError。"""
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        raise ValueError("不是 NewsNow 接口返回（缺少 items 列表）")
    items: list[dict] = []
    for raw in payload["items"]:
        if not isinstance(raw, dict):
            continue
        title = _strip_html(str(raw.get("title") or ""))
        if not title:
            continue
        extra = raw.get("extra") if isinstance(raw.get("extra"), dict) else {}
        items.append({"title": title, "url": str(raw.get("url") or ""), "summary": "",
                      "published": _ms_time(raw.get("pubDate") or extra.get("date"))})
    return f"NewsNow {payload.get('id') or ''}".strip(), items


def parse_content(content: str | bytes) -> tuple[str, list[dict]]:
    """按内容判断：JSON 对象按 NewsNow 解析，否则按 RSS / Atom 解析"""
    text = content.decode("utf-8", "ignore") if isinstance(content, bytes) else (content or "")
    if text.lstrip("\ufeff").lstrip().startswith("{"):
        try:
            payload = json.loads(text.lstrip("\ufeff"))
        except ValueError as e:
            raise ValueError(f"内容不是合法的 JSON: {e}") from e
        return parse_newsnow(payload)
    return parse_feed(content)


class RSSCollector(BaseCollector):
    """RSS / Atom 资讯采集器"""

    SOURCE_NAME = SOURCE_NAME

    def _fetch_feed(self, url: str, max_retries: int = 2) -> tuple[str, list[dict]]:
        resp = self.fetch_url(url, max_retries=max_retries)
        if resp is None:
            raise RuntimeError("请求失败")
        content = getattr(resp, "content", None)
        return parse_content(content if isinstance(content, bytes) and content else resp.text)

    def collect(self) -> list[dict[str, Any]]:
        cfg = (self.config or {}).get("intelligence") or {}
        limit = max(1, int(cfg.get("max_items_per_source", 30) or 30))
        results: list[dict[str, Any]] = []
        for src in _enabled_sources(self.config):
            started = time.monotonic()
            try:
                _, entries = self._fetch_feed(src["url"])
            except Exception as e:  # 单个源失败不影响其他源
                logger.warning(f"[rss] 源 {src['name']} 采集失败: {e}")
                source_health.record(DATASET, src["name"], False, str(e)[:200], time.monotonic() - started)
                if src.get("id"):
                    from src.services.intelligence import IntelligenceService
                    IntelligenceService(self.config).record_fetch(src["id"], e)
                continue
            source_health.record(DATASET, src["name"], True, elapsed=time.monotonic() - started)
            for entry in entries[:limit]:
                results.append({
                    "title": entry["title"],
                    "content": entry["summary"],
                    "news_time": entry["published"],
                    "url": entry["url"],
                    "category": src["name"],
                    "source": src["name"],
                })
            if src.get("id"):
                from src.services.intelligence import IntelligenceService
                service = IntelligenceService(self.config)
                service.ingest([{**e, "source": src["name"]} for e in entries[:limit]], src)
                service.record_fetch(src["id"])
        return results


def save_items(items: list[dict], db_path: str, keep_days: int = 7) -> int:
    """写入 finance_news（source=rss），按 url（无 url 时按标题）与近 keep_days 天已有记录及批内去重，返回新增条数。"""
    from src.database.db import get_db_session, init_db
    from src.database.models import FinanceNews

    if not items:
        return 0
    init_db(db_path)
    cutoff = datetime.now() - timedelta(days=max(1, int(keep_days)))
    with get_db_session(db_path) as session:
        rows = (
            session.query(FinanceNews.url, FinanceNews.title)
            .filter(FinanceNews.source == SOURCE_NAME, FinanceNews.collected_at >= cutoff)
            .all()
        )
        seen = {(u or t) for u, t in rows}
        added = 0
        for item in items:
            title = str(item.get("title") or "").strip()
            url = str(item.get("url") or "").strip()
            key = url or title
            if not title or key in seen:
                continue
            seen.add(key)
            session.add(FinanceNews(
                source=SOURCE_NAME,
                category=str(item.get("category") or "")[:50],
                title=title[:500],
                content=item.get("content"),
                news_time=item.get("news_time"),
                url=url[:1000] or None,
            ))
            added += 1
    from src.services.intelligence import IntelligenceService
    IntelligenceService({"database": {"sqlite_path": db_path}}).ingest(items)
    return added


def test_feed(url: str, config: dict | None = None) -> dict:
    """试抓一个订阅地址（不入库），返回 {ok, title, count, samples, error}"""
    result = {"ok": False, "title": "", "count": 0, "samples": [], "error": ""}
    url = (url or "").strip()
    if not url.startswith(("http://", "https://")):
        result["error"] = "地址必须以 http:// 或 https:// 开头"
        return result
    collector = RSSCollector(config)
    try:
        title, entries = collector._fetch_feed(url, max_retries=1)
        result.update(ok=True, title=title, count=len(entries), samples=[e["title"] for e in entries[:3]])
    except Exception as e:
        result["error"] = str(e)[:200] or "抓取失败"
    finally:
        collector.close()
    return result


test_feed.__test__ = False  # 避免 pytest 把它当成测试函数收集
