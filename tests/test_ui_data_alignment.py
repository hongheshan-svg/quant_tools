"""工作台与行情源对齐回归：供应商用替身，所有测试离线。"""

from datetime import datetime, timedelta
from types import SimpleNamespace
import json

import pandas as pd
import pytest

from src.collectors import daily_history, extra_sources, paid_market, source_chain as sc
from src.collectors.stock_data import StockDataCollector
from src.services.data_source_settings import validate_settings, probe_daily_source
from tests.test_api import env  # noqa: F401


@pytest.fixture(autouse=True)
def fresh_sources():
    sc._breakers.clear()
    sc._last_good.clear()
    sc.source_health.reset()
    yield
    sc._breakers.clear()
    sc._last_good.clear()
    sc.source_health.reset()


def frame():
    return pd.DataFrame({"date": ["2026-09-25"], "open": [10.0], "close": [11.0], "high": [12.0], "low": [9.0],
                         "volume": [10000.0], "amount": [110000.0]})


def test_daily_invalid_price_falls_back_and_records_every_source(monkeypatch):
    bad = frame()
    bad["high"] = float("inf")
    monkeypatch.setitem(daily_history.DAILY_SOURCES, "tencent", ("tx", lambda *a: bad))
    monkeypatch.setitem(daily_history.DAILY_SOURCES, "sina", ("daily", lambda *a: frame()))
    marker, data = daily_history.fetch_daily_df_with_fallback("600519", "2026-09-01", "2026-09-30", ["tencent", "sina"])
    assert marker == "daily" and len(data) == 1
    records = {r["source"]: r for r in sc.source_health.snapshot()}
    assert records["tx"]["status"] == "failing" and "价格无效" in records["tx"]["last_error"]
    assert records["daily"]["status"] == "ok"


@pytest.mark.parametrize("invalid_date", ["2026-99-99", "2026-09-2x"])
def test_daily_future_and_malformed_dates_do_not_enter_history(monkeypatch, invalid_date):
    bad = frame()
    bad["date"] = invalid_date
    monkeypatch.setitem(daily_history.DAILY_SOURCES, "tencent", ("tx", lambda *a: bad))
    with pytest.raises(RuntimeError, match="日期或价格无效"):
        daily_history.fetch_daily_df_with_fallback("600519", "2026-09-01", "2026-09-30", ["tencent"])


def test_duplicate_bar_with_invalid_latest_price_cannot_pass_by_valid_date(monkeypatch):
    bad = pd.concat([frame(), frame()], ignore_index=True)
    bad.loc[1, "open"] = 0
    monkeypatch.setitem(daily_history.DAILY_SOURCES, "tencent", ("tx", lambda *a: bad))
    with pytest.raises(RuntimeError, match="日期或价格无效"):
        daily_history.fetch_daily_df_with_fallback("600519", "2026-09-01", "2026-09-30", ["tencent"])


def test_unconfigured_paid_source_is_skipped_without_failure(monkeypatch):
    monkeypatch.setattr("src.config_loader.load_config", lambda: {})
    monkeypatch.setitem(daily_history.DAILY_SOURCES, "tickflow", ("tickflow", lambda *a: pytest.fail("未配置源不应调用")))
    monkeypatch.setitem(daily_history.DAILY_SOURCES, "sina", ("daily", lambda *a: frame()))
    assert daily_history.fetch_daily_df_with_fallback("600519", "2026-09-01", "2026-09-30", ["tickflow", "sina"])[0] == "daily"
    assert len(sc.source_health.snapshot()) == 1


def test_stale_cache_is_scoped_to_symbol_and_has_age_limit():
    sc.fetch_with_fallback("test", [("a", lambda: [1])], cache_key="600519")
    assert not sc.fetch_with_fallback("test", [("a", lambda: [])], cache_key="000001", allow_stale=True).ok
    key = ("test", "600519")
    old = sc._last_good[key]
    sc._last_good[key] = (*old[:2], datetime.now() - timedelta(hours=1))
    assert not sc.fetch_with_fallback("test", [("a", lambda: [])], cache_key="600519", allow_stale=True, max_stale_seconds=60).ok
    assert sc.fetch_with_fallback("test", [("a", lambda: [])], cache_key="600519", allow_stale=True).stale


def test_source_recovery_clears_public_error():
    sc.source_health.record("test", "a", False, "api_key=private-token")
    assert "private-token" not in sc.source_health.snapshot()[0]["last_error"]
    sc.source_health.record("test", "a", True)
    assert sc.source_health.snapshot()[0]["last_error"] == ""


