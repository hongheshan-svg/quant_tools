"""RSS/Atom 资讯源：解析、采集、入库去重、调度与舆情上下文（全部离线）"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from src import scheduler
from src.collectors import circuit_breaker as cb_mod  # noqa: F401
from src.collectors import rss as rss_mod
from src.collectors import source_chain as sc
from src.collectors.base import BaseCollector
from src.collectors.rss import RSSCollector, parse_feed, save_items
from src.collectors.source_chain import source_health
from src.database import db as db_module
from src.database.db import get_db_session, init_db
from src.database.models import FinanceNews


@pytest.fixture(autouse=True)
def _fresh_state():
    sc._breakers.clear()
    sc._last_good.clear()
    source_health.reset()
    yield
    sc._breakers.clear()
    sc._last_good.clear()
    source_health.reset()


def _reset_db_engine():
    if db_module._engine:
        db_module._engine.dispose()
    db_module._engine = None
    db_module._SessionFactory = None


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "rss.db")
    _reset_db_engine()
    init_db(path)
    yield path
    _reset_db_engine()


RSS2 = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>测试频道</title>
<item><title>央行降准</title><link>http://x.com/1</link>
<description><![CDATA[<p>央行&nbsp;宣布<b>降准</b> 0.5 个百分点</p>]]></description>
<pubDate>Mon, 29 Sep 2026 10:30:00 +0800</pubDate></item>
<item><title><![CDATA[A股 &amp; 港股]]></title><link>http://x.com/2</link><description>&lt;div&gt;实体&lt;/div&gt;文字</description>
<pubDate>Mon, 29 Sep 2026 02:30:00 GMT</pubDate></item>
</channel></rss>"""

ATOM = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"><title>Atom 频道</title>
<entry><title>条目一</title><link rel="self" href="http://a.com/self"/><link rel="alternate" href="http://a.com/1"/>
<summary>摘要一</summary><updated>2026-09-29T10:30:00Z</updated></entry>
<entry><title>条目二</title><link href="http://a.com/2"/><content type="html">&lt;p&gt;正文二&lt;/p&gt;</content>
<published>2026-09-29T10:30:00+08:00</published></entry>
</feed>"""

RDF = """<?xml version="1.0"?>
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" xmlns="http://purl.org/rss/1.0/"
 xmlns:dc="http://purl.org/dc/elements/1.1/">
<channel><title>RDF 频道</title></channel>
<item><title>RDF 条目</title><link>http://r.com/1</link><description>rdf 摘要</description>
<dc:date>2026-09-29T09:00:00+08:00</dc:date></item></rdf:RDF>"""


# ---------- parse_feed ----------

def test_rss2_basic_and_cdata_entities():
    title, items = parse_feed(RSS2)
    assert title == "测试频道" and len(items) == 2
    first, second = items
    assert first["title"] == "央行降准" and first["url"] == "http://x.com/1"
    assert "<" not in first["summary"] and "降准" in first["summary"]
    assert second["title"] == "A股 & 港股"                      # CDATA 里的 &amp; 原样保留或解码均可
    assert "<div>" not in second["summary"] and "实体" in second["summary"]


def test_rss2_timezones_are_naive_datetimes():
    _, items = parse_feed(RSS2)
    for it in items:
        assert isinstance(it["published"], datetime) and it["published"].tzinfo is None
    # +0800 与 GMT 表示同一时刻的两种写法：差值应为 8 小时的整数倍换算后相等或本地化后不 crash
    assert items[0]["published"].year == 2026 and items[0]["published"].month == 9


def test_atom_link_and_dates():
    title, items = parse_feed(ATOM)
    assert title == "Atom 频道" and len(items) == 2
    assert items[0]["url"] == "http://a.com/1"                  # 优先 rel=alternate
    assert items[0]["summary"] == "摘要一"
    assert items[1]["url"] == "http://a.com/2"                  # 无 rel 取第一个 href
    assert "正文二" in items[1]["summary"] and "<p>" not in items[1]["summary"]
    for it in items:
        assert it["published"] is not None and it["published"].tzinfo is None


def test_rdf_rss1():
    title, items = parse_feed(RDF)
    assert title == "RDF 频道" and len(items) == 1
    assert items[0]["title"] == "RDF 条目" and items[0]["url"] == "http://r.com/1"
    assert items[0]["published"] is not None and items[0]["published"].tzinfo is None


def test_missing_title_link_and_date():
    text = """<rss version="2.0"><channel><title>t</title>
    <item><link>http://x.com/only-link</link></item>
    <item><title>只有标题</title></item>
    <item><title>完整</title><link>http://x.com/3</link></item></channel></rss>"""
    _, items = parse_feed(text)
    by_title = {it["title"]: it for it in items}
    # 缺标题的条目可以被丢弃；保留下来的必须字段齐全
    assert "只有标题" in by_title and by_title["只有标题"]["url"] in ("", None)
    assert by_title["只有标题"]["published"] is None
    assert by_title["完整"]["url"] == "http://x.com/3"
    for it in items:
        assert set(it) >= {"title", "url", "summary", "published"}


