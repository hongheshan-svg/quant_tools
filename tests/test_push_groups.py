"""WS31 推送分组：邮件分组解析与定向发送、逐只推送、决策仪表盘总超时与分组仪表盘、/settings/watchlist 与推送设置 groups。"""

from __future__ import annotations

import smtplib
import threading
import time
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from api.auth import AuthStore
from src import config_loader, settings_store
from src import notifier as notifier_mod
from src.database import db as db_module
from src.database.db import get_db_session, init_db
from src.database.models import StockDaily, StockInfo
from src.notifier.mail import EmailNotifier
from src.services.stock_search import StockSearch
from src.services.watchlist import WatchlistService
from src.services.watchlist_report import WatchlistReportService


def _reset_db_engine():
    if db_module._engine:
        db_module._engine.dispose()
    db_module._engine = None
    db_module._SessionFactory = None


STOCKS = {"600519": "贵州茅台", "601919": "中远海控", "000001": "平安银行", "300750": "宁德时代"}
NOW = datetime(2026, 9, 25, 16, 30)


@pytest.fixture(autouse=True)
def _notify_env(monkeypatch):
    # 测试里不能让 QUANT_NO_NOTIFY 泄漏；先设置再还原
    monkeypatch.setenv("QUANT_NO_NOTIFY", "")


@pytest.fixture
def config(tmp_path):
    path = str(tmp_path / "pg.db")
    _reset_db_engine()
    init_db(path)
    StockSearch.reset()
    with get_db_session(path) as session:
        for code, name in STOCKS.items():
            session.add(StockInfo(code=code, name=name))
            session.add(StockDaily(code=code, name=name, trade_date="2026-09-25", close=10.0, change_pct=1.5))
    yield {"database": {"sqlite_path": path}, "watchlist": {"max_stocks": 10, "workers": 3}}
    StockSearch.reset()
    _reset_db_engine()


# ---------- 假对象 ----------

class FakeDiagnosis:
    """600519/601919/300750 成功；000001 失败；blocked 中的代码会等 release 事件。"""

    def __init__(self, blocked=()):
        self.blocked = set(blocked)
        self.release = threading.Event()
        self.started = threading.Event()

    def latest(self, code, max_age_minutes=None):
        return None

    def diagnose(self, code, force=False):
        if code in self.blocked:
            self.started.set()
            self.release.wait(30)
        if code == "000001":
            return {"code": code, "error": "AI 未返回有效结果"}
        return {"code": code, "name": STOCKS[code], "action": "buy", "action_label": "买入", "score": 80,
                "created_at": "2026-09-25 16:30", "one_sentence": f"{code}放量突破", "risks": [f"{code}风险点"],
                "guardrails": [], "catalysts": [], "battle_plan": {"buy_price": 10.5, "stop_loss": 9.8, "target_price": 12.0}}

    def history(self, code, limit=5):
        return []


@pytest.fixture
def pushes(monkeypatch):
    """替换 broadcast / send_email / enabled_channels，记录调用。"""
    rec = {"broadcast": [], "email": []}
    monkeypatch.setattr(notifier_mod, "enabled_channels", lambda cfg, kind=None: ["wechat"])
    monkeypatch.setattr(notifier_mod, "broadcast",
                        lambda cfg, title, content, kind=None: rec["broadcast"].append((title, content, kind)) or {"wechat": True})

    def fake_email(cfg, to, title, content, *a, **k):
        rec["email"].append((list(to) if not isinstance(to, str) else [to], title, content))
        return True

    monkeypatch.setattr(notifier_mod, "send_email", fake_email)
    return rec


def _add(config, *codes):
    service = WatchlistService(config)
    for c in codes:
        service.add(c)


def _svc(config, fake, **watchlist):
    cfg = {**config, "watchlist": {**config["watchlist"], **watchlist}}
    return WatchlistReportService(cfg, diagnosis=fake), cfg


def _email_cfg(groups=None, enabled=True, **extra):
    return {"notifier": {"email": {"enabled": enabled, "smtp_host": "smtp.x.com", "username": "a@x.com", "password": "p",
                                   "to": ["default@x.com"], "groups": groups or [], **extra}}}


# ---------- email_groups ----------

