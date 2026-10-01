"""资讯相关度分级与垃圾过滤：score_news / junk_reason / rank_news、联网搜索、个股诊断与问股工具接入（全部离线）。"""

from __future__ import annotations

import copy
from datetime import datetime, timedelta

import pandas as pd
import pytest

from src.collectors import daily_history as daily_history_mod
from src.collectors import fundamentals as fundamentals_mod
from src.collectors import news_search
from src.collectors import stock_news as stock_news_mod
from src.collectors.news_relevance import junk_reason, rank_news, score_news
from src.database import db as db_module
from src.database.db import get_db_session, init_db
from src.database.models import FinanceNews, LimitUpStock, StockDaily
from src.services.run_log import RunLog
from src.services.stock_diagnosis import StockDiagnosisService

CODE, NAME = "000002", "万科A"


def _s(title="", **kw):
    kw.setdefault("code", CODE)
    kw.setdefault("name", NAME)
    return score_news(title, **kw)


# ---------- score_news ----------

def test_score_code_in_title_snippet_url():
    r = _s("000002 今日走势")
    assert r["score"] == 55 and r["category"] == "direct" and r["label"] == "直接相关"
    assert "标题命中股票代码 000002" in r["reasons"]
    r = _s("无关标题", snippet="代码 000002 的情况")
    assert r["score"] == 34 and r["category"] == "sector"  # 34 < 38，不算直接相关
    r = _s("无关标题", url="https://example.com/sz000002.html")
    assert r["score"] == 18 and r["category"] == "sector"
    assert _s("sz000002 动态")["score"] == 55


def test_score_code_only_highest_position_counts():
    assert _s("000002", snippet="000002", url="http://a.com/000002")["score"] == 55


def test_score_name_variants():
    r = _s("万科发布公告")  # 万科A 的变体「万科」
    assert r["category"] == "direct" and "标题命中公司名 万科" in r["reasons"]
    assert _s("万科董事长表示")["score"] == 45
    assert _s("无关标题", snippet="万科的最新消息")["score"] == 28
    assert _s("万科的消息", snippet="万科的消息")["score"] == 45  # 只取最高一处
    assert _s("无关标题", snippet="万科的最新消息")["category"] == "sector"  # 28 < 38


def test_score_code_and_name_combine_and_cap():
    assert _s("万科A(000002)今日走势")["score"] == 100
    r = _s("万科000002公告", source="证券时报", snippet="")
    assert r["score"] == 100 and len(r["reasons"]) <= 3
    assert _s("无关标题", snippet="000002 万科 消息")["score"] == 62  # 34 + 28
    assert _s("无关标题", snippet="000002 万科 消息")["category"] == "direct"


def test_score_event_words_only_with_company_signal():
    assert _s("万科发布三季报")["score"] == 45 + 12
    assert "命中公司事件词" in _s("万科发布三季报")["reasons"]
    r = _s("某公司发布三季报")  # 无公司信号：事件词不加分
    assert r["score"] == 0 and "命中公司事件词" not in r["reasons"]
    for word in ("公告", "财报", "业绩", "回购", "增持", "问询函", "立案", "投资者关系"):
        assert _s(f"万科{word}")["score"] == 57, word


def test_score_authority_source_bonus():
    assert _s("万科董事长表示", source="证券时报")["score"] == 45 + 8
    assert _s("万科董事长表示", url="http://www.cninfo.com.cn/x")["score"] == 45 + 8
    assert _s("万科董事长表示", source="某自媒体")["score"] == 45
    assert _s("万科董事长表示", source="财联社", url="http://www.sse.com.cn/x")["score"] == 53  # 只加一次


def test_score_direct_threshold_boundary():
    # 信号 34（代码在摘要）低于 38：即便来源权威、分数为 42 也归 sector
    r = _s("无关标题", snippet="000002", source="证券时报")
    assert r["category"] == "sector" and r["score"] == 42
    # 信号 18 + 28 = 46 >= 38
    r = _s("无关标题", snippet="万科", url="http://a.com/000002")
    assert r["category"] == "direct" and r["score"] == 46