def test_long_summary_truncated_to_500():
    text = f"<rss><channel><title>t</title><item><title>长</title><description>{'字' * 2000}</description></item></channel></rss>"
    _, items = parse_feed(text)
    assert 0 < len(items[0]["summary"]) <= 500


def test_empty_feed():
    title, items = parse_feed('<rss version="2.0"><channel><title>空</title></channel></rss>')
    assert title == "空" and items == []


def test_invalid_xml_raises_value_error():
    with pytest.raises(ValueError):
        parse_feed("<rss><channel><title>坏")
    with pytest.raises(ValueError):
        parse_feed("this is not xml")


def test_gbk_declaration_and_bom_do_not_crash():
    gbk_text = RSS2.replace("UTF-8", "GBK")
    try:
        title, items = parse_feed(gbk_text)          # str 带 GBK 声明
        assert title == "测试频道" and len(items) == 2
    except ValueError:
        pass                                          # 不能处理时至少要抛 ValueError 而非其他异常
    try:
        title, items = parse_feed("﻿" + RSS2)   # 带 BOM
        assert len(items) == 2
    except ValueError:
        pass


# ---------- RSSCollector ----------

class _Resp:
    def __init__(self, text: str):
        self.text = text
        self.content = text.encode("utf-8")
        self.status_code = 200
        self.encoding = "utf-8"
        self.headers = {}


def _config(sources, **extra):
    return {"intelligence": {"enabled": True, "interval_minutes": 30, "max_items_per_source": 50,
                             "keep_days": 7, "sources": sources, **extra}}


def _patch_fetch(monkeypatch, mapping: dict):
    """按 URL 返回内容；值为 None 表示抓取失败，为异常实例则抛出。"""
    calls = []

    def fake(self, url, *a, **k):
        calls.append(url)
        value = mapping.get(url)
        if isinstance(value, Exception):
            raise value
        return None if value is None else _Resp(value)

    monkeypatch.setattr(BaseCollector, "fetch_url", fake)
    return calls


def test_collect_maps_items_and_respects_enabled_and_limit(monkeypatch):
    calls = _patch_fetch(monkeypatch, {"http://f1": RSS2, "http://f2": ATOM, "http://off": ATOM})
    cfg = _config([
        {"name": "源一", "url": "http://f1", "enabled": True},
        {"name": "源二", "url": "http://f2", "enabled": True},
        {"name": "关闭", "url": "http://off", "enabled": False},
    ], max_items_per_source=1)
    items = RSSCollector(cfg).collect()
    assert "http://off" not in calls
    assert len(items) == 2                                       # 每源最多 1 条
    assert {i["category"] for i in items} == {"源一", "源二"}
    for i in items:
        assert set(i) >= {"title", "content", "news_time", "url", "category"}