@pytest.mark.parametrize("name,expected", [("600519.SH", "600519"), ("920001.BJ", "920001"), ("sh000001", ""), ("600519.SZ", "")])
def test_provider_symbol_aliases_preserve_stock_identity(name, expected):
    assert StockDataCollector._extract_bare_equity_code(name) == expected


@pytest.mark.parametrize("code,expected", [("920001", "bj920001"), ("920001.BJ", "bj920001"), ("600519.SH", "sh600519"), ("900901", "sh900901")])
def test_tencent_routes_new_beijing_codes_before_shanghai_nine_prefix(code, expected):
    assert StockDataCollector._to_tencent_code(code) == expected


def test_code_list_fallback_keeps_beijing_new_prefix(env, monkeypatch):  # noqa: F811
    import src.collectors.stock_data as stock_data
    _, _, config = env
    monkeypatch.setattr(stock_data.ak, "stock_info_a_code_name", lambda: pd.DataFrame())
    data = {"result": {"data": [{"SECURITY_CODE": "920001", "TRADE_MARKET_CODE": "999"}]}}
    monkeypatch.setattr(stock_data, "get_em_client", lambda: SimpleNamespace(request_json=lambda *a, **kw: data))
    monkeypatch.setattr(stock_data, "MIN_EM_CODE_COUNT", 1)
    collector = StockDataCollector(config)
    try:
        assert collector._get_all_stock_codes() == ["bj920001"]
    finally:
        collector.close()


def test_snapshot_coverage_and_quote_dates_trigger_fallback(monkeypatch):
    config = {"data_sources": {"realtime": ["tencent", "sina"], "minimum_realtime_rows": 2}}
    collector = StockDataCollector(config)
    bad = pd.DataFrame({"代码": ["600519", "000001", "garbage"], "最新价": [11, 12, 13], "行情日期": ["2026-09-24"] * 3})
    good = pd.DataFrame({"代码": ["600519", "000001"], "最新价": [11, 12]})
    monkeypatch.setattr(collector, "_fetch_tencent_quotes", lambda: bad)
    monkeypatch.setattr("src.collectors.stock_data.ak.stock_zh_a_spot", lambda: good)
    monkeypatch.setattr(sc.time, "sleep", lambda _: None)
    saved = []
    monkeypatch.setattr(collector, "_save_quotes", lambda df, *a: saved.append(df))
    collector._collect_realtime_quotes("2026-09-25", "unused")
    assert len(saved) == 1 and saved[0].attrs["source"] == "新浪(AKShare)"
    assert any("仅 0 只" in r["last_error"] for r in sc.source_health.snapshot())


def test_tushare_gateway_keeps_history_units_and_request_timeout(monkeypatch):
    payload = {"code": 0, "data": {"fields": ["trade_date", "open", "high", "low", "close", "vol", "amount"],
                                      "items": [["20260925", 10, 12, 9, 11, 100, 1100]]}}
    calls = []
    monkeypatch.setattr(paid_market.requests, "post", lambda url, **kw: calls.append((url, kw)) or SimpleNamespace(raise_for_status=lambda: None, json=lambda: payload))
    config = {"tushare_token": "example-token", "tushare_http_url": "https://example.invalid/tushare", "request_timeout_seconds": 7}
    pro = paid_market.TushareHttpClient(config)
    data = extra_sources.fetch_daily_tushare("600519", "2026-09-01", "2026-09-30", pro)
    assert data.iloc[0]["volume"] == 10000 and data.iloc[0]["amount"] == 1100000
    assert calls[0][0] == config["tushare_http_url"] and calls[0][1]["timeout"] == 7
    assert calls[0][1]["json"]["params"]["ts_code"] == "600519.SH"


def test_tushare_realtime_uses_shares_and_yuan_without_scaling(monkeypatch):
    data = pd.DataFrame({"ts_code": ["600519.SH"], "close": [11], "pre_close": [10], "vol": [10000], "amount": [110000], "trade_time": ["2026-09-25 10:30:00"]})
    monkeypatch.setattr(paid_market.TushareHttpClient, "query", lambda *a, **kw: data)
    got = paid_market.fetch_spot_tushare({"tushare_token": "dummy"})
    assert got.iloc[0]["成交量"] == 10000 and got.iloc[0]["成交额"] == 110000
    assert got.iloc[0]["涨跌幅"] == pytest.approx(10) and got.iloc[0]["行情日期"] == "2026-09-25"


def test_tushare_server_echo_does_not_leak_token(monkeypatch):
    monkeypatch.setattr(paid_market.requests, "post", lambda *a, **kw: SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"code": 1, "msg": "invalid abc123private"}))
    with pytest.raises(RuntimeError) as caught:
        paid_market.TushareHttpClient({"tushare_token": "abc123private"}).query("daily")
    assert "abc123private" not in str(caught.value)


