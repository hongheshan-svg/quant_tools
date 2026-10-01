from __future__ import annotations

from datetime import datetime

import httpx
import pandas as pd
import pytest

from src.collectors import daily_history as daily_history_mod
from src.collectors import fundamentals as fundamentals_mod
from src.collectors import shareholders as sh
from src.collectors import stock_news as stock_news_mod
from src.collectors.source_chain import source_health
from src.database import db as db_module
from src.database.db import get_db_session, init_db
from src.database.models import FinanceNews, StockDaily, StockInfo
from src.services.chat_tools import ChatTools
from src.services.stock_diagnosis import StockDiagnosisService, render_markdown
from src.services.stock_search import StockSearch

PAYLOAD = {
    "gdrs": [
        {"END_DATE": "2026-07-31 00:00:00", "HOLDER_TOTAL_NUM": 458000, "TOTAL_NUM_RATIO": -1.79, "AVG_FREE_SHARES": 21000.5,
         "HOLD_FOCUS": "非常分散", "PRICE": 6.5},
        {"END_DATE": "2026-06-30 00:00:00", "HOLDER_TOTAL_NUM": 466000, "TOTAL_NUM_RATIO": 2.0, "HOLD_FOCUS": "非常分散"},
        {"END_DATE": "2026-03-31 00:00:00", "HOLDER_TOTAL_NUM": 450000, "TOTAL_NUM_RATIO": 1.0, "HOLD_FOCUS": "非常分散"},
        {"END_DATE": "2025-12-31 00:00:00", "HOLDER_TOTAL_NUM": 445000, "TOTAL_NUM_RATIO": 3.0, "HOLD_FOCUS": "非常分散"},
        {"END_DATE": "2025-09-30 00:00:00", "HOLDER_TOTAL_NUM": 430000, "TOTAL_NUM_RATIO": 5.0, "HOLD_FOCUS": "非常分散"},
    ],
    "sdgd": [
        {"END_DATE": "2026-06-30 00:00:00", "HOLDER_RANK": 1, "HOLDER_NAME": "深圳市地铁集团有限公司", "HOLD_NUM": 3.2e9,
         "HOLD_NUM_RATIO": 27.2034, "HOLD_NUM_CHANGE": "不变"},
        {"END_DATE": "2026-06-30 00:00:00", "HOLDER_RANK": 2, "HOLDER_NAME": "华泰证券股份有限公司", "HOLD_NUM": 1.0e8,
         "HOLD_NUM_RATIO": 1.0, "HOLD_NUM_CHANGE": "新进"},
    ],
    "sdltgd": [
        {"END_DATE": "2026-06-30 00:00:00", "HOLDER_NAME": "深圳市地铁集团有限公司", "HOLDER_TYPE": "其他", "HOLD_NUM": 3.2e9,
         "FREE_HOLDNUM_RATIO": 27.2, "HOLD_NUM_CHANGE": "不变"},
        {"END_DATE": "2026-06-30 00:00:00", "HOLDER_NAME": "易方达沪深300交易型开放式指数基金", "HOLDER_TYPE": "基金", "HOLD_NUM": 2.0e8,
         "FREE_HOLDNUM_RATIO": 1.7, "HOLD_NUM_CHANGE": "新进"},
        {"END_DATE": "2026-06-30 00:00:00", "HOLDER_NAME": "香港中央结算有限公司", "HOLDER_TYPE": "其他", "HOLD_NUM": 5.0e8,
         "FREE_HOLDNUM_RATIO": 4.5, "HOLD_NUM_CHANGE": "1000000"},
    ],
    "jgcc": [
        {"REPORT_DATE": "2026-06-30 00:00:00", "ORG_TYPE": "01", "TOTAL_ORG_NUM": 10, "TOTAL_SHARES_RATIO": 5.0},
        {"REPORT_DATE": "2026-06-30 00:00:00", "ORG_TYPE": "00", "TOTAL_ORG_NUM": 277, "TOTAL_SHARES_RATIO": 39.83},
        {"REPORT_DATE": "2026-06-30 00:00:00", "ORG_TYPE": "02", "TOTAL_ORG_NUM": 3, "TOTAL_SHARES_RATIO": 1.0},
    ],
}