def test_score_macro_and_floor():
    r = _s("央行降准利好A股")
    assert r["category"] == "macro" and r["label"] == "宏观市场" and r["score"] == 0
    assert "未命中公司，归为宏观/市场新闻" in r["reasons"]
    r = _s("央行降准利好A股", source="财联社")  # 8 - 12 不低于 0
    assert r["score"] == 0 and r["category"] == "macro"
    r = _s("央行降准", url="http://x.com/000002")  # 18 - 12
    assert r["score"] == 6 and r["category"] == "macro"
    for word in ("降息", "美联储", "CPI", "PMI", "汇率", "沪指", "外资", "证监会"):
        assert _s(f"{word}最新动态")["category"] == "macro", word
    # 有公司直接信号时即便含宏观词也是 direct 且不扣分
    r = _s("万科受益于降准")
    assert r["category"] == "direct" and r["score"] == 45


def test_score_sector_terms_and_generic_words():
    r = _s("汽车整车销量走高", sector_terms=["汽车整车"])
    assert r["category"] == "sector" and r["label"] == "行业相关" and r["score"] == 6
    assert "命中所属行业/题材 汽车整车" in r["reasons"]
    r = _s("新能源产业链景气")
    assert r["score"] == 6 and "仅命中行业或板块背景" in r["reasons"]
    assert _s("光伏板块拉升")["score"] == 6
    r = _s("今日天气晴朗", sector_terms=["汽车整车", "a"])
    assert r["score"] == 0 and r["category"] == "sector"
    assert _s("字母a出现", sector_terms=["a"])["score"] == 0  # 长度不足 2 的题材词忽略


def test_score_reasons_max_three_and_int():
    r = _s("万科A 000002 年报公告", source="证券时报")
    assert len(r["reasons"]) <= 3 and isinstance(r["score"], int) and 0 <= r["score"] <= 100
    assert set(r) == {"score", "category", "label", "reasons"}


def test_score_without_code_and_name():
    r = score_news("万科发布公告")
    assert r["score"] == 0 and r["category"] == "sector"


# ---------- junk_reason ----------

@pytest.mark.parametrize("kwargs", [
    {"title": "万科A怎么样", "url": "https://guba.eastmoney.com/news,000002,1.html"},
    {"title": "万科热帖", "url": "https://tieba.baidu.com/p/123"},
    {"title": "万科好吗", "url": "https://zhidao.baidu.com/question/1.html"},
    {"title": "万科", "url": "https://baike.baidu.com/item/万科"},
    {"title": "万科前景如何", "url": "https://www.zhihu.com/question/12345"},
    {"title": "万科A(000002)股票价格_行情_走势图"},
    {"title": "万科A 实时行情"},
    {"title": "万科A股吧_东方财富网"},
    {"title": "万科A(000002)资金流向_东方财富"},
    {"title": "万科A(000002) 股票"},
    {"title": "万科A(000002)行情"},
    {"title": "今日牛股推荐"},
    {"title": "跟着老师，带你赚钱"},
    {"title": "添加加微信领取"},
    {"title": "内幕消息抢先看"},
    {"title": "这只票稳赚不赔"},
    {"title": "三个月翻倍秘籍"},
    {"title": "约炮交友"},
    {"title": "海外博彩平台"},
    {"title": "娱乐城开户"},
    {"title": "上门服务"},
])
def test_junk_detected(kwargs):
    reason = junk_reason(**kwargs)
    assert isinstance(reason, str) and reason


@pytest.mark.parametrize("title", ["一文带你看懂万科年报", "万科股价翻倍背后的逻辑", "万科空降新总裁"])
def test_normal_titles_with_ad_like_words_not_junk(title):
    assert junk_reason(title) == ""


def test_junk_in_snippet_ad():
    assert junk_reason("万科A最新", snippet="加群领取牛股推荐")


@pytest.mark.parametrize("kwargs", [
    {"title": "万科A发布三季度业绩预告"},
    {"title": "万科:关于回购股份的公告", "url": "http://www.cninfo.com.cn/new/1"},
    {"title": "央行宣布降准0.5个百分点", "source": "财联社"},
    {"title": "汽车行业销量数据出炉", "snippet": "9月销量同比增长"},
])
def test_not_junk(kwargs):
    assert junk_reason(**kwargs) == ""


@pytest.mark.parametrize("kwargs", [
    {"title": "万科A股吧热议", "source": "证券时报"},
    {"title": "万科A(000002)股票价格_行情_走势图", "source": "财联社"},
    {"title": "万科业绩说明会", "url": "http://www.cninfo.com.cn/guba"},
    {"title": "牛股推荐", "source": "上海证券报"},
    {"title": "万科增持", "url": "https://www.szse.cn/disclosure/x"},
])
def test_official_source_never_junk(kwargs):
    assert junk_reason(**kwargs) == ""


