"""联网新闻搜索：各 provider 解析、回退、Key 轮询与冷却、缓存、相关性过滤、诊断与问股接入（全部离线）。"""

from __future__ import annotations

from datetime import datetime, timedelta

import httpx
import pytest

from src.collectors import circuit_breaker as cb_mod  # noqa: F401
from src.collectors import news_search
from src.collectors import source_chain as sc
from src.collectors.source_chain import source_health

TODAY = datetime.now()


def _day(n: int) -> str:
    return (TODAY - timedelta(days=n)).strftime("%Y-%m-%d")


@pytest.fixture(autouse=True)
def _fresh_state():
    news_search.reset_state()
    sc._breakers.clear()
    sc._last_good.clear()
    source_health.reset()
    yield
    news_search.reset_state()
    sc._breakers.clear()
    sc._last_good.clear()
    source_health.reset()


class Resp:
    def __init__(self, data=None, status=200, text=""):
        self.status_code = status
        self._data = data
        self.text = text or ("" if data is None else str(data))

    def json(self):
        if self._data is None:
            raise ValueError("no json")
        return self._data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("err", request=None, response=self)  # type: ignore[arg-type]


class Net:
    """伪造 httpx.get / httpx.post：按 URL 里的域名分派到 handler(kwargs)，并记录所有请求。"""

    def __init__(self, monkeypatch):
        self.calls: list[dict] = []
        self.handlers: dict[str, object] = {}
        monkeypatch.setattr(httpx, "get", lambda url, **kw: self._do("GET", url, kw))
        monkeypatch.setattr(httpx, "post", lambda url, **kw: self._do("POST", url, kw))

    def _do(self, method, url, kw):
        self.calls.append({"method": method, "url": url, "kw": kw})
        for host, handler in self.handlers.items():
            if host in url:
                out = handler(kw) if callable(handler) else handler
                if isinstance(out, Exception):
                    raise out
                return out
        raise AssertionError(f"未预期的请求 {url}")

    def on(self, host, handler):
        self.handlers[host] = handler

    def count(self, host=""):
        return sum(1 for c in self.calls if host in c["url"])

    def blob(self, call) -> str:
        """请求里的全部参数拼成文本，用来断言 Key / 查询词出现在请求里，不限定放在哪个字段。"""
        return repr(call["kw"]) + call["url"]


@pytest.fixture
def net(monkeypatch):
    return Net(monkeypatch)


def cfg(providers=("bocha",), keys=None, urls=None, **search):
    """配置形状：search.<provider>.api_keys，search.searxng.base_urls。"""
    keys = keys if keys is not None else {p: "key-" + p for p in providers if p != "searxng"}
    urls = urls if urls is not None else ({"searxng": "https://sx.example.com"} if "searxng" in providers else {})
    base = {"enabled": True, "providers": list(providers), "max_results": 8, "days": 7, "cache_minutes": 30}
    for p, v in keys.items():
        base.setdefault(p, {})["api_keys"] = v
    for p, v in urls.items():
        base.setdefault(p, {})["base_urls"] = v
    base.update(search)
    return {"search": base}


def bocha_ok(items=None):
    items = items if items is not None else [
        {"name": "比亚迪销量创新高", "url": "https://a.com/1", "snippet": "摘要1", "siteName": "证券时报", "datePublished": _day(1) + "T08:00:00+08:00"}]
    return Resp({"data": {"webPages": {"value": items}}})


# ---------- 配置 ----------

def test_providers_registry():
    assert news_search.PROVIDERS == {"bocha": "博查", "tavily": "Tavily", "serpapi": "SerpAPI", "brave": "Brave", "searxng": "SearXNG"}


def test_configured_providers_order_and_filtering():
    config = cfg(("tavily", "bocha", "searxng", "brave"), keys={"tavily": "t1", "bocha": "", "brave": "your-key"},
                 urls={"searxng": "https://sx.example.com"})
    assert news_search.configured_providers(config) == ["tavily", "searxng"]