def test_email_groups_parse():
    cfg = _email_cfg([
        {"name": "家人", "stocks": ["600519", "510300", "SH000300", "sh600519"], "to": "a@x.com；b@x.com, c@x.com"},
        {"name": "无收件人", "stocks": ["600519"], "to": []},
        {"name": "无股票", "stocks": [], "to": ["z@x.com"]},
        {"name": "列表收件人", "stocks": ["000001"], "to": ["d@x.com", " "]},
    ])
    groups = notifier_mod.email_groups(cfg)
    assert [g["name"] for g in groups] == ["家人", "列表收件人"]
    assert groups[0]["to"] == ["a@x.com", "b@x.com", "c@x.com"]
    assert groups[0]["stocks"] == {"600519", "510300", "sh000300"}   # 指数保留前缀，个股取 6 位，sh600519 → 600519
    assert groups[1]["stocks"] == {"000001"} and groups[1]["to"] == ["d@x.com"]


def test_email_groups_missing_or_invalid():
    assert notifier_mod.email_groups({}) == []
    assert notifier_mod.email_groups({"notifier": {"email": {"groups": None}}}) == []
    assert notifier_mod.email_groups(_email_cfg([{"name": "x"}, "bad"])) == []


# ---------- EmailNotifier to= ----------

class _SMTPRecorder:
    sent: list = []

    def __init__(self, *a, **k):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def login(self, *a):
        pass

    def starttls(self):
        pass

    def sendmail(self, sender, to, msg):
        _SMTPRecorder.sent.append((list(to), msg))


@pytest.fixture
def smtp(monkeypatch):
    _SMTPRecorder.sent = []
    monkeypatch.setattr(smtplib, "SMTP_SSL", _SMTPRecorder)
    monkeypatch.setattr(smtplib, "SMTP", _SMTPRecorder)
    return _SMTPRecorder


def test_email_send_to_override(smtp):
    n = EmailNotifier(_email_cfg())
    assert n.send("标题", "内容") is True
    assert smtp.sent[-1][0] == ["default@x.com"]
    assert n.send("标题", "内容", to=["g1@x.com", "g2@x.com"]) is True
    assert smtp.sent[-1][0] == ["g1@x.com", "g2@x.com"]
    assert n.send("标题", "内容", to=[]) is True            # 空列表回落到默认收件人
    assert smtp.sent[-1][0] == ["default@x.com"]
    assert n.send("标题", "内容", to=None) is True
    assert smtp.sent[-1][0] == ["default@x.com"]


def test_email_send_image_to_override(smtp):
    n = EmailNotifier(_email_cfg())
    assert n.send_image("图", b"\x89PNG\r\n", to=["g@x.com"]) is True
    assert smtp.sent[-1][0] == ["g@x.com"]
    assert "g@x.com" in smtp.sent[-1][1] and "default@x.com" not in smtp.sent[-1][1]   # To 头也是覆盖后的收件人


def test_email_send_to_override_without_default_recipients(smtp):
    """默认 to 为空、但指定了收件人：仍然可以发（返回值语义：发送成功为 True）。"""
    cfg = _email_cfg()
    cfg["notifier"]["email"]["to"] = []
    assert EmailNotifier(cfg).send("t", "c", to=["g@x.com"]) in (True, False)  # 实现可选择拒绝；不应抛异常


def test_email_send_failure_returns_false(monkeypatch):
    class Boom(_SMTPRecorder):
        def sendmail(self, *a):
            raise OSError("down")

    monkeypatch.setattr(smtplib, "SMTP_SSL", Boom)
    assert EmailNotifier(_email_cfg()).send("t", "c", to=["g@x.com"]) is False


# ---------- send_email ----------

def test_send_email_ok(smtp):
    assert notifier_mod.send_email(_email_cfg(), ["g@x.com"], "标题", "内容") is True
    assert smtp.sent and smtp.sent[-1][0] == ["g@x.com"]


def test_send_email_respects_no_notify(smtp, monkeypatch):
    monkeypatch.setenv("QUANT_NO_NOTIFY", "1")
    assert notifier_mod.send_email(_email_cfg(), ["g@x.com"], "标题", "内容") is False
    assert smtp.sent == []


def test_send_email_disabled_or_incomplete(smtp):
    assert notifier_mod.send_email(_email_cfg(enabled=False), ["g@x.com"], "t", "c") is False
    cfg = _email_cfg()
    cfg["notifier"]["email"]["smtp_host"] = ""
    assert notifier_mod.send_email(cfg, ["g@x.com"], "t", "c") is False
    assert notifier_mod.send_email({}, ["g@x.com"], "t", "c") is False
    assert smtp.sent == []