class _Resp:
    def __init__(self, payload=None, status=200, text=None):
        self._payload, self.status_code = payload, status
        self.text = text if text is not None else ""

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("err", request=httpx.Request("GET", "http://x"), response=httpx.Response(self.status_code))


class _Net:
    """同时替换 httpx.get 和 httpx.Client，记录请求，兼容两种实现写法。"""

    def __init__(self, monkeypatch, handler):
        self.calls: list[tuple[str, dict]] = []
        self.handler = handler
        net = self

        def _get(url, *a, **kw):
            net.calls.append((url, kw))
            return net.handler(url, kw)

        class _Client:
            def __init__(self, *a, **kw):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def get(self, url, *a, **kw):
                return _get(url, *a, **kw)

            def close(self):
                pass

        monkeypatch.setattr(httpx, "get", _get)
        monkeypatch.setattr(httpx, "Client", _Client)


@pytest.fixture(autouse=True)
def _fresh():
    sh.reset_cache()
    source_health.reset()
    StockSearch.reset()
    yield
    sh.reset_cache()
    source_health.reset()
    StockSearch.reset()


def _net(monkeypatch, payload=PAYLOAD, status=200):
    return _Net(monkeypatch, lambda url, kw: _Resp(payload, status))


def _health():
    return [r for r in source_health.snapshot() if r["dataset"] == "股东数据"]


# ---- 解析 ----

def test_fetch_parses_all_fields(monkeypatch):
    net = _net(monkeypatch)
    data = sh.fetch_shareholders("000002")
    assert net.calls[0][1]["params"]["code"] == "SZ000002"
    assert data["report_date"] == "2026-06-30"
    assert data["holder_count"] == 458000 and data["holder_count_date"] == "2026-07-31"
    assert data["holder_count_change_pct"] == pytest.approx(-1.79)
    assert data["holder_trend"] == [{"date": "2025-12-31", "count": 445000}, {"date": "2026-03-31", "count": 450000},
                                    {"date": "2026-06-30", "count": 466000}, {"date": "2026-07-31", "count": 458000}]
    assert data["concentration"] == "非常分散"
    assert data["top10"][0] == {"name": "深圳市地铁集团有限公司", "shares": 3.2e9, "ratio": 27.2, "change": "不变"}
    assert data["top10"][1]["change"] == "新进"
    assert [h["name"] for h in data["top10_float"]][:2] == ["深圳市地铁集团有限公司", "易方达沪深300交易型开放式指数基金"]
    assert data["top10_float"][1]["type"] == "基金" and data["top10_float"][1]["ratio"] == pytest.approx(1.7)
    assert data["top10_float_ratio"] == pytest.approx(33.4)
    assert data["institution"] == {"count": 277, "ratio": 39.83, "date": "2026-06-30"}  # 取 ORG_TYPE=00 合计行
    assert data["source"] == "东方财富"
    rec = _health()[0]
    assert rec["source"] == "东方财富" and rec["status"] == "ok"


def test_exchange_prefix(monkeypatch):
    net = _net(monkeypatch)
    sh.fetch_shareholders("600519")
    sh.fetch_shareholders("sz002594")
    codes = [c[1]["params"]["code"] for c in net.calls]
    assert codes == ["SH600519", "SZ002594"]


def test_fewer_than_four_periods(monkeypatch):
    _net(monkeypatch, {**PAYLOAD, "gdrs": PAYLOAD["gdrs"][:2]})
    data = sh.fetch_shareholders("000002")
    assert [p["date"] for p in data["holder_trend"]] == ["2026-06-30", "2026-07-31"]


def test_missing_keys_tolerated(monkeypatch):
    _net(monkeypatch, {"sdltgd": PAYLOAD["sdltgd"]})
    data = sh.fetch_shareholders("000002")
    assert data["holder_count"] is None and data["holder_trend"] == [] and data["top10"] == []
    assert data["institution"]["count"] is None and data["top10_float"]
    assert data["report_date"] == "2026-06-30"


