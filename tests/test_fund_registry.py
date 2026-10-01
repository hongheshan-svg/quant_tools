"""ETF 与指数：注册表解析、行情采集（K 线解析、补齐）、搜索、自选股排斥。离线运行。"""

from __future__ import annotations

from datetime import datetime, timedelta

import httpx
import pandas as pd
import pytest

from src.database import db as db_module
from src.database.db import get_db_session, init_db
from src.database.models import FundDaily, FundInfo, StockDaily, StockInfo


def _reset_db_engine():
    if db_module._engine:
        db_module._engine.dispose()
    db_module._engine = None
    db_module._SessionFactory = None


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "fund.db")
    _reset_db_engine()
    init_db(path)
    from src.services.stock_search import StockSearch

    StockSearch.reset()
    with get_db_session(path) as session:
        session.add(FundInfo(code="510300", name="沪深300ETF", kind="etf", exchange="sh", updated_at=datetime.now()))
        session.add(FundInfo(code="159915", name="创业板ETF", kind="etf", exchange="sz", updated_at=datetime.now()))
        session.add(StockInfo(code="000001", name="平安银行"))
        session.add(StockInfo(code="600519", name="贵州茅台"))
        session.add(StockDaily(code="000001", name="平安银行", trade_date="2026-09-25", close=11.0, change_pct=1.0))
        session.add(StockDaily(code="600519", name="贵州茅台", trade_date="2026-09-25", close=1500.0, change_pct=1.0))
    yield path
    StockSearch.reset()
    _reset_db_engine()


# ---------- 注册表与解析 ----------

def test_indexes_table():
    from src.services.fund_registry import INDEXES

    items = {i["code"]: i for i in INDEXES}
    for code in ["sh000001", "sz399001", "sz399006", "sh000300", "sh000016", "sh000905", "sh000852",
                 "sh000688", "bj899050", "sz399673", "sh000922", "sz399303"]:
        assert code in items, code
        assert items[code]["name"] and "aliases" in items[code]
    assert items["sh000001"]["name"] == "上证指数"
    assert items["sh000300"]["name"] == "沪深300"
    assert items["sh000688"]["name"] == "科创50"


@pytest.mark.parametrize("text", ["沪深300", "hs300", "HS300", "sh000300", "000300.SH", "000300.sh"])
def test_resolve_index_variants(db_path, text):
    from src.services.fund_registry import resolve_fund

    found = resolve_fund(text, db_path)
    assert found and found["kind"] == "index" and found["code"] == "sh000300" and found["name"] == "沪深300"


def test_bare_000001_is_not_index(db_path):
    from src.services.fund_registry import resolve_fund

    found = resolve_fund("000001", db_path)
    assert found is None or found["kind"] != "index"
    # 但按名称仍能解析为上证指数
    sh = resolve_fund("上证指数", db_path)
    assert sh and sh["code"] == "sh000001" and sh["kind"] == "index"
    assert resolve_fund("sh000001", db_path)["code"] == "sh000001"


def test_resolve_etf_by_code_and_name(db_path):
    from src.services.fund_registry import resolve_fund

    by_code = resolve_fund("510300", db_path)
    assert by_code and by_code["kind"] == "etf" and by_code["code"] == "510300"
    by_name = resolve_fund("创业板ETF", db_path)
    assert by_name and by_name["kind"] == "etf" and by_name["code"] == "159915"


def test_resolve_unknown_and_stock(db_path):
    from src.services.fund_registry import resolve_fund

    assert resolve_fund("不存在的东西", db_path) is None
    assert resolve_fund("", db_path) is None
    assert resolve_fund("600519", db_path) is None  # 个股代码不是基金
    assert resolve_fund("贵州茅台", db_path) is None


def test_resolve_index_without_etf_list(tmp_path):
    from src.services.fund_registry import resolve_fund

    _reset_db_engine()
    path = str(tmp_path / "empty.db")
    init_db(path)
    try:
        assert resolve_fund("沪深300", path)["code"] == "sh000300"
        assert resolve_fund("510300", path) is None or resolve_fund("510300", path)["kind"] == "etf"
    finally:
        _reset_db_engine()


def test_is_fund_code():
    from src.services.fund_registry import is_fund_code

    assert is_fund_code("sh000300") and is_fund_code("sz399006") and is_fund_code("bj899050")
    assert not is_fund_code("600519")
    assert not is_fund_code("")


# ---------- ETF 列表刷新 ----------

_AK_FUNCS = ["fund_etf_spot_em", "fund_etf_category_sina", "fund_etf_spot_ths", "fund_exchange_rank_em", "fund_name_em"]


