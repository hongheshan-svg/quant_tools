"""
个股新闻与公告（东方财富，普通 HTTP），按股票按需获取，进程内缓存 30 分钟

- 新闻：东方财富资讯搜索（按时间倒序），标题里只提到代码的综合类快讯也会出现
- 公告：东方财富公告大全，附公告类型；标题命中风险关键词（立案、减持、问询等）时标注风险，
  其中立案调查、退市风险警示等属于严重风险（个股诊断会据此拦截买入建议）
失败时返回空列表并记入数据源健康状态；没有新闻不算失败。
"""

from __future__ import annotations

import json
import re
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Any

import httpx
from loguru import logger

from src.collectors.source_chain import fetch_with_fallback
from src.utils.stock_code import bare_code

NEWS_URL = "https://search-api-web.eastmoney.com/search/jsonp"
NOTICE_URL = "https://np-anotice-stock.eastmoney.com/api/security/ann"
NOTICE_DETAIL_URL = "https://data.eastmoney.com/notices/detail/{code}/{art_code}.html"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36"
TIMEOUT_SECONDS = 10
CACHE_MINUTES = 30
NEWS_DAYS = 7
NOTICE_DAYS = 30
NEWS_LIMIT = 10
NOTICE_LIMIT = 20

SEVERE_NOTICE_KEYWORDS = ("立案", "退市风险", "终止上市", "暂停上市", "重大违法", "风险警示")
RISK_NOTICE_KEYWORDS = SEVERE_NOTICE_KEYWORDS + (
    "处罚", "问询函", "监管函", "警示函", "诉讼", "仲裁", "减持", "质押", "冻结", "预亏", "预减", "亏损", "延期", "无法表示意见",
    "异常波动",
)

_JSONP = re.compile(r"^[^(]*\((.*)\)\s*;?\s*$", re.S)
_TAG = re.compile(r"<[^>]+>")

_cache: dict[str, tuple[datetime, dict[str, list[dict[str, Any]]]]] = {}
_cache_lock = threading.Lock()


@dataclass
class NewsItem:
    kind: str           # 新闻 / 公告
    title: str
    date: str           # YYYY-MM-DD HH:MM
    source: str = ""    # 新闻媒体或公告类型
    url: str = ""
    risk: str = ""      # 命中的风险关键词
    severe: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _get(url: str, params: dict, referer: str) -> httpx.Response:
    resp = httpx.get(url, params=params, headers={"User-Agent": USER_AGENT, "Referer": referer}, timeout=TIMEOUT_SECONDS)
    resp.raise_for_status()
    return resp


def fetch_stock_news(code: str, limit: int = NEWS_LIMIT) -> list[NewsItem]:
    bare = bare_code(code)
    inner = {
        "uid": "", "keyword": bare, "type": ["cmsArticleWebOld"], "client": "web", "clientType": "web", "clientVersion": "curr",
        "param": {"cmsArticleWebOld": {"searchScope": "default", "sort": "time", "pageIndex": 1, "pageSize": limit,
                                       "preTag": "", "postTag": ""}},
    }
    resp = _get(NEWS_URL, {"cb": "jQuery1", "param": json.dumps(inner, ensure_ascii=False)}, f"https://so.eastmoney.com/news/s?keyword={bare}")
    match = _JSONP.match(resp.text)
    payload = json.loads(match.group(1) if match else resp.text)
    rows = (payload.get("result") or {}).get("cmsArticleWebOld") or []
    return [
        NewsItem(kind="新闻", title=_TAG.sub("", r.get("title") or "").strip(), date=str(r.get("date") or "")[:16],
                 source=r.get("mediaName") or "", url=r.get("url") or "")
        for r in rows if r.get("title")
    ]


def classify_notice(title: str) -> tuple[str, bool]:
    """(命中的风险关键词, 是否严重风险)。"""
    hit = next((kw for kw in RISK_NOTICE_KEYWORDS if kw in title), "")
    return hit, hit in SEVERE_NOTICE_KEYWORDS


def fetch_stock_notices(code: str, limit: int = NOTICE_LIMIT) -> list[NewsItem]:
    bare = bare_code(code)
    params = {"sr": -1, "page_size": limit, "page_index": 1, "ann_type": "A", "client_source": "web",
              "stock_list": bare, "f_node": 0, "s_node": 0}
    rows = ((_get(NOTICE_URL, params, "https://data.eastmoney.com/").json().get("data") or {}).get("list")) or []
    items = []
    for r in rows:
        title = (r.get("title") or "").strip()
        if not title:
            continue
        risk, severe = classify_notice(title)
        columns = [c.get("column_name", "") for c in r.get("columns") or [] if isinstance(c, dict)]
        items.append(NewsItem(
            kind="公告", title=title, date=str(r.get("notice_date") or "")[:10], source="、".join(c for c in columns if c),
            url=NOTICE_DETAIL_URL.format(code=bare, art_code=r.get("art_code", "")) if r.get("art_code") else "",
            risk=risk, severe=severe,
        ))
    return items


def get_stock_news(code: str, refresh: bool = False, now: datetime | None = None, *, include_status: bool = False) -> dict:
    """近 7 天新闻和近 30 天公告：{"news": [...], "notices": [...]}，按日期倒序。"""
    bare = bare_code(code)
    now = now or datetime.now()
    with _cache_lock:
        cached = _cache.get(bare)
        if cached and not refresh and now - cached[0] < timedelta(minutes=CACHE_MINUTES):
            return {**cached[1], "states": {"news": "available", "notices": "available"}} if include_status else cached[1]

    def recent(items: list[NewsItem] | None, days: int) -> list[dict[str, Any]]:
        since = (now - timedelta(days=days)).strftime("%Y-%m-%d")
        return [i.to_dict() for i in sorted(items or [], key=lambda i: i.date, reverse=True) if i.date[:10] >= since]

    # 没有新闻也是有效结果，不能计为数据源失败；缓存按股票区分，不用数据集级的过期兜底
    news = fetch_with_fallback("个股新闻", [("东方财富", lambda: fetch_stock_news(bare))], is_valid=lambda d: d is not None)
    notices = fetch_with_fallback("个股公告", [("东方财富", lambda: fetch_stock_notices(bare))], is_valid=lambda d: d is not None)
    result = {"news": recent(news.data, NEWS_DAYS), "notices": recent(notices.data, NOTICE_DAYS)}
    if news.data is not None and notices.data is not None:
        with _cache_lock:
            _cache[bare] = (now, result)
    else:
        logger.debug(f"个股新闻/公告获取不完整 [{bare}]: {news.errors or ''} {notices.errors or ''}")
    if include_status:
        return {**result, "states": {"news": "available" if news.data is not None else "fetch_failed", "notices": "available" if notices.data is not None else "fetch_failed"}}
    return result


def reset_cache() -> None:
    with _cache_lock:
        _cache.clear()