# ---------- rank_news ----------

def _items():
    return [
        {"title": "今日天气晴朗"},                                                  # 0 分 sector
        {"title": "央行降准利好A股", "url": "http://x.com/000002"},                  # macro 6
        {"title": "新能源产业链景气"},                                                # sector 6
        {"title": "万科董事长表示"},                                                  # direct 45
        {"title": "万科发布公告"},                                                    # direct 57
        {"title": "万科A股吧热议", "url": "https://guba.eastmoney.com/x"},           # 垃圾
        {"title": "汽车整车行业景气", "snippet": ""},                                 # sector 6
    ]


def test_rank_news_order_and_junk_and_zero_drop():
    out = rank_news(_items(), CODE, NAME, sector_terms=["汽车整车"])
    titles = [i["title"] for i in out]
    assert titles == ["万科发布公告", "万科董事长表示", "新能源产业链景气", "汽车整车行业景气", "央行降准利好A股"]
    assert all("relevance" in i for i in out)
    assert [i["relevance"]["category"] for i in out] == ["direct", "direct", "sector", "sector", "macro"]
    assert "今日天气晴朗" not in titles and "万科A股吧热议" not in titles


def test_rank_news_same_score_keeps_original_order():
    items = [{"title": "万科董事长甲表示"}, {"title": "万科董事长乙表示"}, {"title": "万科董事长丙表示"}]
    assert [i["title"] for i in rank_news(items, CODE, NAME)] == [i["title"] for i in items]


def test_rank_news_all_zero_kept():
    items = [{"title": "今日天气晴朗"}, {"title": "明日天气多云"}]
    out = rank_news(items, CODE, NAME)
    assert [i["title"] for i in out] == ["今日天气晴朗", "明日天气多云"]


def test_rank_news_only_positive_non_direct_drops_zero():
    items = [{"title": "今日天气晴朗"}, {"title": "新能源产业链景气"}]
    assert [i["title"] for i in rank_news(items, CODE, NAME)] == ["新能源产业链景气"]


def test_rank_news_limit_and_empty():
    assert len(rank_news(_items(), CODE, NAME, limit=2)) == 2
    assert [i["title"] for i in rank_news(_items(), CODE, NAME, limit=1)] == ["万科发布公告"]
    assert rank_news([], CODE, NAME) == []
    assert rank_news([{"title": "万科A股吧热议", "url": "https://guba.eastmoney.com/x"}], CODE, NAME) == []


def test_rank_news_does_not_mutate_input():
    items = _items()
    snapshot = copy.deepcopy(items)
    out = rank_news(items, CODE, NAME)
    assert items == snapshot and all("relevance" not in i for i in items)
    assert out is not items


# ---------- search_stock_news ----------

R = news_search.SearchResult


def test_searchresult_relevance_default_none():
    assert R(title="t", url="u").relevance is None


def test_search_stock_news_filter_rank_relevance(monkeypatch):
    found = [
        R(title="今日天气晴朗", url="http://w.com/0", snippet="无关内容", provider="bocha"),
        R(title="万科A股吧热议", url="https://guba.eastmoney.com/x", snippet="万科", provider="bocha"),
        R(title="地产行业动态", url="http://w.com/1", snippet="000002 的最新情况", provider="bocha"),
        R(title="万科发布三季报", url="http://w.com/2", snippet="摘要", provider="bocha"),
        R(title="万科发布三季报", url="http://w.com/2b", snippet="重复标题", provider="bocha"),
        R(title="央行降准", url="http://w.com/3", snippet="宏观", provider="bocha"),
    ]
    seen = {}

    def fake_search(query, config, **kw):
        seen["query"] = query
        return list(found)
    monkeypatch.setattr(news_search, "search", fake_search)
    out = news_search.search_stock_news(CODE, NAME, {}, limit=5)
    titles = [r.title for r in out]
    assert titles == ["万科发布三季报", "地产行业动态"]  # direct 在前；垃圾、零分、宏观零分被丢；标题去重
    assert out[0].relevance["category"] == "direct" and out[0].relevance["score"] == 57
    assert out[1].relevance["category"] == "sector" and out[1].relevance["score"] == 40  # 代码在摘要 34 + 行业词 6，仍保留
    assert CODE in seen["query"] or NAME in seen["query"]