def _fake_etf_df():
    return pd.DataFrame({"代码": ["510500", "588000"], "名称": ["中证500ETF", "科创50ETF"],
                         "symbol": ["sh510500", "sh588000"], "name": ["中证500ETF", "科创50ETF"],
                         "基金代码": ["510500", "588000"], "基金简称": ["中证500ETF", "科创50ETF"], "基金类型": ["ETF-场内", "ETF-场内"]})


def test_refresh_etf_list_success_then_skip_within_7_days(db_path, monkeypatch):
    import akshare as ak
    from src.services import fund_registry

    calls = []
    for fn in _AK_FUNCS:
        monkeypatch.setattr(ak, fn, lambda *a, _n=fn, **k: calls.append(_n) or _fake_etf_df(), raising=False)
    with get_db_session(db_path) as session:
        session.query(FundInfo).delete()
    added = fund_registry.refresh_etf_list(db_path, force=True)
    assert added >= 1
    with get_db_session(db_path) as session:
        rows = session.query(FundInfo).all()
        assert rows and all(r.kind == "etf" for r in rows)
    calls.clear()
    fund_registry.refresh_etf_list(db_path)  # 7 天内不重复刷新
    assert calls == []


def test_refresh_etf_list_failure_keeps_old_data(db_path, monkeypatch):
    import akshare as ak
    from src.services import fund_registry

    def boom(*a, **k):
        raise RuntimeError("network down")

    for fn in _AK_FUNCS:
        monkeypatch.setattr(ak, fn, boom, raising=False)
    assert fund_registry.refresh_etf_list(db_path, force=True) == 0
    with get_db_session(db_path) as session:
        assert session.query(FundInfo).filter(FundInfo.code == "510300").count() == 1



def test_background_refresh_fills_stock_list_and_resets_search(tmp_path, monkeypatch):
    """新装（股票列表为空）时后台刷新股票列表，节假日没有行情也能按名称、拼音搜到个股"""
    import akshare as ak
    from src.collectors.stock_info import StockInfoCollector
    from src.services import fund_registry
    from src.services.stock_search import StockSearch

    path = str(tmp_path / "fresh.db")
    _reset_db_engine()
    init_db(path)
    StockSearch.reset()
    try:
        monkeypatch.setattr(fund_registry, "refresh_etf_list", lambda db_path: 0)
        assert StockSearch(path).search("茅台") == []                          # 新装：还搜不到

        sh = pd.DataFrame({"证券代码": ["600519"], "证券简称": ["贵州茅台"], "上市日期": ["2001-08-27"]})
        monkeypatch.setattr(ak, "stock_info_sh_name_code", lambda **k: sh if k.get("symbol") == "主板A股" else pd.DataFrame())
        monkeypatch.setattr(ak, "stock_info_sz_name_code", lambda **k: pd.DataFrame())
        monkeypatch.setattr(ak, "stock_info_bj_name_code", lambda **k: pd.DataFrame())
        fund_registry.refresh_etf_list_background(path).join(timeout=10)
        assert [r["code"] for r in StockSearch(path).search("gzmt")] == ["600519"]   # 刷新后立即可搜

        calls = []
        monkeypatch.setattr(StockInfoCollector, "refresh", lambda self, db_path: calls.append(db_path) or 0)
        fund_registry.refresh_etf_list_background(path).join(timeout=10)
        assert calls == []                                                     # 1 天内不重复联网
    finally:
        StockSearch.reset()
        _reset_db_engine()


def test_search_index_without_stocks_is_rebuilt_soon(db_path, monkeypatch):
    from src.services import stock_search as search_mod
    from src.services.stock_search import StockSearch

    with get_db_session(db_path) as session:
        session.query(StockInfo).delete()
        session.query(StockDaily).delete()
    assert StockSearch(db_path).search("茅台") == []
    with get_db_session(db_path) as session:
        session.add(StockInfo(code="600519", name="贵州茅台"))
    assert StockSearch(db_path).search("茅台") == []                           # 1 分钟内用缓存
    monkeypatch.setattr(search_mod, "EMPTY_INDEX_TTL_SECONDS", 0)
    assert [r["code"] for r in StockSearch(db_path).search("茅台")] == ["600519"]  # 没有个股的索引不缓存 12 小时


# ---------- K 线采集 ----------

def _kline_payload(code: str, rows: list[list], key: str = "qfqday") -> dict:
    return {"code": 0, "msg": "", "data": {code: {key: rows}}}


class _Resp:
    def __init__(self, payload, status=200):
        self._payload, self.status_code = payload, status
        import json

        self.text = json.dumps(payload)
        self.content = self.text.encode()

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("err", request=None, response=None)  # type: ignore[arg-type]