def test_keys_accept_comma_string_list_and_strip_junk():
    config = cfg(("bocha", "tavily"), keys={"bocha": " k1 , ,your-xxx, k2 ", "tavily": [" t1 ", "", "your-key"]})
    assert news_search.configured_providers(config) == ["bocha", "tavily"]
    assert news_search.configured_providers(cfg(("bocha",), keys={"bocha": "your-api-key"})) == []
    assert news_search.configured_providers(cfg(("bocha",), keys={"bocha": " , "})) == []
    assert news_search.configured_providers(cfg(("searxng",), urls={"searxng": "https://a.com, https://b.com"})) == ["searxng"]
    assert news_search.configured_providers(cfg(("searxng",), urls={"searxng": []})) == []


def test_is_enabled():
    assert news_search.is_enabled(cfg())
    assert not news_search.is_enabled(cfg(enabled=False))
    assert not news_search.is_enabled(cfg(keys={"bocha": ""}))
    assert not news_search.is_enabled({})


def test_disabled_makes_zero_requests(net):
    assert news_search.search("比亚迪", cfg(enabled=False)) == []
    assert news_search.search("比亚迪", cfg(keys={"bocha": ""})) == []
    assert news_search.search("比亚迪", {}) == []
    assert net.calls == []


# ---------- 各 provider 解析 ----------

def test_bocha_parse_and_request(net):
    net.on("bochaai.com", bocha_ok([
        {"name": "标题A", "url": "https://a.com/1", "snippet": "摘要A", "siteName": "站点A", "datePublished": _day(1) + "T08:00:00+08:00"},
        {"name": "标题B", "url": "https://a.com/2", "summary": "摘要B", "siteName": "站点B", "datePublished": _day(2) + " 10:00:00"},
        {"name": "无日期", "url": "https://a.com/3"},                 # 缺字段
        {"name": "", "url": ""},                                         # 无效项
    ]))
    results = news_search.search("比亚迪 销量", cfg(("bocha",), keys={"bocha": "bk-123"}))
    by_title = {r.title: r for r in results}
    assert set(by_title) >= {"标题A", "标题B", "无日期"}
    a = by_title["标题A"]
    assert (a.url, a.snippet, a.source, a.provider) == ("https://a.com/1", "摘要A", "站点A", "bocha")
    assert a.published == _day(1) and by_title["标题B"].published == _day(2) and by_title["标题B"].snippet == "摘要B"
    assert by_title["无日期"].published == "" and by_title["无日期"].snippet == ""
    call = net.calls[0]
    assert call["method"] == "POST" and call["url"] == "https://api.bochaai.com/v1/web-search"
    assert "Bearer bk-123" in repr(call["kw"].get("headers")) and "比亚迪 销量" in net.blob(call)


def test_tavily_parse(net):
    net.on("tavily.com", Resp({"results": [
        {"title": "T1", "url": "https://t.com/1", "content": "内容1", "published_date": "Tue, 29 Sep 2026 08:00:00 GMT"},
        {"title": "T2", "url": "https://t.com/2", "content": "内容2", "published_date": _day(1)},
        {"title": "T3", "url": "https://t.com/3"},
    ]}))
    results = news_search.search("q", cfg(("tavily",), keys={"tavily": "tk"}, days=30))
    by_title = {r.title: r for r in results}
    assert by_title["T2"].published == _day(1) and by_title["T2"].snippet == "内容2" and by_title["T2"].provider == "tavily"
    assert by_title["T3"].published == "" and by_title["T1"].url == "https://t.com/1"
    assert net.calls[0]["method"] == "POST" and net.calls[0]["url"] == "https://api.tavily.com/search"
    assert "tk" in net.blob(net.calls[0])