def test_search_stock_news_limit_and_sector_terms(monkeypatch):
    found = [R(title=f"万科动态{i}", url=f"http://w.com/{i}", provider="bocha") for i in range(8)]
    found.append(R(title="汽车整车销量", url="http://w.com/x", provider="bocha"))
    monkeypatch.setattr(news_search, "search", lambda *a, **k: list(found))
    assert len(news_search.search_stock_news(CODE, NAME, {}, limit=3)) == 3
    out = news_search.search_stock_news(CODE, NAME, {}, limit=20, sector_terms=["汽车整车"])
    assert out[-1].title == "汽车整车销量" and out[-1].relevance["category"] == "sector"
    monkeypatch.setattr(news_search, "search", lambda *a, **k: [])
    assert news_search.search_stock_news(CODE, NAME, {}) == []


# ---------- 个股诊断 ----------

DAYS = [d.strftime("%Y-%m-%d") for d in pd.bdate_range(end="2026-09-25", periods=25)]
STOCK_NEWS = {
    "news": [{"kind": "新闻", "title": "比亚迪9月销量创新高", "date": "2026-09-24 18:00", "source": "证券时报网", "url": "", "risk": "", "severe": False}],
    "notices": [],
}
SEARCH_CFG = {"search": {"enabled": True, "providers": ["bocha"], "bocha": {"api_keys": "key-bocha"}, "cache_minutes": 30}}


def _reset_db_engine():
    if db_module._engine:
        db_module._engine.dispose()
    db_module._engine = None
    db_module._SessionFactory = None


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(fundamentals_mod, "fetch_chip_summary", lambda code, db_path: None)
    monkeypatch.setattr(fundamentals_mod.EarningsCache, "get", classmethod(lambda cls, code: None))
    monkeypatch.setattr(stock_news_mod, "get_stock_news", lambda code, refresh=False, now=None: {k: list(v) for k, v in STOCK_NEWS.items()})
    monkeypatch.setattr(daily_history_mod, "ensure_daily_history", lambda code, db_path, name="", min_bars=60, now=None: 0)
    news_search.reset_state()
    yield
    news_search.reset_state()


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "rel.db")
    _reset_db_engine()
    init_db(path)
    now = datetime.now()
    with get_db_session(path) as session:
        for i, d in enumerate(DAYS):
            close = 14 + i * 0.4
            session.add(StockDaily(code="sz002594", name="比亚迪", trade_date=d, open=close - 0.2, close=close,
                                   change_pct=2.0, volume=1000 + i, amount=5e9, turnover=3.2, circ_mv=6e11))
        session.add(LimitUpStock(code="002594", name="比亚迪", trade_date="2026-09-25", continuous_days=2, sector="汽车整车",
                                 first_limit_time="09:35", open_count=0, seal_amount=3e8, reason="固态电池"))
        session.add(FinanceNews(source="cailianshe", title="比亚迪固态电池量产提速", collected_at=now))
        session.add(FinanceNews(source="cailianshe", title="比亚迪股吧热议 加微信带你翻倍", collected_at=now))
        for i in range(5):  # 题材相关、不含本股名称，只应保留最近 3 条
            session.add(FinanceNews(source="cailianshe", title=f"固态电池行业新进展{i}", collected_at=now - timedelta(minutes=i + 1)))
        session.add(FinanceNews(source="cailianshe", title="固态电池太旧的新闻", collected_at=now - timedelta(days=10)))
        session.add(FinanceNews(source="cailianshe", title="完全无关的新闻", collected_at=now))
    yield path
    _reset_db_engine()


def _service(db_path, extra=None):
    cfg = {"database": {"sqlite_path": db_path}, "risk": {}, "trading": {}, **(extra or {})}
    return StockDiagnosisService(cfg, llm=None)


def _news_line(text: str) -> str:
    return next(line for line in text.splitlines() if line.startswith("【相关资讯】"))


def test_diagnosis_news_categories_junk_and_counts(db_path):
    context = _service(db_path).build_context("002594")
    line = _news_line(context["text"])
    assert "[直接]" in line and "[行业]" in line
    assert "比亚迪固态电池量产提速" in context["text"]
    assert "2026-09-24 [证券时报网] 比亚迪9月销量创新高" in context["text"]
    assert "加微信" not in context["text"] and "完全无关的新闻" not in context["text"]
    assert "太旧的新闻" not in context["text"]
    assert line.count("[行业]") == 3  # 行业背景最多 3 条
    assert "固态电池行业新进展0" in line and "固态电池行业新进展3" not in line
    assert line.index("[直接]") < line.index("[行业]") and line.rindex("[直接]") < line.index("[行业]")
    assert context["news_relevance"] == {"direct": 2, "sector": 3, "macro": 0, "dropped": 1}