def test_report_date_falls_back_to_top10(monkeypatch):
    _net(monkeypatch, {"sdgd": PAYLOAD["sdgd"], "sdltgd": [], "jgcc": None})
    data = sh.fetch_shareholders("000002")
    assert data["report_date"] == "2026-06-30" and data["top10_float"] == [] and data["top10_float_ratio"] is None


def test_only_holder_count(monkeypatch):
    _net(monkeypatch, {"gdrs": PAYLOAD["gdrs"][:1]})
    data = sh.fetch_shareholders("000002")
    assert data["holder_count"] == 458000


@pytest.mark.parametrize("payload", [{}, {"gdrs": [], "sdgd": [], "sdltgd": [], "jgcc": []}, {"gdrs": None}])
def test_all_empty_returns_none(monkeypatch, payload):
    _net(monkeypatch, payload)
    assert sh.fetch_shareholders("000002") is None
    assert _health() and _health()[0]["status"] != "ok"


def test_http_error_returns_none_and_records_failure(monkeypatch):
    _net(monkeypatch, PAYLOAD, status=500)
    assert sh.fetch_shareholders("000002") is None
    rec = _health()[0]
    assert rec["total_failure"] >= 1 and rec["status"] == "failing"


def test_non_json_returns_none(monkeypatch):
    _Net(monkeypatch, lambda url, kw: _Resp(None, 200, "<html>oops</html>"))
    assert sh.fetch_shareholders("000002") is None
    assert _health()[0]["total_failure"] >= 1


def test_network_exception_not_raised(monkeypatch):
    def boom(url, kw):
        raise httpx.ConnectError("断开")

    _Net(monkeypatch, boom)
    assert sh.fetch_shareholders("000002") is None
    assert _health()[0]["total_failure"] >= 1


def test_non_stock_code_returns_none(monkeypatch):
    _net(monkeypatch)
    for code in ("sh000300", "", "abc"):
        assert sh.fetch_shareholders(code) is None


# ---- 缓存 ----

def test_success_is_cached_and_reset(monkeypatch):
    net = _net(monkeypatch)
    first = sh.fetch_shareholders("000002")
    n = len(net.calls)
    assert sh.fetch_shareholders("000002") == first and len(net.calls) == n
    sh.fetch_shareholders("600519")
    assert len(net.calls) > n  # 不同代码单独缓存
    n = len(net.calls)
    sh.reset_cache()
    sh.fetch_shareholders("000002")
    assert len(net.calls) > n


def test_failure_is_cached(monkeypatch):
    net = _net(monkeypatch, PAYLOAD, status=500)
    assert sh.fetch_shareholders("000002") is None
    n = len(net.calls)
    assert sh.fetch_shareholders("000002") is None and len(net.calls) == n
    sh.reset_cache()
    assert sh.fetch_shareholders("000002") is None and len(net.calls) > n


# ---- 摘要与规则 ----

def _data(**kw):
    base = {"report_date": "2026-06-30", "holder_count": 458000, "holder_count_date": "2026-07-31", "holder_count_change_pct": -1.79,
            "holder_trend": [], "concentration": "非常分散", "top10": [],
            "top10_float": [{"name": "深圳市地铁集团有限公司", "type": "其他", "shares": 1, "ratio": 27.2, "change": "不变"},
                            {"name": "基金甲", "type": "基金", "shares": 1, "ratio": 1.7, "change": "新进"}],
            "top10_float_ratio": 49.5, "institution": {"count": 277, "ratio": 39.83, "date": "2026-06-30"}, "source": "东方财富"}
    base.update(kw)
    return base


def test_describe_full():
    text = sh.describe_shareholders(_data())
    assert "股东户数 45.8 万（2026-07-31，较上期 -1.79%，筹码非常分散）" in text
    assert "十大流通股东合计占 49.5%" in text and "深圳市地铁集团有限公司 27.20%（不变）" in text
    assert "机构 277 家，占流通股 39.83%（2026-06-30）" in text


def test_describe_small_count_and_positive_change():
    text = sh.describe_shareholders(_data(holder_count=8500, holder_count_change_pct=3.5))
    assert "8500" in text and "万" not in text.split("；")[0]
    assert "+3.50%" in text or "3.50%" in text