def _bars(n=70, end=None):
    end = end or (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")  # 相对当前日期，始终落在 150 天补齐窗口内
    days = [d.strftime("%Y-%m-%d") for d in pd.bdate_range(end=end, periods=n)]
    return [[d, f"{4000 + i:.2f}", f"{4001 + i:.2f}", f"{4010 + i:.2f}", f"{3990 + i:.2f}", f"{100000 + i}"] for i, d in enumerate(days)]


def test_fetch_fund_daily_parses(monkeypatch):
    from src.collectors import fund_data

    rows = _bars(5)
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _Resp(_kline_payload("sh000300", rows)))
    out = fund_data.fetch_fund_daily("sh000300", 5)
    assert len(out) == 5
    first = out[0]
    assert first["trade_date"] == rows[0][0]
    assert first["close"] == pytest.approx(4001.0)
    assert first["open"] == pytest.approx(4000.0)
    assert first["high"] == pytest.approx(4010.0) and first["low"] == pytest.approx(3990.0)
    assert first["volume"] > 0


def test_fetch_fund_daily_day_key_fallback(monkeypatch):
    from src.collectors import fund_data

    monkeypatch.setattr(httpx, "get", lambda *a, **k: _Resp(_kline_payload("sh000300", _bars(3), key="day")))
    assert len(fund_data.fetch_fund_daily("sh000300", 3)) == 3


@pytest.mark.parametrize("payload", [
    {"code": 0, "data": {}},
    {"code": 0, "data": {"sh000300": {"qfqday": []}}},
    {"code": 0, "data": {"sh000300": {}}},
    {"code": -1, "msg": "bad"},
    {"data": []},
])
def test_fetch_fund_daily_empty_or_missing(monkeypatch, payload):
    from src.collectors import fund_data

    monkeypatch.setattr(httpx, "get", lambda *a, **k: _Resp(payload))
    try:
        out = fund_data.fetch_fund_daily("sh000300", 60)
    except Exception:
        return  # 抛异常也可接受，关键是不出现未处理的 KeyError 之外的崩溃；见下方 ensure 用例
    assert out == [] or out is None or len(out) == 0


def test_fetch_fund_daily_skips_short_rows(monkeypatch):
    from src.collectors import fund_data

    rows = _bars(3) + [["2026-09-30", "1"], []]
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _Resp(_kline_payload("sh000300", rows)))
    try:
        out = fund_data.fetch_fund_daily("sh000300", 5)
    except Exception:
        pytest.fail("残缺行不应导致崩溃")
    assert len(out) >= 3 and all(r["trade_date"] for r in out)


def test_ensure_fund_daily_writes_only_missing_and_isolated(db_path, monkeypatch):
    from src.collectors import fund_data

    rows = _bars(70)
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _Resp(_kline_payload("sh000300", rows)))
    with get_db_session(db_path) as session:
        stock_count = session.query(StockDaily).count()
        session.add(FundDaily(code="sh000300", name="沪深300", trade_date=rows[-1][0], close=1.0))
    added = fund_data.ensure_fund_daily("sh000300", db_path)
    assert added >= 1
    with get_db_session(db_path) as session:
        dates = [r.trade_date for r in session.query(FundDaily).filter(FundDaily.code == "sh000300").all()]
        assert len(dates) == len(set(dates))  # 已有日期不重复写
        assert rows[-1][0] in dates
        assert session.query(StockDaily).count() == stock_count  # 不写 stock_daily
    # 数据已足够，再次调用不新增
    assert fund_data.ensure_fund_daily("sh000300", db_path) == 0


def test_ensure_fund_daily_failure_backs_off(db_path, monkeypatch):
    from src.collectors import fund_data

    calls = []

    def boom(*a, **k):
        calls.append(1)
        raise httpx.ConnectError("down")

    monkeypatch.setattr(httpx, "get", boom)
    assert fund_data.ensure_fund_daily("sh000300", db_path) == 0
    n = len(calls)
    assert n >= 1
    assert fund_data.ensure_fund_daily("sh000300", db_path) == 0
    assert len(calls) == n  # 失败后 30 分钟内不重试


# ---------- 搜索与自选股 ----------

def test_stock_search_kinds(db_path):
    from src.services.stock_search import StockSearch

    search = StockSearch(db_path)
    idx = search.search("沪深300")
    assert any(r["code"] == "sh000300" and r["kind"] == "index" for r in idx)
    etf = search.search("510300")
    assert any(r["code"] == "510300" and r["kind"] == "etf" for r in etf)
    sh = search.search("上证指数")
    assert any(r["code"] == "sh000001" and r["kind"] == "index" for r in sh)
    stocks = search.search("茅台")
    assert stocks and stocks[0]["code"] == "600519" and stocks[0].get("kind", "stock") == "stock"


