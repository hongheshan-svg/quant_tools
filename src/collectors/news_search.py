"""
联网新闻搜索：博查、Tavily、SerpAPI、Brave、SearXNG 五种搜索服务，按 search.providers 的顺序回退。

- 每个 provider 可配置多个 Key（或多个 SearXNG 地址），在自己的 Key 之间轮询；
  某个 Key 返回 401/403/429 时进入 10 分钟冷却（进程级），同一次调用里换下一个 Key。
- 多个 provider 之间用 source_chain.fetch_with_fallback("news_search", ...) 回退，出错或无结果都换下一个。
- 结果按 (查询词, 条数, 天数) 缓存 search.cache_minutes 分钟（进程级），只缓存非空结果。
- 未启用或没有可用配置时 search() 返回空列表，不抛出。
"""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from typing import Any, Callable, Iterable

import httpx
from loguru import logger

from src.collectors.source_chain import NO_DATA, fetch_with_fallback

DATASET = "news_search"
PROVIDERS: dict[str, str] = {"bocha": "博查", "tavily": "Tavily", "serpapi": "SerpAPI", "brave": "Brave",
                             "anspire": "Anspire", "minimax": "MiniMax", "searxng": "SearXNG"}
KEY_PROVIDERS = ("bocha", "tavily", "serpapi", "brave", "anspire", "minimax")
DEFAULT_TIMEOUT = 10.0
KEY_COOLDOWN_SECONDS = 600
REJECT_STATUS = (401, 403, 429)
SNIPPET_MAX_CHARS = 300


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str = ""
    source: str = ""        # 站点或媒体名，可空
    published: str = ""     # YYYY-MM-DD，未知为空
    provider: str = ""      # PROVIDERS 的键
    relevance: dict | None = None   # 相关度分级（score_news 的结果），个股搜索时才有


class KeyRejected(Exception):
    """Key 被拒绝（401/403/429），需要冷却并换下一个 Key。"""


_lock = threading.Lock()
_cache: dict[tuple, tuple[float, list[SearchResult]]] = {}
_cooldown: dict[tuple[str, str], float] = {}     # (provider, key) -> 冷却结束的 monotonic 时间
_rotation: dict[str, int] = {}                    # provider -> 下一次起始位置


def reset_state() -> None:
    """清空缓存和 Key 冷却（测试用）。"""
    with _lock:
        _cache.clear()
        _cooldown.clear()
        _rotation.clear()


# ---------- 配置 ----------

def _as_list(value: Any) -> list[str]:
    """列表或逗号分隔字符串 -> 去空白、去空项、去占位符后的列表。"""
    if value is None:
        return []
    items = value.replace("\n", ",").split(",") if isinstance(value, str) else list(value) if isinstance(value, (list, tuple)) else [value]
    out = []
    for item in items:
        text = str(item).strip()
        if text and "your-" not in text:
            out.append(text)
    return out


def _section(config: dict) -> dict:
    return (config or {}).get("search") or {}


def _credentials(config: dict, provider: str) -> list[str]:
    conf = _section(config).get(provider) or {}
    return _as_list(conf.get("base_urls" if provider == "searxng" else "api_keys"))


def configured_providers(config: dict) -> list[str]:
    """按 search.providers 的顺序（缺省为全部）返回已配置的 provider。"""
    order = _as_list(_section(config).get("providers")) or list(PROVIDERS)
    result: list[str] = []
    for name in order:
        name = name.lower()
        if name in PROVIDERS and name not in result and _credentials(config, name):
            result.append(name)
    return result


def is_enabled(config: dict) -> bool:
    return bool(_section(config).get("enabled")) and bool(configured_providers(config))


def _int(config: dict, name: str, default: int) -> int:
    try:
        value = int(_section(config).get(name, default))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


# ---------- 日期 ----------