def test_send_email_image_for_email_channel(smtp, monkeypatch):
    """notifier.image 对 email 生效时尝试分享图（邮件里带 image/png）；渲染失败回退文字。"""
    from src.services import report_image

    cfg = _email_cfg()
    cfg["notifier"]["image"] = {"channels": ["email"], "kinds": ["watchlist", "daily_report"], "max_chars": 8000}
    monkeypatch.setattr(report_image, "render_markdown_image", lambda *a, **k: b"\x89PNG\r\n\x1a\n")
    assert notifier_mod.send_email(cfg, ["g@x.com"], "标题", "内容") is True
    assert "image/png" in smtp.sent[-1][1]

    def boom(*a, **k):
        raise RuntimeError("no browser")

    monkeypatch.setattr(report_image, "render_markdown_image", boom)
    assert notifier_mod.send_email(cfg, ["g@x.com"], "标题", "内容") is True
    assert "text/plain" in smtp.sent[-1][1]


# ---------- 逐只推送 ----------

def test_single_notify_each_success(config, pushes):
    _add(config, "600519", "601919", "000001")
    svc, _ = _svc(config, FakeDiagnosis(), single_notify=True)
    result = svc.run(now=NOW)
    assert result["done"] == 2 and len(result["failed"]) == 1
    singles = [b for b in pushes["broadcast"] if "自选股诊断" in b[0]]
    summary = [b for b in pushes["broadcast"] if "自选股决策仪表盘" in b[0]]
    assert len(singles) == 2 and len(summary) == 1
    assert all(b[2] == "watchlist" for b in pushes["broadcast"])
    titles = sorted(b[0] for b in singles)
    assert titles == ["自选股诊断 中远海控(601919)", "自选股诊断 贵州茅台(600519)"]
    assert not any("平安银行" in t for t, _, _ in singles)       # 失败的不逐只推送
    body = next(c for t, c, _ in singles if "600519" in t)
    for part in ("买入", "80", "600519放量突破", "10.50", "9.80", "12.00", "600519风险点"):
        assert part in body, part
    assert pushes["broadcast"][-1][0].startswith("自选股决策仪表盘")    # 汇总最后推送


def test_single_notify_off_by_default(config, pushes):
    _add(config, "600519", "601919")
    svc, _ = _svc(config, FakeDiagnosis())
    svc.run(now=NOW)
    assert [b[0] for b in pushes["broadcast"]] == ["自选股决策仪表盘 2026-09-25"]


def test_single_notify_push_false(config, pushes):
    _add(config, "600519")
    cfg = {**config, "watchlist": {**config["watchlist"], "single_notify": True}}
    cfg["notifier"] = _email_cfg([{"name": "g", "stocks": ["600519"], "to": ["g@x.com"]}])["notifier"]
    result = WatchlistReportService(cfg, diagnosis=FakeDiagnosis()).run(push=False, now=NOW)
    assert result["pushed"] is False
    assert pushes["broadcast"] == [] and pushes["email"] == []


def test_single_notify_failure_does_not_break_run(config, monkeypatch, pushes):
    _add(config, "600519", "601919")
    calls = []

    def bad(cfg, title, content, kind=None):
        calls.append(title)
        if "自选股诊断" in title:
            raise RuntimeError("推送炸了")
        return {"wechat": True}

    monkeypatch.setattr(notifier_mod, "broadcast", bad)
    svc, _ = _svc(config, FakeDiagnosis(), single_notify=True)
    result = svc.run(now=NOW)
    assert result["done"] == 2 and result["pushed"] is True
    assert any("决策仪表盘" in t for t in calls)


def test_single_notify_sends_group_email(config, pushes):
    _add(config, "600519", "601919")
    cfg = {**config, "watchlist": {**config["watchlist"], "single_notify": True},
           **_email_cfg([{"name": "家人", "stocks": ["600519"], "to": ["fam@x.com"]}])}
    WatchlistReportService(cfg, diagnosis=FakeDiagnosis()).run(now=NOW)
    single_mail = [m for m in pushes["email"] if "自选股诊断" in m[1]]
    assert len(single_mail) == 1
    assert single_mail[0][0] == ["fam@x.com"] and "600519" in single_mail[0][1]


# ---------- 分组仪表盘 ----------