def test_stock_search_stock_first(db_path):
    from src.services.stock_search import StockSearch

    # 000001：个股平安银行排在前面，且个股没有被指数覆盖
    found = StockSearch(db_path).search("000001")
    assert found and found[0]["code"] == "000001" and found[0].get("kind", "stock") == "stock"
    assert not any(r["code"] == "sh000001" and r is found[0] for r in found)


def test_watchlist_accepts_funds(db_path):
    from src.services.watchlist import WatchlistService

    service = WatchlistService({"database": {"sqlite_path": db_path}})
    for text, code in (("510300", "510300"), ("沪深300", "sh000300")):
        result = service.add(text)
        assert result.get("ok") and result["code"] == code, (text, result)
    assert not service.add("sh000300").get("ok")  # 重复添加
    assert not service.add("sh000300", include_funds=False).get("ok")
    assert service.add("600519").get("ok")


# ---------- 中证、国证官网来源的指数 ----------

CSINDEX_PAYLOAD = {"code": "200", "msg": "Success", "data": [
    {"tradeDate": "20260930", "indexCode": "932365", "indexNameCn": "中证现金流", "open": 4595.25, "high": 4653.08,
     "low": 4594.27, "close": 4644.13, "changePct": 0.97, "tradingVol": 1596354709.0, "tradingValue": 190.07},
    {"tradeDate": "20260929", "indexCode": "932365", "indexNameCn": "中证现金流", "open": 4590.0, "high": 4610.0,
     "low": 4580.0, "close": 4599.63, "changePct": -0.12, "tradingVol": 1.2e9, "tradingValue": 150.0},
]}
CNINDEX_PAYLOAD = {"code": 200, "data": {"indexName": "自由现金流", "data": [
    ["2026-09-30", 5014.3957, 5027.5969, 4969.0434, 4968.4146, 5014.3957, 41.5916, "0.84%", 274.49, 2631.0, None],
]}}


def test_new_indexes_and_sources():
    from src.services.fund_registry import INDEX_CODES, INDEXES

    assert len(INDEXES) == 35
    assert INDEX_CODES["sh932365"]["source"] == "csindex" and "932365.csi" in INDEX_CODES["sh932365"]["aliases"]
    assert INDEX_CODES["sz980092"]["source"] == "cnindex" and INDEX_CODES["sz399324"]["source"] == "tencent"
    assert len({i["code"] for i in INDEXES}) == len(INDEXES)


@pytest.mark.parametrize("text", ["中证现金流", "中证全指自由现金流", "sh932365", "932365.CSI"])
def test_resolve_csi_index(db_path, text):
    from src.services.fund_registry import resolve_fund

    found = resolve_fund(text, db_path)
    assert found and found["kind"] == "index" and found["code"] == "sh932365"


def test_parse_official_index_sites():
    from src.collectors import fund_data

    bars = fund_data.parse_csindex("sh932365", CSINDEX_PAYLOAD, 5)
    assert [b["trade_date"] for b in bars] == ["2026-09-29", "2026-09-30"]              # 按日期升序
    last = bars[-1]
    assert (last["open"], last["close"], last["change_pct"], last["name"]) == (4595.25, 4644.13, 0.97, "中证现金流")
    assert last["amount"] == pytest.approx(190.07e8)
    cni = fund_data.parse_cnindex("sz980092", CNINDEX_PAYLOAD, 5)[0]
    assert (cni["open"], cni["high"], cni["low"], cni["close"]) == (4969.0434, 5027.5969, 4968.4146, 5014.3957)
    assert cni["change_pct"] == 0.84 and cni["amount"] == pytest.approx(274.49e8) and cni["volume"] == 0
    with pytest.raises(ValueError):
        fund_data.parse_csindex("sh932365", {"data": []}, 5)


def test_fetch_routes_by_index_source(monkeypatch):
    from src.collectors import fund_data

    urls = []

    def fake_get(url, params=None, **k):
        urls.append(url)
        if "csindex" in url:
            assert params["indexCode"] == "932365"
            return _Resp(CSINDEX_PAYLOAD)
        if "cnindex" in url:
            assert params["indexCode"] == "980092"
            return _Resp(CNINDEX_PAYLOAD)
        return _Resp(_kline_payload("sz399324", _bars(3)))

    monkeypatch.setattr(httpx, "get", fake_get)
    assert fund_data.fetch_fund_daily("sh932365", 10)[-1]["close"] == 4644.13
    assert fund_data.fetch_fund_daily("sz980092", 10)[-1]["close"] == 5014.3957
    fund_data.fetch_fund_daily("sz399324", 3)
    assert [u.split("/")[2] for u in urls] == ["www.csindex.com.cn", "hq.cnindex.com.cn", "web.ifzq.gtimg.cn"]
