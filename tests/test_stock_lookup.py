"""个股查询与按需数据：日线按需补齐、个股新闻与公告、股票搜索。"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import pandas as pd
import pytest

from src.collectors import daily_history as dh
from src.collectors import source_chain as sc
from src.collectors import stock_news as sn
from src.collectors.source_chain import source_health
from src.database import db as db_module
from src.database.db import get_db_session, init_db
from src.database.models import StockDaily, StockInfo

NOW = datetime(2026, 9, 28, 10, 0)


def _reset_db_engine():
    if db_module._engine:
        db_module._engine.dispose()
    db_module._engine = None
    db_module._SessionFactory = None


@pytest.fixture(autouse=True)
def _fresh_state():
    sc._breakers.clear()
    sc._last_good.clear()
    source_health.reset()
    dh._failed_at.clear()
    sn.reset_cache()
    yield
    sc._breakers.clear()
    sc._last_good.clear()
    source_health.reset()
    dh._failed_at.clear()
    sn.reset_cache()


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "lookup.db")
    _reset_db_engine()
    init_db(path)
    yield path
    _reset_db_engine()


# ---------- 日线按需补齐 ----------

def _daily_df(dates: list[str]) -> pd.DataFrame:
    n = len(dates)
    return pd.DataFrame({"date": dates, "open": [10.0] * n, "close": [10.0 + i * 0.1 for i in range(n)], "high": [10.5] * n,
                         "low": [9.8] * n, "volume": [1e6] * n, "turnover": [0.02] * n, "amount": [1e7] * n})


def test_ensure_daily_history_fills_missing_days(db_path, monkeypatch):
    dates = [d.strftime("%Y-%m-%d") for d in pd.bdate_range(end="2026-09-25", periods=70)]
    with get_db_session(db_path) as session:  # 本地已有最近 3 天（实时行情写入的格式带前缀）
        for d in dates[-3:]:
            session.add(StockDaily(code="sh600519", name="贵州茅台", trade_date=d, close=1500.0, change_pct=1.0))
    calls = []
    monkeypatch.setattr(dh, "fetch_daily_df_with_fallback", lambda code, start, end: calls.append((code, start, end)) or ("tx", _daily_df(dates)))

    added = dh.ensure_daily_history("600519", db_path, name="贵州茅台", now=NOW)
    start = (NOW - timedelta(days=dh.ENSURE_CALENDAR_DAYS)).strftime("%Y-%m-%d")
    assert calls == [("600519", (NOW - timedelta(days=dh.ENSURE_CALENDAR_DAYS + dh.CHANGE_LOOKBACK_DAYS)).strftime("%Y-%m-%d"), "2026-09-27")]
    expected = [d for d in dates if d >= start][:-3]  # 已有的 3 天不重复写入
    assert added == len(expected)
    with get_db_session(db_path) as session:
        rows = session.query(StockDaily.code, StockDaily.trade_date, StockDaily.amount).order_by(StockDaily.trade_date).all()
    assert [r.trade_date for r in rows if r.code == "600519"] == expected
    assert rows[0].amount == 1e7

    # 已经够 60 根：不再联网
    assert dh.ensure_daily_history("600519", db_path, now=NOW) == 0 and len(calls) == 1
    assert dh.ensure_daily_history("abc", db_path, now=NOW) == 0


def test_ensure_daily_history_backs_off_after_failure(db_path, monkeypatch):
    calls = []

    def boom(code, start, end):
        calls.append(code)
        source_health.record("个股日线", "tx", False, "timeout")
        raise RuntimeError("tx empty | daily empty | em: timeout")

    monkeypatch.setattr(dh, "fetch_daily_df_with_fallback", boom)
    assert dh.ensure_daily_history("000001", db_path, now=NOW) == 0
    assert dh.ensure_daily_history("000001", db_path, now=NOW + timedelta(minutes=10)) == 0  # 30 分钟内不重试
    assert dh.ensure_daily_history("000001", db_path, now=NOW + timedelta(minutes=31)) == 0
    assert calls == ["000001", "000001"]
    assert source_health.snapshot()[0]["dataset"] == "个股日线"
    assert source_health.snapshot()[0]["source"] == "tx"


def test_history_end_date_waits_for_close():
    assert dh._history_end_date(datetime(2026, 9, 28, 15, 0)) == "2026-09-27"
    assert dh._history_end_date(datetime(2026, 9, 28, 15, 30)) == "2026-09-28"


# ---------- 个股新闻与公告 ----------

class _Resp:
    def __init__(self, text: str):
        self.text = text

    def json(self):
        return json.loads(self.text)


NEWS_PAYLOAD = {"code": 0, "result": {"cmsArticleWebOld": [
    {"date": "2026-09-27 17:05:00", "mediaName": "证券时报网", "title": "航运概念涨0.35%", "url": "http://a/1"},
    {"date": "2026-09-10 09:00:00", "mediaName": "财中社", "title": "旧闻", "url": "http://a/2"},
]}}
NOTICE_PAYLOAD = {"data": {"list": [
    {"notice_date": "2026-09-22 00:00:00", "title": "中远海控:关于调整回购股份价格上限的公告", "art_code": "AN1",
     "columns": [{"column_name": "回购方案修订"}]},
    {"notice_date": "2026-09-20 00:00:00", "title": "中远海控:关于收到中国证监会立案告知书的公告", "art_code": "AN2", "columns": []},
    {"notice_date": "2026-08-01 00:00:00", "title": "中远海控:关于股东减持计划的公告", "art_code": "AN3", "columns": []},
]}}


def _fake_get(calls: list, notices_ok: bool = True):
    def get(url, params, referer):
        calls.append(url)
        if url == sn.NEWS_URL:
            assert json.loads(params["param"])["keyword"] == "601919"
            return _Resp("jQuery1(" + json.dumps(NEWS_PAYLOAD, ensure_ascii=False) + ")")
        if not notices_ok:
            raise RuntimeError("503")
        assert params["stock_list"] == "601919"
        return _Resp(json.dumps(NOTICE_PAYLOAD, ensure_ascii=False))
    return get


def test_stock_news_and_notices(monkeypatch):
    calls = []
    monkeypatch.setattr(sn, "_get", _fake_get(calls))
    result = sn.get_stock_news("sh601919", now=NOW)

    assert [(n["date"], n["source"], n["title"]) for n in result["news"]] == [("2026-09-27 17:05", "证券时报网", "航运概念涨0.35%")]
    notices = result["notices"]
    assert [n["url"][-8:] for n in notices] == ["AN1.html", "AN2.html"]  # 30 天以前的公告不要
    assert notices[0]["source"] == "回购方案修订" and notices[0]["url"].endswith("/601919/AN1.html") and not notices[0]["risk"]
    assert (notices[1]["risk"], notices[1]["severe"]) == ("立案", True)

    sn.get_stock_news("601919", now=NOW + timedelta(minutes=10))  # 30 分钟内用缓存
    assert len(calls) == 2
    sn.get_stock_news("601919", refresh=True, now=NOW)
    assert len(calls) == 4


def test_stock_news_partial_failure_is_not_cached(monkeypatch):
    calls = []
    monkeypatch.setattr(sn, "_get", _fake_get(calls, notices_ok=False))
    result = sn.get_stock_news("601919", now=NOW)
    assert len(result["news"]) == 1 and result["notices"] == []
    sn.get_stock_news("601919", now=NOW)
    assert calls.count(sn.NOTICE_URL) == 2  # 公告失败不缓存，下次重试


def test_empty_news_is_not_a_source_failure(monkeypatch):
    monkeypatch.setattr(sn, "fetch_stock_news", lambda code: [])
    monkeypatch.setattr(sn, "fetch_stock_notices", lambda code: [])
    for i in range(5):
        sn.get_stock_news(f"60000{i}", now=NOW)
    assert all(r["status"] == "ok" for r in source_health.snapshot())  # 没有新闻的股票不会触发熔断


def test_classify_notice():
    assert sn.classify_notice("关于公司股票被实施退市风险警示的公告") == ("退市风险", True)
    assert sn.classify_notice("关于股票交易异常波动的公告") == ("异常波动", False)
    assert sn.classify_notice("2026年半年度报告") == ("", False)


# ---------- 股票搜索 ----------

@pytest.fixture
def search(db_path):
    from src.services.stock_search import StockSearch

    StockSearch.reset()
    with get_db_session(db_path) as session:
        session.add(StockInfo(code="600519", name="贵州茅台", exchange="sh"))
        session.add(StockInfo(code="601919", name="中远海控", exchange="sh"))
        session.add(StockInfo(code="603297", name="永新光学", exchange="sh"))
        # 行情里的名称作为补充（stock_info 没有的股票）；代码带前缀也能识别
        session.add(StockDaily(code="sz002594", name="比亚迪", trade_date="2026-09-25", close=300.0))
        session.add(StockDaily(code="600519", name="旧名称", trade_date="2026-09-25", close=1500.0))
        session.add(StockDaily(code="300001", name="朝阳科技", trade_date="2026-09-25", close=20.0))
    yield StockSearch(db_path)
    StockSearch.reset()


def test_stock_search(search):
    assert search.search("600519") == [{"code": "600519", "name": "贵州茅台", "kind": "stock"}]   # 交易所简称优先于行情名称
    assert search.search("sh600519")[0]["code"] == "600519"
    assert [r["code"] for r in search.search("60")][:3] == ["600519", "601919", "603297"]   # 前缀匹配的个股排在前面
    assert search.search("茅台") == [{"code": "600519", "name": "贵州茅台", "kind": "stock"}]
    assert search.search("gzmt")[0]["name"] == "贵州茅台"
    assert search.search("ZYHK")[0]["name"] == "中远海控"
    assert search.search("byd")[0]["code"] == "002594"
    assert search.search("cykj")[0]["code"] == "300001"                         # 多音字：朝 zhao/chao
    assert search.search("zykj")[0]["code"] == "300001"
    assert search.search("") == [] and search.search("不存在的股票") == []


def test_stock_search_aliases(search):
    """常用简称不是全称的连续子串（招行、宁王），按名称匹配搜不到"""
    from src.database.models import StockInfo as SI
    from src.services.stock_search import StockSearch

    with get_db_session(search.db_path) as session:
        session.add(SI(code="600036", name="招商银行", exchange="sh"))
        session.add(SI(code="300750", name="宁德时代", exchange="sz"))
        session.add(SI(code="600037", name="歌华有线", exchange="sh"))
    StockSearch.reset()
    assert search.search("招行")[0] == {"code": "600036", "name": "招商银行", "kind": "stock"}
    assert search.search("宁王")[0]["code"] == "300750"
    assert search.search("茅子")[0]["code"] == "600519"
    assert search.search("宁德")[0]["code"] == "300750"


def test_name_initials():
    from src.services.stock_search import name_initials

    assert name_initials("贵州茅台")[0] == "gzmt"
    assert name_initials("*ST东园")[0] == "stdy"
    assert "cykj" in name_initials("朝阳科技")
