"""额外数据源（baostock、通达信、efinance、Tushare）的解析和兜底顺序（用假 SDK，不联网）"""

import sys
from types import SimpleNamespace

import pandas as pd
import pytest

from src.collectors import daily_history, extra_sources
from src.collectors.daily_history import fetch_daily_df_with_fallback, records_from_daily_df


@pytest.fixture(autouse=True)
def clean_source_health():
    from src.collectors import source_chain
    source_chain._breakers.clear()
    source_chain._last_good.clear()
    source_chain.source_health.reset()
    yield
    source_chain._breakers.clear()
    source_chain._last_good.clear()
    source_chain.source_health.reset()


class FakeResultSet:
    def __init__(self, rows, fields, error_code="0", error_msg=""):
        self.rows, self.fields, self.error_code, self.error_msg = list(rows), fields, error_code, error_msg

    def next(self):
        return bool(self.rows)

    def get_row_data(self):
        return self.rows.pop(0)


@pytest.fixture
def fake_baostock(monkeypatch):
    calls = []
    module = SimpleNamespace(
        login=lambda: print("login success!") or SimpleNamespace(error_code="0", error_msg=""),
        logout=lambda: print("logout success!"),
        query_history_k_data_plus=lambda code, fields, **kw: calls.append((code, kw)) or FakeResultSet(
            [["2026-09-24", "1250.01", "1256.13", "1231.05", "1237.00", "3123935", "3867310920.27", "0.249900"],
             ["2026-09-28", "1236.00", "1244.01", "1228.10", "1243.88", "2821830", "3488720612.97", "0.225700"]],
            fields.split(",")),
    )
    monkeypatch.setitem(sys.modules, "baostock", module)
    return calls


def test_baostock_daily(fake_baostock, capsys):
    df = extra_sources.fetch_daily_baostock("600519", "2026-09-20", "2026-09-29")
    assert fake_baostock == [("sh.600519", {"start_date": "2026-09-20", "end_date": "2026-09-29", "frequency": "d", "adjustflag": "2"})]
    assert list(df.columns) == extra_sources.DAILY_COLUMNS
    assert df.iloc[1].to_dict() == pytest.approx({"date": "2026-09-28", "open": 1236.0, "high": 1244.01, "low": 1228.1, "close": 1243.88,
                                                   "volume": 2821830.0, "amount": 3488720612.97, "turnover": 0.002257})
    assert capsys.readouterr().out == ""  # 登录提示不打印
    with pytest.raises(ValueError, match="北交所"):
        extra_sources.fetch_daily_baostock("920001", "2026-09-20", "2026-09-29")

    records = records_from_daily_df("600519", "贵州茅台", "baostock", df)
    assert records[1]["volume"] == 2821830.0 and records[1]["turnover"] == pytest.approx(0.2257)
    assert records[1]["change_pct"] == pytest.approx((1243.88 / 1237 - 1) * 100)


class FakeTdx:
    connect_ok = {("117.34.114.17", 7709)}
    attempts: list = []

    def connect(self, host, port, time_out=2):
        FakeTdx.attempts.append((host, port))
        return (host, port) in FakeTdx.connect_ok

    def disconnect(self):
        pass

    def get_security_bars(self, category, market, code, start, count):
        assert (category, market, code) == (9, 1, "600519")
        return [{"datetime": "2026-09-24 15:00", "open": 1250.01, "close": 1237.0, "high": 1256.13, "low": 1231.05, "vol": 31239.0, "amount": 3.8673e9},
                {"datetime": "2026-09-28 15:00", "open": 1236.0, "close": 1243.88, "high": 1244.01, "low": 1228.1, "vol": 28218.0, "amount": 3.4887e9}]

    def get_security_quotes(self, pairs):
        return [{"code": code, "price": 10.0 if code != "000002" else 0, "last_close": 8.0, "open": 9.0, "high": 10.5, "low": 8.8,
                 "vol": 1234, "amount": 1.2e6} for _market, code in pairs]


@pytest.fixture
def fake_tdx(monkeypatch):
    import pytdx.hq

    FakeTdx.attempts = []
    FakeTdx.connect_ok = {("117.34.114.17", 7709)}
    monkeypatch.setattr(pytdx.hq, "TdxHq_API", FakeTdx)
    monkeypatch.setattr(extra_sources, "_tdx_good", None)
    monkeypatch.setattr(extra_sources, "data_source_config", lambda: {"pytdx_servers": ["10.0.0.1:7709", "bad"]})
    return FakeTdx


def test_tdx_servers_order(fake_tdx):
    servers = extra_sources.tdx_servers()
    assert servers[0] == ("10.0.0.1", 7709)                     # 配置的优先
    assert servers[1:3] == extra_sources.DEFAULT_TDX_SERVERS[:2]
    assert len(servers) == len(set(servers))