def test_collect_single_source_failure_isolated_and_health_recorded(monkeypatch):
    _patch_fetch(monkeypatch, {"http://ok": RSS2, "http://bad": None, "http://boom": RuntimeError("炸了"),
                               "http://xml": "not xml"})
    cfg = _config([
        {"name": "好", "url": "http://ok", "enabled": True},
        {"name": "空", "url": "http://bad", "enabled": True},
        {"name": "炸", "url": "http://boom", "enabled": True},
        {"name": "坏XML", "url": "http://xml", "enabled": True},
    ])
    items = RSSCollector(cfg).collect()
    assert items and {i["category"] for i in items} == {"好"}
    rows = [r for r in source_health.snapshot() if r["dataset"] == "rss"]
    assert rows, "应以数据集 rss 记录健康状态"
    assert sum(r["total_success"] for r in rows) >= 1 and sum(r["total_failure"] for r in rows) >= 3


def test_collect_without_sources_returns_empty(monkeypatch):
    calls = _patch_fetch(monkeypatch, {})
    assert RSSCollector(_config([])).collect() == []
    assert calls == []


# ---------- save_items ----------

def _item(title, url, category="源一", when=None):
    return {"title": title, "content": "摘要", "news_time": when or datetime.now(), "url": url, "category": category}


def test_save_items_dedupes_by_url_and_title(db_path):
    items = [_item("A", "http://x/1"), _item("A 改标题", "http://x/1"), _item("无链接", ""), _item("无链接", "")]
    assert save_items(items, db_path) == 2                       # 批内去重
    assert save_items(items + [_item("新", "http://x/2")], db_path) == 1   # 与库去重
    with get_db_session(db_path) as s:
        rows = s.query(FinanceNews).all()
        assert len(rows) == 3 and {r.source for r in rows} == {"rss"} and {r.category for r in rows} == {"源一"}


def test_save_items_old_records_do_not_dedupe(db_path):
    old = datetime.now() - timedelta(days=10)
    with get_db_session(db_path) as s:
        s.add(FinanceNews(source="rss", title="旧", url="http://x/old", category="源一", news_time=old,
                          collected_at=old, created_at=old))
    assert save_items([_item("旧", "http://x/old")], db_path, keep_days=7) == 1


def test_save_items_ignores_other_sources(db_path):
    with get_db_session(db_path) as s:
        s.add(FinanceNews(source="cailianshe", title="同链接", url="http://x/1", news_time=datetime.now()))
    assert save_items([_item("同链接", "http://x/1")], db_path) == 1


def test_save_items_empty(db_path):
    assert save_items([], db_path) == 0


# ---------- test_feed ----------

def test_test_feed_ok(monkeypatch):
    _patch_fetch(monkeypatch, {"http://f1": RSS2})
    res = rss_mod.test_feed("http://f1", {})
    assert res["ok"] is True and res["title"] == "测试频道" and res["count"] == 2 and len(res["samples"]) >= 1
    assert not res["error"]


def test_test_feed_failure(monkeypatch):
    _patch_fetch(monkeypatch, {"http://bad": None, "http://xml": "junk"})
    for url in ("http://bad", "http://xml"):
        res = rss_mod.test_feed(url, {})
        assert res["ok"] is False and res["error"]


# ---------- 调度 ----------

def test_run_rss_collection_skips_when_disabled_or_no_sources(monkeypatch):
    called = []
    monkeypatch.setattr(RSSCollector, "collect", lambda self: called.append(1) or [])
    scheduler._run_rss_collection({"intelligence": {"enabled": False, "sources": [{"name": "a", "url": "http://a", "enabled": True}]}})
    scheduler._run_rss_collection({"intelligence": {"enabled": True, "sources": []}})
    scheduler._run_rss_collection({"intelligence": {"enabled": True, "sources": [{"name": "a", "url": "http://a", "enabled": False}]}})
    scheduler._run_rss_collection({})
    assert called == []


def test_run_rss_collection_runs_and_saves(monkeypatch, db_path):
    _patch_fetch(monkeypatch, {"http://f1": RSS2})
    cfg = _config([{"name": "源一", "url": "http://f1", "enabled": True}])
    cfg["database"] = {"sqlite_path": db_path}
    scheduler._run_rss_collection(cfg)
    with get_db_session(db_path) as s:
        assert s.query(FinanceNews).filter_by(source="rss").count() == 2


def test_once_steps_collect_includes_rss():
    _, jobs = scheduler.ONCE_STEPS["collect"]
    assert scheduler._run_rss_collection in jobs