def test_serpapi_parse(net):
    net.on("serpapi.com", Resp({"news_results": [
        {"title": "S1", "link": "https://s.com/1", "snippet": "片段", "source": "路透", "date": _day(1)},
        {"title": "S2", "link": "https://s.com/2", "source": {"name": "彭博"}, "date": "2 hours ago"},
        {"title": "S3", "link": "https://s.com/3"},
    ]}))
    results = news_search.search("q", cfg(("serpapi",), keys={"serpapi": "sk"}))
    by_title = {r.title: r for r in results}
    assert by_title["S1"].url == "https://s.com/1" and by_title["S1"].source == "路透" and by_title["S1"].published == _day(1)
    assert "S3" in by_title and by_title["S3"].published == ""
    call = net.calls[0]
    assert call["method"] == "GET" and call["url"].startswith("https://serpapi.com/search.json") and "sk" in net.blob(call)


def test_brave_parse(net):
    net.on("brave.com", Resp({"results": [
        {"title": "B1", "url": "https://b.com/1", "description": "描述", "age": "3 hours ago", "meta_url": {"hostname": "b.com"}},
        {"title": "B2", "url": "https://b.com/2", "description": "<strong>高亮</strong>描述", "age": "2 days ago"},
        {"title": "B3", "url": "https://b.com/3"},
    ]}))
    results = news_search.search("q", cfg(("brave",), keys={"brave": "bk"}))
    by_title = {r.title: r for r in results}
    assert set(by_title) == {"B1", "B2", "B3"}
    assert by_title["B1"].snippet == "描述" and by_title["B1"].provider == "brave"
    call = net.calls[0]
    assert call["method"] == "GET" and call["url"].startswith("https://api.search.brave.com/res/v1/news/search")
    assert "X-Subscription-Token" in repr(call["kw"].get("headers")) and "bk" in repr(call["kw"].get("headers"))


def test_searxng_parse(net):
    net.on("sx.example.com", Resp({"results": [
        {"title": "X1", "url": "https://x.com/1", "content": "内容", "publishedDate": _day(1) + "T10:00:00"},
        {"title": "X2", "url": "https://x.com/2", "content": "内容2", "publishedDate": None},
    ]}))
    results = news_search.search("q", cfg(("searxng",), urls={"searxng": "https://sx.example.com/"}))
    by_title = {r.title: r for r in results}
    assert by_title["X1"].published == _day(1) and by_title["X2"].published == "" and by_title["X1"].provider == "searxng"
    call = net.calls[0]
    assert call["method"] == "GET" and call["url"].startswith("https://sx.example.com/search") and "json" in net.blob(call)


@pytest.mark.parametrize("host,provider,empty", [
    ("bochaai.com", "bocha", {"data": {"webPages": {"value": []}}}),
    ("bochaai.com", "bocha", {}),
    ("tavily.com", "tavily", {"results": []}),
    ("serpapi.com", "serpapi", {}),
    ("brave.com", "brave", {"results": []}),
])
def test_empty_or_missing_payload_gives_empty_list(net, host, provider, empty):
    net.on(host, Resp(empty))
    assert news_search.search("q", cfg((provider,))) == []


# ---------- 回退 ----------

def _two_providers(net, first_handler):
    net.on("bochaai.com", first_handler)
    net.on("tavily.com", Resp({"results": [{"title": "备用结果", "url": "https://t.com/1", "content": "c", "published_date": _day(1)}]}))
    return cfg(("bocha", "tavily"))


@pytest.mark.parametrize("first", [
    ConnectionError("boom"), httpx.TimeoutException("timeout"), Resp(status=500, text="server error"),
    Resp({"data": {"webPages": {"value": []}}}), Resp(text="not json"),
])
def test_falls_back_to_second_provider(net, first):
    config = _two_providers(net, first)
    results = news_search.search("q", config)
    assert [r.title for r in results] == ["备用结果"] and results[0].provider == "tavily"
    assert net.count("bochaai.com") >= 1 and net.count("tavily.com") == 1


def test_first_provider_success_skips_second(net):
    config = _two_providers(net, bocha_ok())
    assert news_search.search("q", config)[0].provider == "bocha"
    assert net.count("tavily.com") == 0


def test_all_fail_returns_empty_without_raising(net):
    net.on("bochaai.com", ConnectionError("x"))
    net.on("tavily.com", Resp(status=500))
    assert news_search.search("q", cfg(("bocha", "tavily"))) == []