def test_diagnosis_news_no_name_match_still_counts(db_path):
    with get_db_session(db_path) as session:
        session.query(FinanceNews).filter(FinanceNews.title.contains("比亚迪")).delete(synchronize_session=False)
    context = _service(db_path).build_context("002594")
    # 仅剩东方财富的 1 条直接相关资讯
    assert context["news_relevance"]["direct"] == 1 and context["news_relevance"]["dropped"] == 0


def test_diagnosis_web_search_line_with_reason_and_run_log(db_path, monkeypatch):
    seen = {}

    def fake_search(query, config, **kw):
        seen["query"] = query
        return [
            R(title="比亚迪发布新款车型", url="http://w.com/1", snippet="摘要", source="某媒体", published="2026-09-25", provider="bocha"),
            R(title="比亚迪股吧热议", url="https://guba.eastmoney.com/x", snippet="比亚迪", provider="bocha"),
        ]
    monkeypatch.setattr(news_search, "search", fake_search)
    run_log = RunLog()
    context = _service(db_path, SEARCH_CFG).build_context("002594", run_log=run_log)
    line = _news_line(context["text"])
    web = next(part for part in line.split("；") if "比亚迪发布新款车型" in part)
    assert "[直接]" in web and "[联网·某媒体]" in web and "（标题命中公司名 比亚迪）" in web
    assert "比亚迪股吧热议" not in context["text"]
    assert context["news_relevance"]["direct"] == 3  # 本地 1 + 东方财富 1 + 联网 1
    step = next(s for s in run_log.to_dict()["steps"] if s["name"] == "联网搜索")
    assert step["detail"] == "1 条（直接 1，行业 0，宏观 0，过滤 1）"


def test_diagnosis_passes_sector_terms_to_web_search(db_path, monkeypatch):
    seen = {}

    def fake_stock_news(code, name, config, limit=5, sector_terms=()):
        seen["terms"] = list(sector_terms)
        return []
    monkeypatch.setattr(news_search, "search_stock_news", fake_stock_news)
    _service(db_path, SEARCH_CFG).build_context("002594")
    assert "固态电池" in seen["terms"] and len(seen["terms"]) <= 3
    assert all(len(t) >= 2 for t in seen["terms"])


def test_diagnosis_web_search_disabled_has_no_web_step(db_path):
    run_log = RunLog()
    _service(db_path).build_context("002594", run_log=run_log)
    assert all(s["name"] != "联网搜索" for s in run_log.to_dict()["steps"])


# ---------- 问股 web_search 工具 ----------

@pytest.fixture
def chat_tools(db_path):
    from src.services.chat_tools import ChatTools

    return ChatTools({"database": {"sqlite_path": db_path}, **SEARCH_CFG})


def _fake_web(monkeypatch):
    monkeypatch.setattr(news_search, "search", lambda *a, **k: [
        R(title="比亚迪发布新款车型", url="http://w.com/1", snippet="摘要内容", source="某媒体", published="2026-09-25", provider="bocha"),
        R(title="比亚迪股吧热议", url="https://guba.eastmoney.com/x", snippet="比亚迪", provider="bocha"),
        R(title="新能源产业链景气", url="http://w.com/3", snippet="摘要", source="某媒体", published="2026-09-25", provider="bocha"),
    ])


def test_chat_web_search_with_code_annotates(chat_tools, monkeypatch):
    _fake_web(monkeypatch)
    text = chat_tools.call("web_search", {"query": "最新消息", "code": "002594"})
    assert "比亚迪发布新款车型" in text and "直接" in text and "行业" in text
    assert "比亚迪股吧热议" not in text  # 垃圾被过滤
    assert text.index("比亚迪发布新款车型") < text.index("新能源产业链景气")


def test_chat_web_search_query_only_unchanged(chat_tools, monkeypatch):
    _fake_web(monkeypatch)
    text = chat_tools.call("web_search", {"query": "比亚迪 最新消息"})
    assert "比亚迪发布新款车型" in text and "比亚迪股吧热议" in text
    assert "直接" not in text and "行业" not in text