_RELATIVE_EN = re.compile(r"(\d+)\s*(minute|min|hour|day|week|month|year)s?\s*ago", re.I)
_RELATIVE_ZH = re.compile(r"(\d+)\s*(分钟|小时|天|周|个月|月|年)前")
_UNIT_DAYS = {"minute": 0, "min": 0, "hour": 0, "day": 1, "week": 7, "month": 30, "year": 365,
              "分钟": 0, "小时": 0, "天": 1, "周": 7, "个月": 30, "月": 30, "年": 365}


def parse_date(value: Any, today: date | None = None) -> str:
    """把各种日期写法统一成 YYYY-MM-DD，解析不了返回空字符串。"""
    text = str(value or "").strip()
    if not text:
        return ""
    today = today or date.today()
    m = re.match(r"(\d{4})[-/.年](\d{1,2})[-/.月](\d{1,2})", text)
    if m:
        try:
            return date(int(m[1]), int(m[2]), int(m[3])).isoformat()
        except ValueError:
            return ""
    m = _RELATIVE_EN.search(text) or _RELATIVE_ZH.search(text)
    if m:
        return (today - timedelta(days=int(m[1]) * _UNIT_DAYS[m[2].lower()])).isoformat()
    for fmt in ("%b %d, %Y", "%B %d, %Y", "%m/%d/%Y", "%d %b %Y"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return ""


def _clip(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()[:SNIPPET_MAX_CHARS]


# ---------- 各 provider：请求单个 Key，返回 SearchResult 列表 ----------

def _check(resp: Any) -> None:
    status = getattr(resp, "status_code", 200)
    if status in REJECT_STATUS:
        raise KeyRejected(f"HTTP {status}")
    if status != 200:
        raise RuntimeError(f"HTTP {status}")


def _bocha(key: str, query: str, limit: int, days: int, config: dict) -> list[SearchResult]:
    freshness = "oneDay" if days <= 1 else "oneWeek" if days <= 7 else "oneMonth" if days <= 31 else "oneYear"
    resp = httpx.post(
        "https://api.bochaai.com/v1/web-search",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={"query": query, "freshness": freshness, "summary": True, "count": limit},
        timeout=DEFAULT_TIMEOUT,
    )
    _check(resp)
    body = resp.json() or {}
    if body.get("code") not in (None, 200):
        raise RuntimeError(f"博查返回错误：{body.get('msg') or body.get('code')}")
    data = body.get("data") or body
    rows = ((data.get("webPages") or {}).get("value")) or []
    return [
        SearchResult(_clip(r.get("name")), r.get("url") or "", _clip(r.get("summary") or r.get("snippet")),
                     _clip(r.get("siteName")), parse_date(r.get("datePublished")), "bocha")
        for r in rows
    ]


def _tavily(key: str, query: str, limit: int, days: int, config: dict) -> list[SearchResult]:
    resp = httpx.post(
        "https://api.tavily.com/search",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={"api_key": key, "query": query, "topic": "news", "days": days, "max_results": limit, "search_depth": "basic"},
        timeout=DEFAULT_TIMEOUT,
    )
    _check(resp)
    out = []
    for r in (resp.json() or {}).get("results") or []:
        url = r.get("url") or ""
        host = re.sub(r"^https?://(www\.)?", "", url).split("/")[0]
        out.append(SearchResult(_clip(r.get("title")), url, _clip(r.get("content")), host, parse_date(r.get("published_date")), "tavily"))
    return out


def _serpapi(key: str, query: str, limit: int, days: int, config: dict) -> list[SearchResult]:
    period = "d" if days <= 1 else "w" if days <= 7 else "m" if days <= 31 else "y"
    resp = httpx.get(
        "https://serpapi.com/search.json",
        params={"engine": "google", "tbm": "nws", "q": query, "api_key": key, "hl": "zh-cn", "gl": "cn",
                "num": limit, "tbs": f"qdr:{period}"},
        timeout=DEFAULT_TIMEOUT,
    )
    _check(resp)
    body = resp.json() or {}
    if body.get("error") and not body.get("news_results"):
        raise RuntimeError(f"SerpAPI 返回错误：{body['error']}")
    out = []
    for r in body.get("news_results") or []:
        source = r.get("source")
        source = source.get("name") if isinstance(source, dict) else source
        out.append(SearchResult(_clip(r.get("title")), r.get("link") or "", _clip(r.get("snippet")),
                                _clip(source), parse_date(r.get("date")), "serpapi"))
    return out


def _brave(key: str, query: str, limit: int, days: int, config: dict) -> list[SearchResult]:
    freshness = "pd" if days <= 1 else "pw" if days <= 7 else "pm" if days <= 31 else "py"
    resp = httpx.get(
        "https://api.search.brave.com/res/v1/news/search",
        headers={"X-Subscription-Token": key, "Accept": "application/json"},
        params={"q": query, "count": min(limit, 20), "freshness": freshness, "country": "cn", "search_lang": "zh-hans"},
        timeout=DEFAULT_TIMEOUT,
    )
    _check(resp)
    out = []
    for r in (resp.json() or {}).get("results") or []:
        meta = r.get("meta_url") or {}
        source = meta.get("hostname") or (r.get("source") if isinstance(r.get("source"), str) else "") or ""
        out.append(SearchResult(_clip(r.get("title")), r.get("url") or "", _clip(r.get("description")),
                                _clip(re.sub(r"^www\.", "", source)), parse_date(r.get("page_age") or r.get("age")), "brave"))
    return out


def _host(url: str) -> str:
    return re.sub(r"^https?://(www\.)?", "", url or "").split("/")[0]


def _anspire(key: str, query: str, limit: int, days: int, config: dict) -> list[SearchResult]:
    """Anspire 搜索（plugin.anspire.cn）；region_mode=2 覆盖全球区域（与 daily_stock_analysis 一致）"""
    now = datetime.now()
    resp = httpx.get(
        "https://plugin.anspire.cn/api/ntsearch/search",
        headers={"Authorization": f"Bearer {key}"},
        params={"query": query, "top_k": min(limit, 50), "region_mode": 2,
                "FromTime": (now - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S"), "ToTime": now.strftime("%Y-%m-%d %H:%M:%S")},
        timeout=DEFAULT_TIMEOUT,
    )
    _check(resp)
    body = resp.json() or {}
    if "results" not in body:
        raise RuntimeError(f"Anspire 返回错误：{body.get('message') or body.get('msg') or '缺少 results'}")
    return [
        SearchResult(_clip(r.get("title")), r.get("url") or "", _clip(r.get("content")), _host(r.get("url") or ""),
                     parse_date(r.get("date")), "anspire")
        for r in body.get("results") or []
    ]


def _minimax_time_hint(query: str, days: int) -> str:
    """MiniMax 搜索没有时间参数，在查询词后加时间提示（结果再按日期过滤）"""
    if any("\u4e00" <= ch <= "\u9fff" for ch in query):
        return "今天" if days <= 1 else "最近三天" if days <= 3 else "最近一周" if days <= 7 else "最近一个月"
    return "today" if days <= 1 else "past 3 days" if days <= 3 else "past week" if days <= 7 else "past month"


def _minimax(key: str, query: str, limit: int, days: int, config: dict) -> list[SearchResult]:
    resp = httpx.post(
        "https://api.minimaxi.com/v1/coding_plan/search",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json", "MM-API-Source": "Minimax-MCP"},
        json={"q": f"{query} {_minimax_time_hint(query, days)}"},
        timeout=DEFAULT_TIMEOUT,
    )
    _check(resp)
    body = resp.json() or {}
    base = body.get("base_resp") or {}
    if base.get("status_code", 0) != 0:
        raise RuntimeError(f"MiniMax 返回错误：{base.get('status_msg') or base.get('status_code')}")
    return [
        SearchResult(_clip(r.get("title")), r.get("link") or "", _clip(r.get("snippet")), _host(r.get("link") or ""),
                     parse_date(r.get("date")), "minimax")
        for r in (body.get("organic") or [])[: max(limit, 1)]
    ]


def _searxng(base_url: str, query: str, limit: int, days: int, config: dict) -> list[SearchResult]:
    conf = _section(config).get("searxng") or {}
    try:
        timeout = float(conf.get("timeout") or DEFAULT_TIMEOUT)
    except (TypeError, ValueError):
        timeout = DEFAULT_TIMEOUT
    time_range = "day" if days <= 1 else "week" if days <= 7 else "month" if days <= 31 else "year"
    resp = httpx.get(
        base_url.rstrip("/") + "/search",
        params={"q": query, "format": "json", "categories": "news", "language": "zh-CN", "time_range": time_range},
        timeout=timeout,
    )
    _check(resp)
    out = []
    for r in ((resp.json() or {}).get("results") or [])[:limit]:
        url = r.get("url") or ""
        host = re.sub(r"^https?://(www\.)?", "", url).split("/")[0]
        out.append(SearchResult(_clip(r.get("title")), url, _clip(r.get("content")), host, parse_date(r.get("publishedDate")), "searxng"))
    return out


_FETCHERS: dict[str, Callable[[str, str, int, int, dict], list[SearchResult]]] = {
    "bocha": _bocha, "tavily": _tavily, "serpapi": _serpapi, "brave": _brave,
    "anspire": _anspire, "minimax": _minimax, "searxng": _searxng,
}


# ---------- Key 轮询与冷却 ----------

def _ordered_keys(provider: str, keys: list[str]) -> list[str]:
    """轮询顺序：起始位置逐次后移，跳过冷却中的 Key。"""
    now = time.monotonic()
    with _lock:
        start = _rotation.get(provider, 0) % len(keys)
        _rotation[provider] = start + 1
        rotated = keys[start:] + keys[:start]
        return [k for k in rotated if _cooldown.get((provider, k), 0) <= now]


def _request_provider(provider: str, query: str, limit: int, days: int, config: dict) -> list[SearchResult]:
    """依次尝试该 provider 的各个 Key，全部不可用才抛出。"""
    keys = _credentials(config, provider)
    available = _ordered_keys(provider, keys)
    if not available:
        raise RuntimeError("所有 Key 都在冷却中（被拒绝或限流）")
    last_error: Exception | None = None
    for key in available:
        try:
            return _FETCHERS[provider](key, query, limit, days, config)
        except KeyRejected as e:
            with _lock:
                _cooldown[(provider, key)] = time.monotonic() + KEY_COOLDOWN_SECONDS
            logger.warning(f"[搜索] {PROVIDERS[provider]} 的 Key …{key[-4:]} 被拒绝（{e}），冷却 10 分钟")
            last_error = e
        except Exception as e:
            last_error = e
            if provider == "searxng":   # 多个地址：换下一个
                continue
            raise
    raise RuntimeError(f"所有 Key 均不可用：{last_error}")


# ---------- 对外接口 ----------

def _filter(results: list[SearchResult], days: int, limit: int) -> list[SearchResult]:
    """丢掉早于 days 天的结果，按 url 去重（无标题的丢弃）。"""
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    seen: set[str] = set()
    out = []
    for r in results:
        if not r.title or (r.published and r.published < cutoff):
            continue
        key = r.url or r.title
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out[:limit]


# 搜索状态：有结果 / 搜过但没有结果 / 全部服务都请求失败 / 未启用
STATUS_OK, STATUS_EMPTY, STATUS_FAILED, STATUS_DISABLED = "ok", "empty", "failed", "disabled"
_last_status = threading.local()


def last_search_status() -> tuple[str, str]:
    """当前线程最近一次 search() 的 (状态, 失败原因)；还没搜过时为 ("", "")。
    报告据此说明消息面是否可信：只要有一个服务正常返回（哪怕没有结果）就是 empty，所有服务都出错才是 failed。"""
    return getattr(_last_status, "value", ("", ""))


def clear_search_status() -> None:
    _last_status.value = ("", "")


def search(query: str, config: dict, *, max_results: int | None = None, days: int | None = None,
           use_cache: bool = True) -> list[SearchResult]:
    """联网搜索新闻；未启用、全部失败或无结果都返回空列表（状态见 last_search_status()）。"""
    results, status, reason = _search(query, config, max_results=max_results, days=days, use_cache=use_cache)
    _last_status.value = (status, reason)
    return results


def _search(query: str, config: dict, *, max_results: int | None, days: int | None,
            use_cache: bool) -> tuple[list[SearchResult], str, str]:
    if not query or not is_enabled(config):
        return [], STATUS_DISABLED, ""
    limit = max_results or _int(config, "max_results", 8)
    days = days or _int(config, "days", 7)
    cache_key = (query, limit, days)
    ttl = float(_section(config).get("cache_minutes", 30) or 0) * 60
    if use_cache and ttl > 0:
        with _lock:
            hit = _cache.get(cache_key)
            if hit and time.monotonic() - hit[0] < ttl:
                return list(hit[1]), STATUS_OK, ""

    def make(provider: str) -> Callable[[], list[SearchResult]]:
        return lambda: _filter(_request_provider(provider, query, limit, days, config), days, limit)

    try:
        fetched = fetch_with_fallback(DATASET, [(p, make(p)) for p in configured_providers(config)])
    except Exception as e:
        logger.warning(f"[搜索] 联网搜索失败: {e}")
        return [], STATUS_FAILED, str(e)
    results = list(fetched.data or []) if fetched.ok else []
    if results and ttl > 0:
        with _lock:
            _cache[cache_key] = (time.monotonic(), results)
    if results:
        return list(results), STATUS_OK, ""
    errors = {k: v for k, v in fetched.errors.items() if v != NO_DATA}
    if errors and len(errors) == len(fetched.errors):
        return [], STATUS_FAILED, "；".join(f"{PROVIDERS.get(k, k)}：{v}" for k, v in errors.items())
    return [], STATUS_EMPTY, ""


def search_stock_news(code: str, name: str, config: dict, limit: int = 5,
                      sector_terms: Iterable[str] = ()) -> list[SearchResult]:
    """搜索个股最新消息：过滤股吧、行情页等低质量页面，按相关度（直接 → 行业 → 宏观）排序，结果带 relevance。"""
    from src.collectors.news_relevance import rank_news

    query = f"{name} {code} 最新消息".strip()
    found = search(query, config, max_results=max(limit * 2, 8))
    by_title: dict[str, SearchResult] = {}
    for r in found:
        by_title.setdefault(r.title, r)
    items = [{"title": r.title, "snippet": r.snippet, "url": r.url, "source": r.source, "_r": r} for r in by_title.values()]
    ranked = rank_news(items, code, name, sector_terms)
    out = []
    for it in ranked:
        rel = it["relevance"]
        if rel["category"] != "direct" and rel["score"] <= 0:
            continue
        out.append(replace(it["_r"], relevance=rel))
    return out[:limit]


def test_providers(config: dict, query: str) -> list[dict[str, Any]]:
    """对每个已配置的 provider 单独请求一次（不走缓存和熔断，也不记入健康状态），供设置页测试。"""
    limit, days = _int(config, "max_results", 8), _int(config, "days", 7)
    rows = []
    for provider in configured_providers(config):
        row: dict[str, Any] = {"provider": provider, "label": PROVIDERS[provider], "ok": False, "count": 0, "error": "", "samples": []}
        try:
            results = _filter(_request_provider(provider, query, limit, days, config), days, limit)
            row.update(ok=bool(results), count=len(results), samples=[r.title for r in results[:3]],
                       error="" if results else "请求成功但没有结果")
        except Exception as e:
            row["error"] = (str(e) or type(e).__name__)[:200]
        rows.append(row)
    return rows