def test_health_recorded_under_news_search(net):
    config = _two_providers(net, ConnectionError("x"))
    news_search.search("q", config)
    datasets = {r["dataset"] for r in source_health.snapshot()}
    assert "news_search" in datasets


# ---------- Key 轮询与冷却 ----------

def test_key_rotation_on_401_within_one_call(net):
    def handler(kw):
        return Resp(status=401) if "bad-key" in repr(kw.get("headers")) else bocha_ok()
    net.on("bochaai.com", handler)
    results = news_search.search("q", cfg(("bocha",), keys={"bocha": "bad-key,good-key"}))
    assert len(results) == 1
    assert net.count("bochaai.com") == 2


@pytest.mark.parametrize("status", [401, 403, 429])
def test_bad_key_cooled_down_for_later_calls(net, status):
    def handler(kw):
        return Resp(status=status) if "bad-key" in repr(kw.get("headers")) else bocha_ok()
    net.on("bochaai.com", handler)
    config = cfg(("bocha",), keys={"bocha": ["bad-key", "good-key"]})
    news_search.search("q1", config)
    before = net.count()
    news_search.search("q2", config)
    assert net.count() - before == 1        # 坏 Key 冷却中，直接用好 Key
    bad_calls = [c for c in net.calls if "bad-key" in net.blob(c)]
    assert len(bad_calls) == 1


def test_all_keys_bad_returns_empty(net):
    net.on("bochaai.com", Resp(status=401))
    assert news_search.search("q", cfg(("bocha",), keys={"bocha": "k1,k2"})) == []


def test_cooldown_expires_after_10_minutes(net, monkeypatch):
    net.on("bochaai.com", Resp(status=429))
    config = cfg(("bocha",), keys={"bocha": "only-key"})
    assert news_search.search("q1", config) == []
    assert net.count() == 1
    assert news_search.search("q2", config) == []
    assert net.count() == 1                                   # 冷却中不再请求
    import time as time_mod
    real = time_mod.monotonic()
    monkeypatch.setattr(time_mod, "monotonic", lambda: real + 601)
    news_search.search("q3", config)
    assert net.count() == 2                                   # 10 分钟后重新尝试


# ---------- 过滤、去重、上限 ----------

def test_days_filter_drops_old_but_keeps_unknown_date(net):
    net.on("bochaai.com", bocha_ok([
        {"name": "新", "url": "https://a.com/1", "datePublished": _day(1)},
        {"name": "旧", "url": "https://a.com/2", "datePublished": _day(30)},
        {"name": "无日期", "url": "https://a.com/3"},
    ]))
    titles = [r.title for r in news_search.search("q", cfg(), days=7)]
    assert "新" in titles and "无日期" in titles and "旧" not in titles
    titles = [r.title for r in news_search.search("q", cfg(), days=60, use_cache=False)]
    assert "旧" in titles


def test_days_default_from_config(net):
    net.on("bochaai.com", bocha_ok([{"name": "十天前", "url": "https://a.com/1", "datePublished": _day(10)}]))
    assert news_search.search("q", cfg(days=3)) == []
    assert len(news_search.search("q", cfg(days=15), use_cache=False)) == 1


def test_url_dedupe_and_max_results(net):
    items = [{"name": f"标题{i}", "url": f"https://a.com/{i % 6}", "datePublished": _day(1)} for i in range(12)]
    net.on("bochaai.com", bocha_ok(items))
    results = news_search.search("q", cfg(), max_results=4)
    urls = [r.url for r in results]
    assert len(urls) == len(set(urls)) and len(results) <= 4
    assert len(news_search.search("q", cfg(max_results=3), use_cache=False)) <= 3
    assert len(news_search.search("q", cfg(), use_cache=False)) == 6     # 12 条按 url 去重后 6 条


# ---------- 缓存 ----------

