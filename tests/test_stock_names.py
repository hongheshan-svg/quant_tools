"""股票名称规范化：normalize_name / name_variants、入库、搜索、诊断资讯匹配、启动迁移。"""
from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd
import pytest

from src.collectors import daily_history as daily_history_mod
from src.collectors import fundamentals as fundamentals_mod
from src.collectors import stock_info as stock_info_mod
from src.collectors import stock_news as stock_news_mod
from src.database import db as db_module
from src.database.db import get_db_session, init_db
from src.database.models import FinanceNews, StockDaily, StockInfo, Watchlist
from src.services.stock_diagnosis import StockDiagnosisService
from src.services.stock_search import StockSearch
from src.utils.stock_code import name_variants, normalize_name


def _reset_db_engine():
    if db_module._engine:
        db_module._engine.dispose()
    db_module._engine = None
    db_module._SessionFactory = None


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "names.db")
    _reset_db_engine()
    init_db(path)
    StockSearch.reset()
    yield path
    StockSearch.reset()
    _reset_db_engine()


# ---------- 纯函数 ----------

@pytest.mark.parametrize("raw,expected", [
    ("万  科Ａ", "万科A"),
    ("南  玻Ａ", "南玻A"),
    ("深 赛 格", "深赛格"),
    ("＊ＳＴ 华 微", "*ST华微"),
    ("万　科　Ａ", "万科A"),
    (" 贵州茅台 ", "贵州茅台"),
    ("贵州茅台", "贵州茅台"),
    ("Ａ\tＢ\n", "AB"),
    ("TCL 科技", "TCL科技"),
    (None, ""),
    ("", ""),
    ("   ", ""),
])
def test_normalize_name(raw, expected):
    assert normalize_name(raw) == expected


def test_normalize_name_idempotent():
    for raw in ("万  科Ａ", "＊ＳＴ 华 微", None):
        once = normalize_name(raw)
        assert normalize_name(once) == once


@pytest.mark.parametrize("name,expected", [
    ("万科A", ["万科A", "万科"]),
    ("万  科Ａ", ["万科A", "万科"]),
    ("*ST华微", ["*ST华微", "华微"]),
    ("XD万科A", ["XD万科A", "万科A", "万科"]),
    ("N大普", ["N大普", "大普"]),
    ("TCL科技", ["TCL科技"]),
    ("比亚迪", ["比亚迪"]),
    ("ST华微", ["ST华微", "华微"]),
    ("XR贵州茅台", ["XR贵州茅台", "贵州茅台"]),
    ("DR万科", ["DR万科", "万科"]),
    ("C某某", ["C某某", "某某"]),
    ("深赛格B", ["深赛格B", "深赛格"]),
])
def test_name_variants_examples(name, expected):
    assert name_variants(name) == expected


@pytest.mark.parametrize("name", [None, "", "  ", "A", "ST", "*ST", "N"])
def test_name_variants_degenerate(name):
    variants = name_variants(name)
    assert all(len(v) >= 2 for v in variants)
    assert len(variants) == len(set(variants))
    if not name or len(normalize_name(name)) < 2:
        assert variants == []


def test_name_variants_short_names_no_short_variants():
    # 去掉前缀/后缀后不足 2 字的变体必须丢弃
    assert name_variants("ST华") == ["ST华"]
    assert name_variants("万A") == ["万A"]
    assert name_variants("N大") == ["N大"]
    for n in ("ST华", "万A", "N大", "*ST宏", "AB"):
        assert all(len(v) >= 2 for v in name_variants(n))


def test_name_variants_english_name_untouched():
    assert name_variants("CATL") == ["CATL"]
    assert name_variants("NAS") == ["NAS"]  # N 后不是汉字
    assert name_variants("ABCA") == ["ABCA"]  # A 前不是汉字


# ---------- 采集器 ----------

def test_stock_info_collect_normalizes_names(monkeypatch):
    sz = pd.DataFrame({"A股代码": ["000002", "000004"], "A股简称": ["万  科Ａ", "＊ＳＴ 国 华"],
                       "A股上市日期": ["1991-01-29", "1991-01-14"]})
    sh = pd.DataFrame({"证券代码": ["600519"], "证券简称": ["贵州茅台"], "上市日期": ["2001-08-27"]})
    bj = pd.DataFrame({"证券代码": ["920001"], "证券简称": ["纬 达 光 电"], "上市日期": ["2024-01-01"]})
    monkeypatch.setattr(stock_info_mod.ak, "stock_info_sh_name_code", lambda **kw: sh)
    monkeypatch.setattr(stock_info_mod.ak, "stock_info_sz_name_code", lambda **kw: sz)
    monkeypatch.setattr(stock_info_mod.ak, "stock_info_bj_name_code", lambda **kw: bj)
    result = {r["code"]: r["name"] for r in stock_info_mod.StockInfoCollector().collect()}
    assert result["000002"] == "万科A"
    assert result["000004"] == "*ST国华"
    assert result["600519"] == "贵州茅台"
    assert result["920001"] == "纬达光电"


# ---------- 股票搜索（读取兜底） ----------

def test_stock_search_handles_unnormalized_names(db_path):
    with get_db_session(db_path) as session:
        session.add(StockInfo(code="000002", name="万  科Ａ", exchange="sz", updated_at=datetime.now()))
        session.add(StockInfo(code="000012", name="南  玻Ａ", exchange="sz", updated_at=datetime.now()))
        session.add(StockDaily(code="000001", name="平安银行", trade_date="2026-09-25", close=10, open=10))
    search = StockSearch(db_path)
    for query in ("万科", "万 科", "万科Ａ", "万科A", "wk", "000002"):
        hits = search.search(query)
        match = [h for h in hits if h["code"] == "000002"]
        assert match, query
        assert match[0]["name"] == "万科A", query
    assert [h["name"] for h in search.search("南玻")] == ["南玻A"]
    assert search.search("平安")[0]["name"] == "平安银行"