def test_build_scheduler_registers_rss_job():
    from apscheduler.schedulers.background import BackgroundScheduler

    cfg = {"intelligence": {"enabled": True, "interval_minutes": 20,
                            "sources": [{"name": "a", "url": "http://a", "enabled": True}]}}
    sched = scheduler.build_scheduler(cfg, BackgroundScheduler())
    job = sched.get_job("rss")
    assert job is not None and job.trigger.interval == timedelta(minutes=20)

    off = scheduler.build_scheduler({"intelligence": {"enabled": False, "sources": cfg["intelligence"]["sources"]}},
                                    BackgroundScheduler())
    assert off.get_job("rss") is None
    empty = scheduler.build_scheduler({"intelligence": {"enabled": True, "sources": []}}, BackgroundScheduler())
    assert empty.get_job("rss") is None


# ---------- 舆情上下文 ----------

def test_sentiment_context_includes_recent_rss(db_path):
    from src.analyzers.sentiment import SentimentAnalyzer

    now = datetime.now()
    with get_db_session(db_path) as s:
        s.add(FinanceNews(source="rss", category="路透中文", title="近期RSS要闻", url="http://x/n",
                          news_time=now, collected_at=now))
        s.add(FinanceNews(source="rss", category="路透中文", title="陈旧RSS要闻", url="http://x/o",
                          news_time=now - timedelta(days=3), collected_at=now - timedelta(days=3)))
    analyzer = SentimentAnalyzer.__new__(SentimentAnalyzer)
    import threading
    analyzer.config = {}
    analyzer.db_path = db_path
    analyzer._news_context_cache = {}
    analyzer._news_context_cache_at = {}
    analyzer._cache_lock = threading.Lock()
    text = analyzer._get_news_context()
    assert "RSS·路透中文" in text and "近期RSS要闻" in text
    assert "陈旧RSS要闻" not in text


# ---------- NewsNow 与资讯源模板 ----------

NEWSNOW = """{"status": "success", "id": "jin10", "items": [
  {"id": "1", "title": "美国9月制造业PMI终值 55.9", "pubDate": 1790862302000, "url": "https://flash.jin10.com/detail/1"},
  {"id": "2", "title": "华尔街见闻快讯", "extra": {"date": 1790860722000}, "url": "https://wallstreetcn.com/livenews/2"},
  {"id": "3", "title": "", "url": "https://x/3"},
  {"id": "4", "title": "没有时间的条目", "url": "https://x/4"}
]}"""


def test_parse_newsnow_items_and_times():
    title, items = rss_mod.parse_content(NEWSNOW)
    assert title == "NewsNow jin10"
    assert [i["title"] for i in items] == ["美国9月制造业PMI终值 55.9", "华尔街见闻快讯", "没有时间的条目"]  # 空标题跳过
    assert items[0]["published"] == datetime.fromtimestamp(1790862302) and items[1]["published"] == datetime.fromtimestamp(1790860722)
    assert items[2]["published"] is None and items[0]["url"] == "https://flash.jin10.com/detail/1"
    assert rss_mod.parse_content(RSS2)[1]                      # 非 JSON 仍按 RSS 解析
    with pytest.raises(ValueError):
        rss_mod.parse_content('{"status": "error"}')
    with pytest.raises(ValueError):
        rss_mod.parse_content("{not json")


def test_collect_newsnow_source(monkeypatch, db_path):
    url = rss_mod.NEWSNOW_BASE + "jin10"
    _patch_fetch(monkeypatch, {url: NEWSNOW})
    items = RSSCollector(_config([{"name": "金十数据", "url": url, "enabled": True}])).collect()
    assert len(items) == 3 and all(i["category"] == "金十数据" for i in items)
    assert save_items(items, db_path) == 3


def test_templates_unique_https():
    urls = [t["url"] for t in rss_mod.TEMPLATES]
    assert len(urls) == len(set(urls)) and all(u.startswith("https://") for u in urls)
    assert not any("xueqiu-hotstock" in u for u in urls)        # 条目是股票名，不是资讯
