"""自选股：增删与批量导入、决策仪表盘、盘中提醒与问股接入。"""

from __future__ import annotations

from datetime import datetime

import pytest

from src import notifier as notifier_mod
from src import scheduler as scheduler_mod
from src import trading_calendar
from src.database import db as db_module
from src.database.db import get_db_session, init_db
from src.database.models import StockDaily, StockInfo
from src.services.alert_service import AlertService
from src.services.chat_tools import ChatTools
from src.services.stock_search import StockSearch
from src.services.watchlist import WatchlistService, extract_tokens
from src.services.watchlist_report import WatchlistReportService, change_text, reuse_threshold


def _reset_db_engine():
    if db_module._engine:
        db_module._engine.dispose()
    db_module._engine = None
    db_module._SessionFactory = None


STOCKS = {"600519": "贵州茅台", "601919": "中远海控", "000001": "平安银行", "300750": "宁德时代"}


@pytest.fixture
def config(tmp_path):
    path = str(tmp_path / "wl.db")
    _reset_db_engine()
    init_db(path)
    StockSearch.reset()
    with get_db_session(path) as session:
        for code, name in STOCKS.items():
            session.add(StockInfo(code=code, name=name))
            session.add(StockDaily(code=code, name=name, trade_date="2026-09-25", close=10.0, change_pct=1.5))
    yield {"database": {"sqlite_path": path}, "watchlist": {"max_stocks": 3}}
    StockSearch.reset()
    _reset_db_engine()


def test_extract_tokens():
    codes, names = extract_tokens("600519 贵州茅台, sz000001；中远海控\n601919.SH、宁德时代")
    assert codes == ["600519", "000001", "601919"] and names == ["贵州茅台", "中远海控", "宁德时代"]
    # 券商导出的表格：只读代码列，持仓数量里的 6 位数字不会被当成代码
    assert extract_tokens("证券代码,证券名称,持仓数量\n600519,贵州茅台,600000\n,中远海控,100\n") == (["600519"], ["中远海控"])
    assert extract_tokens("代码\t名称\n000001\t平安银行") == (["000001"], [])


def test_add_remove_and_limit(config):
    service = WatchlistService(config)
    assert service.add("gzmt") == {"ok": True, "code": "600519", "name": "贵州茅台"}
    assert service.add("sh600519")["error"] == "贵州茅台(600519) 已在自选股中"
    assert service.add("不存在的股票") == {"ok": False, "error": "找不到股票「不存在的股票」"}
    assert service.add("中远海控")["ok"] and service.add("000001")["ok"]
    assert "最多 3 只" in service.add("宁德时代")["error"]
    assert service.codes() == ["600519", "601919", "000001"] and service.contains("sz000001")
    assert service.remove("601919") and not service.remove("601919")
    assert service.codes() == ["600519", "000001"]


def test_import_text_and_files(config, tmp_path):
    service = WatchlistService(config)
    service.add("600519")
    result = service.import_text("600519 贵州茅台\n601919 中远海控\n不存在 999999 宁德时代 300750")
    assert result == {"added": ["中远海控(601919)", "宁德时代(300750)"], "existing": ["贵州茅台(600519)"],
                      "unknown": ["999999", "不存在"], "over_limit": []}   # 同一行的代码和名称只算一次

    service.remove("601919")
    service.remove("300750")
    csv = tmp_path / "positions.csv"
    csv.write_bytes("证券代码,证券名称,持仓数量\n601919,中远海控,600000\n".encode("gbk"))
    assert service.import_file(str(csv))["added"] == ["中远海控(601919)"]

    pd = pytest.importorskip("pandas")
    pytest.importorskip("openpyxl")
    service.remove("601919")
    xlsx = tmp_path / "list.xlsx"
    pd.DataFrame({"代码": ["000001", "300750"], "名称": ["平安银行", "宁德时代"]}).to_excel(xlsx, index=False)
    assert service.import_file(str(xlsx)) == {"added": ["平安银行(000001)", "宁德时代(300750)"], "existing": [],
                                              "unknown": [], "over_limit": []}


def test_overview_and_integrations(config, monkeypatch):
    service = WatchlistService(config)
    service.add("600519")
    from src.services import stock_diagnosis as diag_mod

    monkeypatch.setattr(diag_mod.StockDiagnosisService, "latest", lambda self, code, max_age_minutes=None: {
        "action": "watch", "action_label": "观望", "score": 55, "created_at": "2026-09-25 16:00", "one_sentence": "等待放量"})
    rows = service.overview()
    assert rows[0]["close"] == 10.0 and rows[0]["change_pct"] == 1.5 and rows[0]["diagnosis"]["action_label"] == "观望"
    assert ChatTools(config).call("watchlist", {}) == "自选股：贵州茅台(600519) 10.0（+1.50%） 诊断：观望 55分（2026-09-25 16:00）"

    # 盘中提醒会关注自选股
    monkeypatch.setattr(AlertService, "_positions", lambda self: [])
    assert AlertService({**config, "alerts": {}}).watchlist() == {"600519": "贵州茅台"}


# ---------- 决策仪表盘 ----------