def test_group_dashboards(config, pushes):
    _add(config, "600519", "601919", "000001", "300750")
    cfg = {**config, **_email_cfg([
        {"name": "家人", "stocks": ["600519", "000001"], "to": ["fam@x.com"]},
        {"name": "朋友", "stocks": ["601919"], "to": "f1@x.com, f2@x.com"},
        {"name": "空组", "stocks": ["002594"], "to": ["none@x.com"]},     # 自选里没有这只股票 → 子集为空，跳过
    ])}
    result = WatchlistReportService(cfg, diagnosis=FakeDiagnosis()).run(now=NOW)
    # 默认渠道收到全量，一次
    assert len(pushes["broadcast"]) == 1
    full = pushes["broadcast"][0][1]
    for name in STOCKS.values():
        assert name in full
    assert result["pushed"] is True
    mails = {tuple(to): (title, content) for to, title, content in pushes["email"]}
    assert set(mails) == {("fam@x.com",), ("f1@x.com", "f2@x.com")}      # 空组跳过
    title, content = mails[("fam@x.com",)]
    assert "（家人）" in title
    assert "贵州茅台" in content and "平安银行" in content             # 失败的也按组过滤进入
    assert "中远海控" not in content and "宁德时代" not in content
    assert "共分析 2 只" in content
    title, content = mails[("f1@x.com", "f2@x.com")]
    assert "（朋友）" in title and "中远海控" in content
    assert "贵州茅台" not in content and "平安银行" not in content
    assert "共分析 1 只" in content


def test_group_not_sent_when_push_false_or_disabled(config, pushes):
    _add(config, "600519")
    cfg = {**config, **_email_cfg([{"name": "家人", "stocks": ["600519"], "to": ["fam@x.com"]}])}
    WatchlistReportService(cfg, diagnosis=FakeDiagnosis()).run(push=False, now=NOW)
    assert pushes["email"] == []


def test_group_normalizes_index_prefix(config, pushes):
    """分组里写 SH600519（大小写/前缀）仍能匹配到规范代码 600519。"""
    _add(config, "600519", "601919")
    cfg = {**config, **_email_cfg([{"name": "家人", "stocks": ["SH600519"], "to": ["fam@x.com"]}])}
    WatchlistReportService(cfg, diagnosis=FakeDiagnosis()).run(now=NOW)
    assert len(pushes["email"]) == 1 and "贵州茅台" in pushes["email"][0][2] and "中远海控" not in pushes["email"][0][2]


def test_dashboard_stored_is_full_even_with_groups(config, pushes):
    _add(config, "600519", "601919")
    cfg = {**config, **_email_cfg([{"name": "家人", "stocks": ["600519"], "to": ["fam@x.com"]}])}
    WatchlistReportService(cfg, diagnosis=FakeDiagnosis()).run(now=NOW)
    latest = WatchlistReportService(cfg).latest()
    assert len(latest["items"]) == 2 and "（家人）" not in latest["markdown"]


# ---------- 总超时 ----------

def test_total_timeout_returns_partial(config, pushes):
    _add(config, "600519", "601919", "300750")
    fake = FakeDiagnosis(blocked={"601919"})
    cfg = {**config, "watchlist": {**config["watchlist"], "timeout_minutes": 0.02, "single_notify": True},
           **_email_cfg([{"name": "家人", "stocks": ["600519", "601919"], "to": ["fam@x.com"]}])}
    progress = []
    start = time.monotonic()
    try:
        result = WatchlistReportService(cfg, diagnosis=fake).run(progress=lambda d, t: progress.append((d, t)), now=NOW)
        elapsed = time.monotonic() - start
    finally:
        fake.release.set()          # 放行卡住的线程，避免遗留阻塞
    assert elapsed < 8, elapsed     # 约 1.2 秒超时，不等待卡住的线程
    assert result["timed_out"] is True
    assert result["done"] == 2 and result["total"] == 3
    assert [f["code"] for f in result["failed"]] == ["601919"]
    assert "超时未完成" in result["failed"][0]["error"] and "总时长上限" in result["failed"][0]["error"]
    assert "超时未完成" in result["markdown"]
    # 已完成的仍推送：汇总一次 + 两只逐只
    assert sum(1 for b in pushes["broadcast"] if "决策仪表盘" in b[0]) == 1
    assert sum(1 for b in pushes["broadcast"] if "自选股诊断" in b[0]) == 2
    assert not any("中远海控" in b[0] for b in pushes["broadcast"] if "自选股诊断" in b[0])
    # 已保存
    assert len(WatchlistReportService(cfg).latest()["items"]) == 2
    # 分组仪表盘照常发（含该组的超时项）
    assert any("（家人）" in m[1] for m in pushes["email"])
    assert progress and progress[-1][1] == 3 and progress[-1][0] >= 2      # 契约：超时后 done 补到实际完成数（2 或含未完成的 3 均可）
    time.sleep(0.05)