def test_tickflow_daily_converts_timezone_lots_and_closes_client(monkeypatch):
    source = frame().rename(columns={"date": "timestamp"})
    source["timestamp"] = int(pd.Timestamp("2026-09-25 00:30", tz="Asia/Shanghai").timestamp() * 1000)
    calls, closed = [], []
    client = SimpleNamespace(klines=SimpleNamespace(get=lambda *a, **kw: calls.append((a, kw)) or source), close=lambda: closed.append(1))
    monkeypatch.setattr(paid_market, "_tickflow_client", lambda cfg: client)
    monkeypatch.setattr(paid_market, "data_source_config", lambda: {"tickflow_kline_adjust": "forward"})
    data = paid_market.fetch_daily_tickflow("600519", "2026-09-01", "2026-09-30")
    assert data.iloc[0]["date"] == "2026-09-25" and data.iloc[0]["volume"] == 1000000
    assert calls[0][0] == ("600519.SH",) and calls[0][1]["adjust"] == "forward" and closed == [1]
    assert data.attrs["price_adjustment"] == "forward"


def test_tickflow_spot_normalizes_provider_units_date_and_identity(monkeypatch):
    closed = []
    quote = {"symbol": "920001.BJ", "last_price": 11, "prev_close": 10, "volume": 100,
             "amount": 110000, "timestamp": int(pd.Timestamp("2026-09-25 00:30", tz="Asia/Shanghai").timestamp() * 1000),
             "ext": {"change_pct": 0.1, "turnover_rate": 0.02}}
    client = SimpleNamespace(quotes=SimpleNamespace(get=lambda **kw: [quote]), close=lambda: closed.append(1))
    monkeypatch.setattr(paid_market, "_tickflow_client", lambda cfg: client)
    got = paid_market.fetch_spot_tickflow({})
    assert got.iloc[0]["代码"] == "920001.BJ" and got.iloc[0]["成交量"] == 10000
    assert got.iloc[0]["涨跌幅"] == 10 and got.iloc[0]["换手率"] == 2
    assert got.iloc[0]["行情日期"] == "2026-09-25" and closed == [1]


def test_tickflow_failed_request_closes_client_and_redacts_key(monkeypatch):
    closed = []
    def fail(**kw):
        raise RuntimeError("request rejected secret-tickflow")
    client = SimpleNamespace(quotes=SimpleNamespace(get=fail), close=lambda: closed.append(1))
    monkeypatch.setattr(paid_market, "_tickflow_client", lambda cfg: client)
    with pytest.raises(RuntimeError) as caught:
        paid_market.fetch_spot_tickflow({"tickflow_api_key": "secret-tickflow"})
    assert "secret-tickflow" not in str(caught.value) and closed == [1]


def test_quarterly_first_source_exception_tries_next_and_keeps_provenance(env, monkeypatch):  # noqa: F811
    import akshare as ak
    from src.collectors.quarterly_fundamentals import QuarterlyFundamentals
    _, _, config = env
    def fail(**kw):
        raise RuntimeError("offline fixture")
    monkeypatch.setattr(ak, "stock_financial_abstract", fail)
    monkeypatch.setattr(ak, "stock_financial_analysis_indicator", lambda **kw: pd.DataFrame([{"日期": "2025-12-31", "净资产收益率(%)": 15}]))
    monkeypatch.setattr(ak, "stock_history_dividend_detail", lambda **kw: pd.DataFrame())
    result = QuarterlyFundamentals(config).get("600519")
    assert result["reports"][0]["roe"] == 15 and result["provider"] == "indicator"
    assert {r["source"] for r in sc.source_health.snapshot() if r["dataset"] == "季度基本面"} == {"sina", "indicator"}


@pytest.mark.parametrize("patch", [{"realtime": ["x"]}, {"daily_history": []}, {"realtime": ["tencent", "tencent"]},
                                  {"tushare_http_url": "https://user:pass@example.invalid"}, {"tushare_http_url": "https://example.invalid/?token=secret"},
                                  {"request_timeout_seconds": True}, {"minimum_realtime_rows": -1}, {"tickflow_kline_adjust": "unknown"}])
def test_source_settings_reject_invalid_values(patch):
    with pytest.raises(ValueError):
        validate_settings(patch)


