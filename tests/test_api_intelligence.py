"""资讯源（RSS）设置与采集接口"""

from __future__ import annotations

from datetime import datetime

from src.collectors import rss as rss_mod
from src.collectors.base import BaseCollector
from src.database.db import get_db_session
from src.database.models import FinanceNews, GlobalNews
from src import settings_store
from tests.test_api import _wait, env  # noqa: F401

VALID = {"enabled": True, "interval_minutes": 30, "max_items_per_source": 50, "keep_days": 7,
         "sources": [{"name": "源一", "url": "https://a.com/rss", "enabled": True},
                     {"name": "源二", "url": "http://b.com/atom", "enabled": False}]}


def test_get_default_shape(env):
    client, _, _ = env
    body = client.get("/api/v1/settings/intelligence").json()["intelligence"]
    for key in ("enabled", "interval_minutes", "max_items_per_source", "keep_days", "sources"):
        assert key in body
    assert isinstance(body["sources"], list)


def test_put_valid_writes_settings(env):
    client, _, _ = env
    res = client.put("/api/v1/settings/intelligence", json={"intelligence": VALID})
    assert res.status_code == 200
    written = settings_store.read_settings()["intelligence"]
    assert [s["name"] for s in written["sources"]] == ["源一", "源二"] and written["interval_minutes"] == 30
    again = client.get("/api/v1/settings/intelligence").json()["intelligence"]
    assert again["sources"][1]["enabled"] is False


def _bad(**patch):
    body = {**VALID, **patch}
    return {"intelligence": body}


def test_put_rejects_invalid(env):
    client, _, _ = env
    src = VALID["sources"]
    cases = {
        "空名称": _bad(sources=[{"name": "", "url": "https://a.com", "enabled": True}]),
        "重名": _bad(sources=[src[0], {**src[1], "name": "源一"}]),
        "非http": _bad(sources=[{"name": "x", "url": "ftp://a.com/rss", "enabled": True}]),
        "无协议": _bad(sources=[{"name": "x", "url": "a.com/rss", "enabled": True}]),
        "间隔过小": _bad(interval_minutes=4),
        "条数过大": _bad(max_items_per_source=201),
        "条数为零": _bad(max_items_per_source=0),
        "sources非列表": _bad(sources="nope"),
    }
    for label, body in cases.items():
        res = client.put("/api/v1/settings/intelligence", json=body)
        assert res.status_code == 422, label


def test_boundary_values_accepted(env):
    client, _, _ = env
    assert client.put("/api/v1/settings/intelligence", json=_bad(interval_minutes=5, max_items_per_source=1)).status_code == 200
    assert client.put("/api/v1/settings/intelligence", json=_bad(max_items_per_source=200)).status_code == 200


def test_test_endpoint(env, monkeypatch):
    client, _, _ = env
    seen = {}

    def fake(url, config=None):
        seen["url"] = url
        return {"ok": True, "title": "频道", "count": 3, "samples": [{"title": "t", "url": "u"}], "error": ""}

    monkeypatch.setattr(rss_mod, "test_feed", fake)
    res = client.post("/api/v1/settings/intelligence/test", json={"url": "https://a.com/rss"})
    assert res.status_code == 200 and res.json()["ok"] is True and res.json()["count"] == 3
    assert seen["url"] == "https://a.com/rss"


def test_test_endpoint_failure_result(env, monkeypatch):
    client, _, _ = env
    monkeypatch.setattr(rss_mod, "test_feed", lambda url, config=None: {"ok": False, "title": "", "count": 0, "samples": [], "error": "超时"})
    res = client.post("/api/v1/settings/intelligence/test", json={"url": "https://bad"})
    assert res.json()["ok"] is False and res.json()["error"] == "超时"


FEED = """<rss version="2.0"><channel><title>T</title>
<item><title>一条</title><link>http://x.com/1</link><description>d</description></item>
<item><title>两条</title><link>http://x.com/2</link><description>d</description></item></channel></rss>"""


def test_collect_rss_task(env, monkeypatch):
    client, app, config = env
    config["intelligence"] = {"enabled": True, "interval_minutes": 30, "max_items_per_source": 50, "keep_days": 7,
                              "sources": [{"name": "源一", "url": "http://f1", "enabled": True}]}

    class R:
        text = FEED
        content = FEED.encode()
        status_code = 200
        encoding = "utf-8"
        headers = {}

    monkeypatch.setattr(BaseCollector, "fetch_url", lambda self, url, *a, **k: R())
    task = client.post("/api/v1/pipeline/collect-rss").json()
    done = _wait(client, task)
    assert done["status"] == "done", done
    assert done["result"]["fetched"] == 2 and done["result"]["inserted"] == 2
    db_path = config["database"]["sqlite_path"]
    with get_db_session(db_path) as s:
        assert s.query(FinanceNews).filter_by(source="rss").count() == 2

    second = _wait(client, client.post("/api/v1/pipeline/collect-rss").json())
    assert second["result"]["fetched"] == 2 and second["result"]["inserted"] == 0    # 重复采集不再入库


def test_unified_news_shows_rss_source(env):
    client, _, config = env
    with get_db_session(config["database"]["sqlite_path"]) as s:
        s.add(FinanceNews(source="rss", category="路透中文", title="RSS 头条", url="http://x/1",
                          news_time=datetime.now(), collected_at=datetime.now()))
    rows = client.get("/api/v1/news").json()
    hit = [r for r in rows if "RSS 头条" in r["title"]]
    assert hit and "RSS·路透中文" in (hit[0]["source"] + hit[0]["title"])


def test_unified_news_tags_are_lists(env):
    """资讯流的 tags 在库里是显示用的字符串，接口要转成列表，否则 Web 资讯流页渲染出错"""
    client, _, config = env
    now = datetime.now()
    with get_db_session(config["database"]["sqlite_path"]) as s:
        s.add(GlobalNews(source="wallstreetcn", title="环球快讯一则", category="环球市场情报,港股动态",
                         importance=5, news_time=now, collected_at=now))
        s.add(FinanceNews(source="cailianshe", category="red", title="重大消息", tags="利好 | 半导体",
                          news_time=now, collected_at=now))
    rows = {r["title"]: r for r in client.get("/api/v1/news").json()}
    glob = next(r for t, r in rows.items() if "环球快讯一则" in t)
    red = next(r for t, r in rows.items() if "重大消息" in t)
    assert glob["tags"] == ["环球市场情报", "港股动态"] and glob["important"] is False
    assert red["tags"] == ["利好", "半导体"] and red["important"] is True


def test_intelligence_templates_endpoint(env):
    client, _, _ = env
    rows = client.get("/api/v1/settings/intelligence/templates").json()
    assert rows and {"id", "name", "url", "description"} <= set(rows[0])
    assert any("newsnow" in r["id"] for r in rows)