def test_describe_only_top10_no_holder_count():
    text = sh.describe_shareholders(_data(holder_count=None, holder_count_date=None, holder_count_change_pct=None,
                                          concentration="", institution={"count": None, "ratio": None, "date": None}))
    assert "股东户数" not in text and "十大流通股东" in text and "机构" not in text


def test_describe_empty():
    assert sh.describe_shareholders(None) == "" and sh.describe_shareholders({}) == ""


@pytest.mark.parametrize("pct, expect", [(-10.0, "下降"), (-9.9, None), (10.0, "上升"), (9.9, None), (-25.5, "下降"), (0.0, None)])
def test_holder_signals_thresholds(pct, expect):
    signals = sh.holder_signals(_data(holder_count_change_pct=pct, top10_float=[]))
    if expect is None:
        assert signals == []
    else:
        assert len(signals) == 1 and f"股东户数环比{expect}" in signals[0]
        assert ("筹码趋于集中" if expect == "下降" else "筹码趋于分散") in signals[0]


def test_holder_signals_new_institutions_max_three():
    names = ["A基金", "B证券", "C保险", "D社保基金", "E个人", "F资管"]
    flt = [{"name": n, "type": "", "shares": 1, "ratio": 1.0, "change": "新进"} for n in names]
    flt.append({"name": "G基金", "type": "", "shares": 1, "ratio": 1.0, "change": "不变"})
    signals = sh.holder_signals(_data(holder_count_change_pct=None, top10_float=flt))
    new = [s for s in signals if s.startswith("新进")]
    assert len(new) == 1 or len(new) == 3  # 一条合并或最多 3 条
    joined = "".join(new)
    assert "E个人" not in joined and "G基金" not in joined
    assert sum(joined.count(n) for n in ("A基金", "B证券", "C保险", "D社保基金", "F资管")) <= 3


def test_holder_signals_empty():
    assert sh.holder_signals(None) == [] and sh.holder_signals({}) == []


# ---- 诊断集成 ----

EARNINGS = {"type": "业绩预告", "period": "20260930", "change_type": "预增", "summary": "预计增长50%", "change_pct": 50.0,
            "notice_date": "2026-09-20"}
DAYS = [d.strftime("%Y-%m-%d") for d in pd.bdate_range(end="2026-09-25", periods=25)]
GOOD_REPLY = {
    "score": 82, "action": "买入", "confidence": "高", "one_sentence": "x", "position_advice": {},
    "battle_plan": {"buy_price": 23.8, "stop_loss": 22.5}, "catalysts": ["固态电池量产"], "risks": ["高位分歧"],
    "checklist": [], "analysis": "a",
}


class _FakeLLM:
    def __init__(self, reply):
        self.reply = reply

    def chat_json(self, user_message, system_message="", **kwargs):
        return self.reply


def _reset_db_engine():
    if db_module._engine:
        db_module._engine.dispose()
    db_module._engine = None
    db_module._SessionFactory = None


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    monkeypatch.setattr(fundamentals_mod, "fetch_chip_summary", lambda code, db_path: None)
    monkeypatch.setattr(fundamentals_mod.EarningsCache, "get", classmethod(lambda cls, code: dict(EARNINGS)))
    monkeypatch.setattr(stock_news_mod, "get_stock_news", lambda code, refresh=False, now=None: {"news": [], "notices": []})
    monkeypatch.setattr(daily_history_mod, "ensure_daily_history", lambda *a, **k: 0)
    path = str(tmp_path / "holders.db")
    _reset_db_engine()
    init_db(path)
    with get_db_session(path) as session:
        session.add(StockInfo(code="600519", name="贵州茅台", exchange="sh"))
        for i, d in enumerate(DAYS):
            close = 14 + i * 0.4
            session.add(StockDaily(code="sz002594", name="比亚迪", trade_date=d, open=close - 0.2, close=close,
                                   change_pct=2.0, volume=1000 + i, amount=5e9, turnover=3.2, circ_mv=6e11))
            session.add(StockDaily(code="sh600519", name="贵州茅台", trade_date=d, close=1500 + i, change_pct=1.0,
                                   amount=5e9, turnover=0.4, circ_mv=1.9e12))
        session.add(FinanceNews(source="cailianshe", title="无关", collected_at=datetime.now()))
    yield path
    _reset_db_engine()