def test_settings_api_masks_preserves_and_clears_credentials(env, monkeypatch):  # noqa: F811
    from src import settings_store, config_loader
    client, app, config = env
    config["data_sources"] = {"tushare_token": "secret-tushare", "tickflow_api_key": "secret-tickflow"}
    monkeypatch.setattr(config_loader, "reload_config", lambda: {**config, "data_sources": settings_store.read_settings()["data_sources"]})
    body = client.get("/api/v1/settings/data-sources").json()
    assert "secret-" not in json.dumps(body) and body["data_sources"]["tickflow_api_key"] == "******"
    body["data_sources"]["realtime"] = ["tickflow", "tencent"]
    response = client.put("/api/v1/settings/data-sources", json={"data_sources": body["data_sources"]})
    assert response.status_code == 200 and "secret-" not in response.text
    assert app.state.pipeline.collector.config["data_sources"]["realtime"] == ["tickflow", "tencent"]
    stored = settings_store.read_settings()["data_sources"]
    assert stored["tickflow_api_key"] == "secret-tickflow" and stored["tushare_token"] == "secret-tushare"
    response = client.put("/api/v1/settings/data-sources", json={"data_sources": {"tickflow_api_key": ""}})
    assert response.status_code == 200 and settings_store.read_settings()["data_sources"]["tickflow_api_key"] == ""
    assert "secret-tushare" not in settings_store.export_settings()


def test_settings_api_rejects_bad_gateway_and_unknown_key(env):  # noqa: F811
    client, _, _ = env
    for data in ({"tushare_http_url": "file:///etc/passwd"}, {"unknown": "x"}, {"realtime": []}):
        assert client.put("/api/v1/settings/data-sources", json={"data_sources": data}).status_code == 400


def test_settings_api_normalizes_legacy_comma_source_order(env):  # noqa: F811
    client, _, config = env
    config["data_sources"] = {"realtime": "tencent,sina,tencent"}
    assert client.get("/api/v1/settings/data-sources").json()["data_sources"]["realtime"] == ["tencent", "sina"]


def test_workspace_uses_exact_report_identity_and_local_provenance(env):  # noqa: F811
    from src.database.db import get_db_session
    from src.database.models import StockDiagnosis, Watchlist, StockDaily
    client, _, config = env
    now = datetime.now()
    with get_db_session(config["database"]["sqlite_path"]) as session:
        session.add(Watchlist(code="600519", name="贵州茅台"))
        session.query(StockDaily).filter_by(code="600519").one().source = "腾讯财经(HTTP)"
        session.add(StockDiagnosis(code="600519", created_at=now, score=60, result_json=json.dumps({"code": "600519", "action_label": "观望", "created_at": now.strftime("%Y-%m-%d %H:%M")})))
    payload = client.get("/api/v1/watchlist/workspace").json()
    assert payload["analyzed_today"] == 1 and payload["watchlist"][0]["quote_source"] == "腾讯财经(HTTP)"
    assert payload["watchlist"][0]["diagnosis"]["diagnosis_id"] == payload["recent_reports"][0]["id"]
    assert client.get("/api/v1/stocks/600519/daily").json()[-1]["source"] == "腾讯财经(HTTP)"


def test_new_research_quote_keeps_provider_metadata():
    from src.services.research_artifact import build_context_pack
    pack = build_context_pack({"code": "600519", "quote": {"close": 11, "trade_date": "2026-09-25", "source": "TickFlow", "price_adjustment": "none"}})
    assert pack["blocks"]["quote"]["source"] == "TickFlow"
    assert pack["blocks"]["quote"]["items"]["price_adjustment"]["value"] == "none"


def test_source_probe_does_not_write_quotes(env, monkeypatch):  # noqa: F811
    client, _, config = env
    from src.database.db import get_db_session
    from src.database.models import StockDaily
    sample = frame()
    sample["date"] = (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d")
    monkeypatch.setitem(daily_history.DAILY_SOURCES, "tencent", ("tx", lambda *a: sample))
    with get_db_session(config["database"]["sqlite_path"]) as session:
        before = session.query(StockDaily).count()
    result = probe_daily_source("tencent", "600519")
    assert result["bars"] == 1 and result["ok"]
    with get_db_session(config["database"]["sqlite_path"]) as session:
        assert session.query(StockDaily).count() == before
    assert client.post("/api/v1/system/sources/probe", json={"source": "tickflow", "code": "600519"}).status_code == 400
    assert client.post("/api/v1/system/sources/probe", json={"source": "tencent", "code": "sh000001"}).status_code == 400
    from tests.test_api import _wait
    response = client.post("/api/v1/system/sources/probe", json={"source": "tencent", "code": "600519.SH"})
    assert response.status_code == 200
    assert _wait(client, response.json())["result"]["bars"] == 1
    with get_db_session(config["database"]["sqlite_path"]) as session:
        assert session.query(StockDaily).count() == before