def test_cache_hit_skips_request_and_bypass(net):
    net.on("bochaai.com", bocha_ok())
    config = cfg()
    first = news_search.search("q", config)
    assert news_search.search("q", config) == first and net.count() == 1
    news_search.search("q", config, use_cache=False)
    assert net.count() == 2
    news_search.search("q", config, max_results=3)       # 参数不同，缓存键不同
    assert net.count() == 3


def test_empty_results_not_cached(net):
    net.on("bochaai.com", bocha_ok([]))
    news_search.search("q", cfg())
    news_search.search("q", cfg())
    assert net.count() == 2


def test_cache_expires_and_reset_state_clears(net, monkeypatch):
    net.on("bochaai.com", bocha_ok())
    config = cfg(cache_minutes=30)
    news_search.search("q", config)
    news_search.reset_state()
    news_search.search("q", config)
    assert net.count() == 2
    import time as time_mod
    real, real_mono = time_mod.time(), time_mod.monotonic()
    monkeypatch.setattr(time_mod, "monotonic", lambda: real_mono + 31 * 60)
    monkeypatch.setattr(time_mod, "time", lambda: real + 31 * 60)
    news_search.search("q", config)
    assert net.count() == 3


# ---------- 个股相关性 ----------

def test_search_stock_news_relevance_dedupe_limit(net):
    items = [
        {"name": "比亚迪9月销量创新高", "url": "https://a.com/1", "datePublished": _day(1)},
        {"name": "无关的新闻", "url": "https://a.com/2", "snippet": "讨论宁德时代", "datePublished": _day(1)},
        {"name": "行业点评", "url": "https://a.com/3", "snippet": "本周比亚迪表现强势", "datePublished": _day(1)},
        {"name": "002594 龙虎榜", "url": "https://a.com/4", "datePublished": _day(1)},
        {"name": "比亚迪9月销量创新高", "url": "https://b.com/9", "datePublished": _day(1)},   # 标题重复
    ]
    net.on("bochaai.com", bocha_ok(items))
    results = news_search.search_stock_news("002594", "比亚迪", cfg())
    titles = [r.title for r in results]
    assert "无关的新闻" not in titles
    assert titles.count("比亚迪9月销量创新高") == 1 and "行业点评" in titles and "002594 龙虎榜" in titles
    assert len(news_search.search_stock_news("002594", "比亚迪", cfg(), limit=2)) == 2


def test_search_stock_news_disabled(net):
    assert news_search.search_stock_news("002594", "比亚迪", cfg(enabled=False)) == []
    assert net.calls == []


# ---------- test_providers ----------

def test_test_providers_reports_each_provider(net):
    net.on("bochaai.com", bocha_ok())
    net.on("tavily.com", Resp(status=500, text="oops"))
    config = cfg(("bocha", "tavily", "brave"), keys={"bocha": "k", "tavily": "k", "brave": ""})
    rows = news_search.test_providers(config, "比亚迪")
    assert [r["provider"] for r in rows] == ["bocha", "tavily"]
    ok, bad = rows
    assert ok["ok"] is True and ok["count"] == 1 and ok["label"] == "博查" and ok["samples"] and not ok["error"]
    assert bad["ok"] is False and bad["count"] == 0 and bad["error"]


def test_test_providers_ignores_cache_and_enabled_flag(net):
    net.on("bochaai.com", bocha_ok())
    config = cfg()
    news_search.search("比亚迪", config)
    news_search.test_providers(config, "比亚迪")
    assert net.count() == 2


# ---------- 个股诊断接入 ----------

def _diag_service(tmp_path, config_search, name="s.db"):
    from tests.test_stock_diagnosis import DAYS, _FakeLLM, _reset_db_engine  # noqa: F401
    from src.database.db import get_db_session, init_db
    from src.database.models import StockDaily
    from src.services.stock_diagnosis import StockDiagnosisService

    path = str(tmp_path / name)
    _reset_db_engine()
    init_db(path)
    with get_db_session(path) as session:
        for i, d in enumerate(DAYS):
            session.add(StockDaily(code="sz002594", name="比亚迪", trade_date=d, open=14 + i * 0.4, close=14.2 + i * 0.4,
                                   change_pct=2.0, volume=1000 + i, amount=5e9, turnover=3.2, circ_mv=6e11))
    config = {"database": {"sqlite_path": path}, "risk": {}, "trading": {}, **config_search}
    return StockDiagnosisService(config, llm=_FakeLLM({}))