def test_stock_search_daily_name_also_normalized(db_path):
    with get_db_session(db_path) as session:
        session.add(StockDaily(code="sz000002", name="万  科Ａ", trade_date="2026-09-25", close=10, open=10))
    hits = StockSearch(db_path).search("万科")
    assert hits and hits[0]["code"] == "000002" and hits[0]["name"] == "万科A"


def test_stock_search_blank_query(db_path):
    assert StockSearch(db_path).search("   ") == []


# ---------- 启动迁移 ----------

def test_init_db_migrates_stock_info_and_watchlist_names(tmp_path):
    path = str(tmp_path / "mig.db")
    _reset_db_engine()
    init_db(path)
    with get_db_session(path) as session:
        session.add(StockInfo(code="000002", name="万  科Ａ", exchange="sz", updated_at=datetime.now()))
        session.add(StockInfo(code="600519", name="贵州茅台", exchange="sh", updated_at=datetime.now()))
        session.add(Watchlist(code="000012", name="南  玻Ａ"))
        session.add(Watchlist(code="000999", name=None))
        session.add(StockDaily(code="sz000002", name="万  科Ａ", trade_date="2026-09-25", close=1, open=1))
    _reset_db_engine()
    init_db(path)
    with get_db_session(path) as session:
        assert {r.code: r.name for r in session.query(StockInfo).all()} == {"000002": "万科A", "600519": "贵州茅台"}
        assert {r.code: r.name for r in session.query(Watchlist).all()} == {"000012": "南玻A", "000999": None}
        # 契约：不处理 stock_daily
        assert session.query(StockDaily.name).scalar() == "万  科Ａ"
    # 再次启动幂等
    _reset_db_engine()
    init_db(path)
    with get_db_session(path) as session:
        assert session.query(StockInfo.name).filter(StockInfo.code == "000002").scalar() == "万科A"
    _reset_db_engine()


# ---------- 预测校验 ----------

def test_validate_predictions_uses_normalized_name(db_path):
    from src.services.premarket_predictor import LimitUpPredictor

    with get_db_session(db_path) as session:
        session.add(StockDaily(code="sz000002", name="万  科Ａ", trade_date="2026-09-25", close=10, open=10))
    predictor = LimitUpPredictor.__new__(LimitUpPredictor)
    predictor.db_path = db_path
    result = predictor._validate_predictions([{"code": "000002", "name": "万科"}])
    assert result and result[0]["name"] == "万科A"


# ---------- 个股诊断资讯匹配 ----------

@pytest.fixture
def offline(monkeypatch):
    monkeypatch.setattr(fundamentals_mod, "fetch_chip_summary", lambda code, db_path: None)
    monkeypatch.setattr(fundamentals_mod.EarningsCache, "get", classmethod(lambda cls, code: None))
    monkeypatch.setattr(stock_news_mod, "get_stock_news", lambda code, refresh=False, now=None: {"news": [], "notices": []})
    monkeypatch.setattr(daily_history_mod, "ensure_daily_history", lambda code, db_path, name="", min_bars=60, now=None: 0)


def test_diagnosis_context_matches_news_with_unnormalized_name(db_path, offline):
    days = [d.strftime("%Y-%m-%d") for d in pd.bdate_range(end="2026-09-25", periods=25)]
    with get_db_session(db_path) as session:
        for i, d in enumerate(days):
            session.add(StockDaily(code="sz000002", name="万  科Ａ", trade_date=d, open=9.8 + i * 0.1,
                                   close=10 + i * 0.1, change_pct=1.0, volume=1000, amount=1e9, turnover=1.0, circ_mv=1e11))
        session.add(FinanceNews(source="cailianshe", title="万科发布三季报", collected_at=datetime.now()))
        session.add(FinanceNews(source="cailianshe", title="无关新闻", collected_at=datetime.now()))
    service = StockDiagnosisService({"database": {"sqlite_path": db_path}, "risk": {}, "trading": {}}, llm=None)
    context = service.build_context("000002")
    assert context["name"] == "万科A"
    assert "万科发布三季报" in context["text"]
    assert "无关新闻" not in context["text"]
    assert "万科A(000002)" in context["text"]


def test_diagnosis_news_match_limit_and_order(db_path, offline):
    days = [d.strftime("%Y-%m-%d") for d in pd.bdate_range(end="2026-09-25", periods=25)]
    with get_db_session(db_path) as session:
        for i, d in enumerate(days):
            session.add(StockDaily(code="sz000002", name="万  科Ａ", trade_date=d, open=10, close=10, change_pct=0,
                                   volume=1000, amount=1e9, turnover=1.0, circ_mv=1e11))
        for i in range(12):
            session.add(FinanceNews(source="x", title=f"万科新闻{i:02d}", collected_at=datetime.now() - timedelta(minutes=60 - i)))
    service = StockDiagnosisService({"database": {"sqlite_path": db_path}, "risk": {}, "trading": {}}, llm=None)
    text = service.build_context("000002")["text"]
    assert "万科新闻11" in text and "万科新闻04" in text  # 最新 8 条
    assert "万科新闻03" not in text and "万科新闻00" not in text