def test_pytdx_daily_and_spot(fake_tdx):
    df = extra_sources.fetch_daily_pytdx("600519", "2026-09-25", "2026-09-29")
    assert df["date"].tolist() == ["2026-09-28"]
    assert df.iloc[0]["volume"] == 2821800.0                    # 手 → 股
    assert fake_tdx.attempts[:2] == [("10.0.0.1", 7709), ("117.34.114.17", 7709)]
    assert extra_sources._tdx_good == ("117.34.114.17", 7709)  # 记住能连上的服务器

    codes = [f"{600000 + i:06d}" for i in range(100)] + ["000002", "920001"]
    spot = extra_sources.fetch_spot_pytdx(codes, {"600000": "浦发银行"})
    assert len(spot) == 100                                     # 停牌（价格 0）和北交所跳过
    first = spot.iloc[0].to_dict()
    assert first["名称"] == "浦发银行" and first["涨跌幅"] == pytest.approx(25.0) and first["成交量"] == 123400


def test_pytdx_all_servers_down(fake_tdx):
    fake_tdx.connect_ok = set()
    with pytest.raises(ConnectionError, match="通达信"):
        extra_sources.fetch_daily_pytdx("600519", "2026-09-25", "2026-09-29")
    assert len(fake_tdx.attempts) == extra_sources.TDX_MAX_TRIES
    with pytest.raises(ValueError, match="北交所"):
        extra_sources.tdx_market("830799")


def test_efinance(monkeypatch):
    hist = pd.DataFrame({"股票名称": ["贵州茅台"], "股票代码": ["600519"], "日期": ["2026-09-28"], "开盘": [1236.0], "收盘": [1243.88],
                         "最高": [1244.01], "最低": [1228.1], "成交量": [28218], "成交额": [3.4887e9], "涨跌幅": [0.56], "换手率": [0.23]})
    spot = pd.DataFrame({"股票代码": ["600519"], "股票名称": ["贵州茅台"], "最新价": [1243.88], "成交量": [28218], "动态市盈率": [20.1]})
    stock = SimpleNamespace(get_quote_history=lambda code, beg, end, klt, fqt: hist.copy(), get_realtime_quotes=lambda: spot.copy())
    monkeypatch.setitem(sys.modules, "efinance", SimpleNamespace(stock=stock))
    df = extra_sources.fetch_daily_efinance("600519", "2026-09-20", "2026-09-29")
    assert df.iloc[0][["volume", "turnover"]].tolist() == pytest.approx([2821800.0, 0.0023])
    live = extra_sources.fetch_spot_efinance()
    assert live.iloc[0][["代码", "名称", "成交量", "市盈率"]].tolist() == ["600519", "贵州茅台", 2821800, 20.1]


def test_tushare(monkeypatch):
    pro = SimpleNamespace(daily=lambda ts_code, start_date, end_date: pd.DataFrame(
        {"ts_code": [ts_code] * 2, "trade_date": ["20260928", "20260924"], "open": [1236.0, 1250.01], "high": [1244.01, 1256.13],
         "low": [1228.1, 1231.05], "close": [1243.88, 1237.0], "vol": [28218.3, 31239.35], "amount": [3488720.6, 3867310.9]}))
    df = extra_sources.fetch_daily_tushare("600519", "2026-09-20", "2026-09-29", pro=pro)
    assert df["date"].tolist() == ["2026-09-24", "2026-09-28"]   # 按日期升序
    assert df.iloc[1][["volume", "amount"]].tolist() == pytest.approx([2821830.0, 3488720600.0])
    assert extra_sources.tushare_code("000001") == "000001.SZ" and extra_sources.tushare_code("920001") == "920001.BJ"
    monkeypatch.setattr(extra_sources, "data_source_config", lambda: {"tushare_token": ""})
    with pytest.raises(RuntimeError, match="tushare_token"):
        extra_sources.fetch_daily_tushare("600519", "2026-09-20", "2026-09-29")


def test_fallback_order_uses_extra_sources(monkeypatch):
    frame = pd.DataFrame({"date": ["2026-09-28"], "open": [1.0], "high": [1.0], "low": [1.0], "close": [1.0],
                          "volume": [100.0], "amount": [100.0], "turnover": [0.0]})
    monkeypatch.setattr(daily_history, "_fetch_tx", lambda *a: (_ for _ in ()).throw(ConnectionError("tx down")))
    monkeypatch.setattr(extra_sources, "fetch_daily_baostock", lambda *a: pd.DataFrame())
    monkeypatch.setattr(extra_sources, "fetch_daily_pytdx", lambda *a: frame)
    monkeypatch.setitem(daily_history.DAILY_SOURCES, "tencent", ("tx", daily_history._fetch_tx))
    label, df = fetch_daily_df_with_fallback("600519", "2026-09-20", "2026-09-29", order=["tencent", "baostock", "pytdx"])
    assert label == "pytdx" and len(df) == 1
    with pytest.raises(RuntimeError, match=r"tx: tx down \| baostock: 无数据"):
        fetch_daily_df_with_fallback("600519", "2026-09-20", "2026-09-29", order=["tencent", "baostock"])


def test_daily_source_order_from_config(monkeypatch):
    from src import config_loader

    monkeypatch.setattr(config_loader, "load_config", lambda: {"data_sources": {"daily_history": ["baostock", "nope", "tencent"]}})
    assert daily_history.daily_source_order() == ["baostock", "tencent"]
    monkeypatch.setattr(config_loader, "load_config", lambda: {})
    from src.services.data_source_settings import DAILY_DEFAULT
    assert daily_history.daily_source_order() == DAILY_DEFAULT