@pytest.fixture
def offline_diag(monkeypatch):
    from src.collectors import daily_history as dh
    from src.collectors import fundamentals as fm
    from src.collectors import stock_news as sn
    monkeypatch.setattr(fm, "fetch_chip_summary", lambda code, db_path: None)
    monkeypatch.setattr(fm.EarningsCache, "get", classmethod(lambda cls, code: None))
    monkeypatch.setattr(sn, "get_stock_news", lambda code, refresh=False, now=None: {"news": [], "notices": []})
    monkeypatch.setattr(dh, "ensure_daily_history", lambda code, db_path, name="", min_bars=60, now=None: 0)


def test_diagnosis_includes_web_news_when_enabled(tmp_path, monkeypatch, offline_diag):
    R = news_search.SearchResult
    hits = [R(title=f"联网标题{i}", url=f"https://w.com/{i}", snippet="s", source="站点", published=_day(1), provider="bocha") for i in range(8)]
    monkeypatch.setattr(news_search, "search_stock_news", lambda code, name, config, limit=5, sector_terms=(): hits[:limit] if limit else hits)
    service = _diag_service(tmp_path, cfg())
    text = service.build_context("002594")["text"]
    lines = [ln for ln in text.replace("；", "\n").splitlines() if "[联网·" in ln]
    assert lines and any("联网标题0" in ln for ln in lines)
    assert sum(text.count(f"联网标题{i}") for i in range(8)) <= 5
    from tests.test_stock_diagnosis import _reset_db_engine
    _reset_db_engine()


def test_diagnosis_skips_search_when_disabled(tmp_path, monkeypatch, offline_diag):
    def boom(*a, **k):
        raise AssertionError("未启用联网搜索时不应调用")
    monkeypatch.setattr(news_search, "search_stock_news", boom)
    monkeypatch.setattr(news_search, "search", boom)
    service = _diag_service(tmp_path, cfg(enabled=False))
    text = service.build_context("002594")["text"]
    assert "[联网·" not in text
    service2 = _diag_service(tmp_path, {}, "s2.db")
    assert "[联网·" not in service2.build_context("002594")["text"]
    from tests.test_stock_diagnosis import _reset_db_engine
    _reset_db_engine()


# ---------- AI 问股工具 ----------

def test_chat_tool_web_search(monkeypatch):
    from src.services import chat_tools
    from src.services.chat_tools import ChatTools

    assert chat_tools.TOOL_LABELS["web_search"] == "联网搜索"
    assert "web_search" in chat_tools.tools_prompt()
    text = ChatTools({"database": {"sqlite_path": "data/none.db"}}).call("web_search", {"query": "固态电池"})
    assert "没有配置联网搜索" in text
    text = ChatTools({"database": {"sqlite_path": "data/none.db"}, **cfg(enabled=False)}).call("web_search", {"query": "固态电池"})
    assert "没有配置联网搜索" in text

    R = news_search.SearchResult
    seen = {}

    def fake_search(query, config, **kw):
        seen["query"] = query
        return [R(title="固态电池量产提速", url="https://w.com/1", snippet="摘要内容", source="证券时报", published=_day(1), provider="bocha")]
    monkeypatch.setattr(news_search, "search", fake_search)
    text = ChatTools({"database": {"sqlite_path": "data/none.db"}, **cfg()}).call("web_search", {"query": "固态电池"})
    assert seen["query"] == "固态电池" and "固态电池量产提速" in text 

    monkeypatch.setattr(news_search, "search", lambda *a, **k: [])
    text = ChatTools({"database": {"sqlite_path": "data/none.db"}, **cfg()}).call("web_search", {"query": "固态电池"})
    assert "没有配置联网搜索" not in text and text