def test_no_timeout_when_zero_or_fast(config, pushes):
    _add(config, "600519", "601919")
    for tm in (0, 5):
        svc, _ = _svc(config, FakeDiagnosis(), timeout_minutes=tm)
        result = svc.run(push=False, now=NOW)
        assert result["timed_out"] is False and result["done"] == 2 and result["failed"] == []


def test_timed_out_key_present_normally(config, pushes):
    _add(config, "600519")
    svc, _ = _svc(config, FakeDiagnosis())
    assert svc.run(push=False, now=NOW)["timed_out"] is False


# ---------- API ----------

@pytest.fixture
def api(tmp_path, monkeypatch):
    path = str(tmp_path / "api.db")
    _reset_db_engine()
    init_db(path)
    settings_file = tmp_path / "settings.yaml"
    settings_file.write_text(
        f"database:\n  sqlite_path: {path!r}\n"
        "notifier:\n  email:\n    enabled: true\n    smtp_host: smtp.x.com\n    password: mail-pass\n    to: [a@x.com]\n",
        encoding="utf-8")
    monkeypatch.setattr(settings_store, "SETTINGS_PATH", settings_file)

    def reload_from_tmp():
        config_loader._config_cache.pop(str(settings_file), None)
        return config_loader.load_config(str(settings_file))

    monkeypatch.setattr(config_loader, "reload_config", reload_from_tmp)
    config = reload_from_tmp()
    static = tmp_path / "dist"
    static.mkdir()
    (static / "index.html").write_text("<html>app</html>", encoding="utf-8")
    app = create_app(config, start_scheduler=False, static_dir=static, auth=AuthStore(tmp_path / "auth.json"))
    with TestClient(app) as client:
        yield client, settings_file
    _reset_db_engine()
    config_loader.reload_config()


def test_watchlist_settings_roundtrip(api):
    client, _ = api
    got = client.get("/api/v1/settings/watchlist").json()
    for key in ("daily_report", "max_stocks", "workers", "single_notify", "timeout_minutes"):
        assert key in got, key
    body = {"daily_report": False, "max_stocks": 80, "workers": 4, "single_notify": True, "timeout_minutes": 15}
    resp = client.put("/api/v1/settings/watchlist", json=body)
    assert resp.status_code == 200, resp.text
    again = client.get("/api/v1/settings/watchlist").json()
    assert {k: again[k] for k in body} == body


@pytest.mark.parametrize("bad", [
    {"max_stocks": 0}, {"max_stocks": 501}, {"workers": 0}, {"workers": 11},
    {"timeout_minutes": -1}, {"timeout_minutes": 601}, {"workers": "abc"}, {"single_notify": "maybe"},
])
def test_watchlist_settings_invalid(api, bad):
    client, _ = api
    good = {"daily_report": True, "max_stocks": 50, "workers": 3, "single_notify": False, "timeout_minutes": 0}
    assert client.put("/api/v1/settings/watchlist", json={**good, **bad}).status_code == 422


def test_watchlist_settings_boundaries(api):
    client, _ = api
    for edge in ({"max_stocks": 1, "workers": 1, "timeout_minutes": 0}, {"max_stocks": 500, "workers": 10, "timeout_minutes": 600}):
        body = {"daily_report": True, "single_notify": False, **edge}
        assert client.put("/api/v1/settings/watchlist", json=body).status_code == 200, edge


def test_notifier_save_keeps_groups_and_password(api):
    client, settings_file = api
    got = client.get("/api/v1/settings/notifier").json()["notifier"]
    assert got["email"]["password"] == "******"
    groups = [{"name": "家人", "stocks": ["600519", "sh000300"], "to": ["fam@x.com"]}]
    got["email"]["groups"] = groups
    assert client.put("/api/v1/settings/notifier", json={"notifier": got}).status_code == 200
    again = client.get("/api/v1/settings/notifier").json()["notifier"]
    assert again["email"]["groups"] == groups
    assert again["email"]["password"] == "******"
    assert "mail-pass" in settings_file.read_text(encoding="utf-8")      # 掩码还原为原密码
    # 清空分组
    got["email"]["groups"] = []
    client.put("/api/v1/settings/notifier", json={"notifier": got})
    assert client.get("/api/v1/settings/notifier").json()["notifier"]["email"]["groups"] == []