def _service(db_path, enabled=None, reply=GOOD_REPLY):
    diagnosis = {} if enabled is None else {"shareholders": enabled}
    cfg = {"database": {"sqlite_path": db_path}, "risk": {}, "trading": {}, "diagnosis": diagnosis}
    return StockDiagnosisService(cfg, llm=_FakeLLM(reply))


def test_diagnosis_enabled_adds_context_result_and_signals(db_path, monkeypatch):
    data = _data(holder_count_change_pct=12.0)  # 上升 -> 风险；新进基金 -> 利好
    monkeypatch.setattr(sh, "fetch_shareholders", lambda code: data)
    service = _service(db_path, True)
    context = service.build_context("002594")
    assert "【股东】" in context["text"] and "股东户数 45.8 万" in context["text"]
    assert context["text"].index("【业绩】") < context["text"].index("【股东】")
    assert context["shareholders"] == data and len(context["holder_signals"]) >= 2

    result = service.diagnose("002594", force=True)
    assert "股东户数 45.8 万" in result["shareholders"]
    assert any(r.startswith("股东：") and "上升" in r for r in result["risks"])
    assert any(c.startswith("股东：") and "新进" in c for c in result["catalysts"])
    assert "**股东**" in render_markdown(result)


def test_diagnosis_holder_count_drop_is_catalyst(db_path, monkeypatch):
    monkeypatch.setattr(sh, "fetch_shareholders", lambda code: _data(holder_count_change_pct=-15.0, top10_float=[]))
    result = _service(db_path, True).diagnose("002594", force=True)
    assert any(c.startswith("股东：") and "下降" in c for c in result["catalysts"])
    assert not any(r.startswith("股东：") for r in result["risks"])


def test_diagnosis_enabled_without_data(db_path, monkeypatch):
    monkeypatch.setattr(sh, "fetch_shareholders", lambda code: None)
    service = _service(db_path, True)
    assert "【股东】暂无" in service.build_context("002594")["text"]
    result = service.diagnose("002594", force=True)
    assert result["shareholders"] == ""
    assert not any(x.startswith("股东：") for x in result["risks"] + result["catalysts"])


def test_diagnosis_disabled_by_default_never_fetches(db_path, monkeypatch):
    def boom(code):
        raise AssertionError("配置缺省时不应调用 fetch_shareholders")

    monkeypatch.setattr(sh, "fetch_shareholders", boom)
    for enabled in (None, False):
        service = _service(db_path, enabled)
        assert "【股东】" not in service.build_context("002594")["text"]
        result = service.diagnose("002594", force=True)
        assert result.get("shareholders", "") == ""


def test_diagnosis_fetch_exception_does_not_break(db_path, monkeypatch):
    def boom(code):
        raise RuntimeError("炸了")

    monkeypatch.setattr(sh, "fetch_shareholders", boom)
    result = _service(db_path, True).diagnose("002594", force=True)
    assert result["score"] == 82


def test_data_quality_weights_unchanged():
    from src.services.stock_diagnosis import DATA_QUALITY_WEIGHTS

    assert not any("股东" in k for k in DATA_QUALITY_WEIGHTS)


# ---- 问股工具 ----

def test_chat_tool_shareholders(db_path, monkeypatch):
    monkeypatch.setattr(sh, "fetch_shareholders", lambda code: _data())
    tools = ChatTools({"database": {"sqlite_path": db_path}})
    out = tools.call("shareholders", {"code": "600519"})
    assert "贵州茅台" in out and "股东户数 45.8 万" in out
    assert "深圳市地铁集团有限公司" in out and "基金甲" in out   # 十大流通股东明细


def test_chat_tool_shareholders_no_data_and_unknown(db_path, monkeypatch):
    monkeypatch.setattr(sh, "fetch_shareholders", lambda code: None)
    tools = ChatTools({"database": {"sqlite_path": db_path}})
    assert "暂无" in tools.call("shareholders", {"code": "600519"})
    assert tools.call("shareholders", {}).startswith("工具执行失败")
    from src.services.chat_tools import tools_prompt

    assert "shareholders" in tools_prompt()