class FakeDiagnosis:
    def __init__(self):
        self.latest_results = {}
        self.histories = {}
        self.diagnosed = []

    def latest(self, code, max_age_minutes=None):
        return self.latest_results.get(code)

    def diagnose(self, code, force=False):
        self.diagnosed.append((code, force))
        if code == "000001":
            return {"code": code, "error": "AI 未返回有效结果，请检查 AI 设置或稍后重试"}
        return _result(code, "buy", 78, "2026-09-25 16:30", one_sentence="放量突破")

    def history(self, code, limit=5):
        return self.histories.get(code, [])[:limit]


def _result(code, action, score, created_at, **extra):
    labels = {"buy": "买入", "watch": "观望", "sell": "卖出"}
    return {"code": code, "name": STOCKS[code], "action": action, "action_label": labels[action], "score": score,
            "created_at": created_at, "one_sentence": extra.get("one_sentence", ""), "risks": extra.get("risks", []),
            "guardrails": extra.get("guardrails", []), "catalysts": [], "battle_plan": extra.get("battle_plan", {})}


def test_reuse_threshold_and_change_text():
    assert reuse_threshold(datetime(2026, 9, 25, 16, 30)) == "2026-09-25 15:00"
    assert reuse_threshold(datetime(2026, 9, 25, 10, 30)) == "2026-09-25 10:00"
    current = _result("600519", "buy", 78, "2026-09-25 16:30")
    assert change_text(current, None) == "首次诊断"
    assert change_text(current, _result("600519", "watch", 55, "2026-09-24 16:30")) == "较上次（09-24 16:30）：观望→买入，评分 55→78"
    assert change_text(current, _result("600519", "buy", 78, "2026-09-24 16:30")) == "与上次一致"


def test_watchlist_report(config, monkeypatch):
    service = WatchlistService({**config, "watchlist": {"max_stocks": 10}})
    for code in ("600519", "601919", "000001"):
        service.add(code)
    fake = FakeDiagnosis()
    # 茅台：收盘后已诊断过，直接复用；中远海控：上次诊断在盘中，需要重新诊断；平安银行：诊断失败
    fake.latest_results["600519"] = _result("600519", "sell", 35, "2026-09-25 15:40", risks=["跌破20日线"], guardrails=["资金净流出"])
    fake.latest_results["601919"] = _result("601919", "watch", 55, "2026-09-25 11:00")
    fake.histories["601919"] = [_result("601919", "buy", 78, "2026-09-25 16:30"), _result("601919", "watch", 55, "2026-09-25 11:00")]
    pushed, progress = [], []
    monkeypatch.setattr(notifier_mod, "enabled_channels", lambda cfg, kind=None: ["wechat"] if kind == "watchlist" else [])
    monkeypatch.setattr(notifier_mod, "broadcast", lambda cfg, title, content, kind=None: pushed.append((title, content)) or {"wechat": True})

    result = WatchlistReportService({**config, "watchlist": {"workers": 2}}, diagnosis=fake).run(
        progress=lambda d, t: progress.append((d, t)), now=datetime(2026, 9, 25, 16, 30))
    assert sorted(fake.diagnosed) == [("000001", True), ("601919", True)]   # 并发执行，顺序不固定
    assert (result["total"], result["done"], result["pushed"]) == (3, 2, True)
    assert result["counts"] == {"买入/加仓": 1, "持有/观望": 0, "减仓/卖出/回避": 1}
    assert result["failed"] == [{"code": "000001", "name": "平安银行", "error": "AI 未返回有效结果，请检查 AI 设置或稍后重试"}]
    assert sorted(progress) == [(1, 3), (2, 3), (3, 3)]

    md = result["markdown"]
    assert "共分析 3 只 | 🟢买入/加仓 1 🟡持有/观望 0 🔴减仓/卖出/回避 1" in md
    assert md.index("中远海控(601919)**：买入") < md.index("贵州茅台(600519)**：卖出")   # 按买入 → 观望 → 卖出排序
    assert "较上次（09-25 11:00）：观望→买入，评分 55→78" in md
    assert "- 风险：跌破20日线" in md and "- 护栏：资金净流出" in md
    assert "### ⚠️ 未完成\n- 平安银行：AI 未返回有效结果" in md
    assert pushed[0][0] == "自选股决策仪表盘 2026-09-25"

    latest = WatchlistReportService(config).latest()
    assert latest["trade_date"] == "2026-09-25" and len(latest["items"]) == 2 and latest["markdown"] == md
    # 重新生成会覆盖当天的仪表盘
    WatchlistReportService(config, diagnosis=fake).run(push=False, now=datetime(2026, 9, 25, 17, 0))
    from src.database.models import WatchlistReport

    with get_db_session(config["database"]["sqlite_path"]) as session:
        assert session.query(WatchlistReport).count() == 1


def test_empty_watchlist_report(config):
    assert WatchlistReportService(config, diagnosis=FakeDiagnosis()).run()["error"].startswith("自选股为空")


def test_scheduler_watchlist_job(monkeypatch):
    calls = []
    monkeypatch.setattr(trading_calendar, "load", lambda db_path, refresh=True: True)
    monkeypatch.setattr(trading_calendar, "is_trade_day", lambda d=None: True)
    monkeypatch.setattr(WatchlistReportService, "run", lambda self, **k: calls.append(1) or {})
    scheduler_mod._run_watchlist_report({"watchlist": {"daily_report": False}})
    assert calls == []
    scheduler_mod._run_watchlist_report({})
    assert calls == [1]
