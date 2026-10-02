"""WS13 推送增强：分享图推送、各渠道 send_image、提醒最低级别、提醒日报、设置接口。全部离线。"""

from __future__ import annotations

import base64
import hashlib
import json
from datetime import date, datetime

import httpx
import pytest
from apscheduler.schedulers.background import BackgroundScheduler

from src import notifier
from src import scheduler as sched
from src import trading_calendar
from src.database import db as db_module
from src.database.db import get_db_session, init_db
from src.database.models import AlertRecord, StockDaily, TradeSignal
from src.services import alert_service as alert_mod
from src.services import report_image
from src.services.alert_service import AlertService
from tests.test_api import env  # noqa: F401  (pytest fixture)

PNG = b"\x89PNG\r\n\x1a\n" + b"0123456789" * 20
TODAY = date.today().strftime("%Y-%m-%d")


# ---------- broadcast 图片分流 ----------

class _Chan:
    """记录 send / send_image 调用的假渠道。"""
    log: list[tuple] = []
    image_ok = True

    def __init__(self, config):
        pass

    def send(self, title, content):
        _Chan.log.append(("send", title))
        return True

    def send_image(self, title, png, *a, **k):
        _Chan.log.append(("image", title, png))
        return _Chan.image_ok


class _TextOnly:
    def __init__(self, config):
        pass

    def send(self, title, content):
        _Chan.log.append(("text_only_send", title))
        return True


def _img_config(**image):
    cfg = {"channels": ["wechat"], "kinds": ["daily_report"], "max_chars": 500}
    cfg.update(image)
    return {"notifier": {
        "wechat": {"enabled": True, "webhook_url": "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=abc"},
        "image": cfg}}


@pytest.fixture
def chan(monkeypatch):
    _Chan.log = []
    _Chan.image_ok = True
    rendered: list[tuple] = []

    def fake_render(title, markdown, footer="", brand="", qr_url=""):
        rendered.append((title, markdown))
        return PNG

    monkeypatch.setattr(report_image, "render_markdown_image", fake_render)
    monkeypatch.setitem(notifier.NOTIFIERS, "wechat", _Chan)
    monkeypatch.setitem(notifier.NOTIFIERS, "dingtalk", _TextOnly)
    monkeypatch.delenv("QUANT_NO_NOTIFY", raising=False)
    return rendered


def test_image_channels_constant():
    assert notifier.IMAGE_CHANNELS == {"wechat", "telegram", "email", "discord", "ntfy"}


def test_broadcast_sends_image_for_matching_channel(chan):
    result = notifier.broadcast(_img_config(), "日报", "简短内容", kind="daily_report")
    assert [e[0] for e in _Chan.log] == ["image"]
    assert _Chan.log[0][2] == PNG
    assert result.get("wechat") is True
    assert len(chan) == 1


def test_broadcast_falls_back_to_text(chan):
    cfg = _img_config()
    notifier.broadcast(cfg, "日报", "内容", kind="alert")                                   # kind 不在列表
    notifier.broadcast(cfg, "日报", "长" * 501, kind="daily_report")                        # 超长
    assert [e[0] for e in _Chan.log] == ["send", "send"]
    assert chan == []                                                                        # 没有渲染


def test_broadcast_channel_not_in_image_channels_uses_send(chan):
    cfg = _img_config(channels=["dingtalk"])
    cfg["notifier"] = {"dingtalk": {"enabled": True, "webhook_url": "https://oapi.dingtalk.com/robot/send?access_token=abc"},
                       "image": cfg["notifier"]["image"]}
    notifier.broadcast(cfg, "日报", "内容", kind="daily_report")
    assert [e[0] for e in _Chan.log] == ["text_only_send"]           # 钉钉不支持图片
    _Chan.log.clear()
    notifier.broadcast(_img_config(channels=[]), "日报", "内容", kind="daily_report")
    assert [e[0] for e in _Chan.log] == ["send"]


def test_broadcast_render_error_falls_back(chan, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("no chromium")

    monkeypatch.setattr(report_image, "render_markdown_image", boom)
    result = notifier.broadcast(_img_config(), "日报", "内容", kind="daily_report")
    assert [e[0] for e in _Chan.log] == ["send"]
    assert result.get("wechat") is True


def test_broadcast_send_image_false_falls_back(chan):
    _Chan.image_ok = False
    result = notifier.broadcast(_img_config(), "日报", "内容", kind="daily_report")
    assert [e[0] for e in _Chan.log] == ["image", "send"]
    assert result.get("wechat") is True


def test_broadcast_no_notify_env(chan, monkeypatch):
    monkeypatch.setenv("QUANT_NO_NOTIFY", "1")
    assert notifier.broadcast(_img_config(), "日报", "内容", kind="daily_report") == {}
    assert _Chan.log == [] and chan == []


# ---------- 各渠道 send_image 请求格式 ----------

class _Resp:
    def __init__(self, status_code=200, data=None):
        self.status_code = status_code
        self._data = data if data is not None else {}
        self.text = json.dumps(self._data)

    def json(self):
        return self._data


class _Rec:
    def __init__(self):
        self.calls: list[dict] = []
        self.resp = _Resp()

    def __call__(self, *args, **kwargs):
        self.calls.append({"args": args, "kwargs": kwargs})
        return self.resp


@pytest.fixture
def http(monkeypatch):
    rec = _Rec()
    for name in ("post", "get", "request", "put"):
        monkeypatch.setattr(httpx, name, rec, raising=False)
    return rec


def _url(call):
    for a in call["args"]:
        if isinstance(a, str) and a.startswith("http"):
            return a
    return str(call["kwargs"].get("url", ""))


def test_wechat_send_image(http):
    from src.notifier.wechat import WeChatNotifier

    http.resp = _Resp(200, {"errcode": 0})
    n = WeChatNotifier({"notifier": {"wechat": {"enabled": True, "webhook_url": "https://qyapi.weixin.qq.com/x?key=1"}}})
    assert n.send_image("标题", PNG) is True
    body = http.calls[0]["kwargs"]["json"]
    assert body["msgtype"] == "image"
    assert base64.b64decode(body["image"]["base64"]) == PNG
    assert body["image"]["md5"] == hashlib.md5(PNG).hexdigest()


def test_wechat_send_image_too_large(http):
    from src.notifier.wechat import WeChatNotifier

    n = WeChatNotifier({"notifier": {"wechat": {"enabled": True, "webhook_url": "https://qyapi.weixin.qq.com/x?key=1"}}})
    assert n.send_image("标题", b"x" * (2 * 1024 * 1024 + 1)) is False
    assert http.calls == []


def test_telegram_send_image(http):
    from src.notifier.telegram import TelegramNotifier

    http.resp = _Resp(200, {"ok": True})
    n = TelegramNotifier({"notifier": {"telegram": {"enabled": True, "bot_token": "123:ABC", "chat_id": "-100"}}})
    assert n.send_image("日报标题", PNG) is True
    call = http.calls[0]
    assert _url(call).endswith("/bot123:ABC/sendPhoto")
    files = call["kwargs"].get("files")
    assert files and "photo" in files
    assert "日报标题" in json.dumps(call["kwargs"].get("data") or {}, ensure_ascii=False)   # caption


def test_discord_send_image(http):
    from src.notifier.discord import DiscordNotifier

    http.resp = _Resp(204)
    n = DiscordNotifier({"notifier": {"discord": {"enabled": True, "webhook_url": "https://discord.com/api/webhooks/1/abc"}}})
    assert n.send_image("标题", PNG) is True
    call = http.calls[0]
    assert _url(call).startswith("https://discord.com/api/webhooks/1/abc")
    assert call["kwargs"].get("files")


def test_ntfy_send_image(http):
    from src.notifier.ntfy import NtfyNotifier

    n = NtfyNotifier({"notifier": {"ntfy": {"enabled": True, "topic": "quant"}}})
    assert n.send_image("图片标题", PNG) is True
    call = http.calls[0]
    kw = call["kwargs"]
    assert (kw.get("content") or kw.get("data")) == PNG
    where = json.dumps(kw.get("params") or {}, ensure_ascii=False) + _url(call)
    assert "图片标题" in where or "%E5%9B%BE" in where


def test_email_send_image(monkeypatch):
    import smtplib

    from src.notifier.mail import EmailNotifier

    sent: list[str] = []

    class FakeSMTP:
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
            sent.append(msg)

    monkeypatch.setattr(smtplib, "SMTP_SSL", FakeSMTP)
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    cfg = {"notifier": {"email": {"enabled": True, "smtp_host": "smtp.x.com", "username": "a@x.com", "password": "p", "to": ["b@x.com"]}}}
    assert EmailNotifier(cfg).send_image("邮件图", PNG) is True
    assert sent and "image/png" in sent[0]


# ---------- 提醒最低推送级别 ----------

def _reset_db_engine():
    if db_module._engine:
        db_module._engine.dispose()
    db_module._engine = None
    db_module._SessionFactory = None


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = str(tmp_path / "push.db")
    _reset_db_engine()
    init_db(path)
    alert_mod.reset_state()
    monkeypatch.setattr(trading_calendar, "in_trade_session", lambda now=None: True)
    with get_db_session(path) as session:
        session.add(TradeSignal(code="600001", name="涨停股", signal_date=TODAY, signal_type="premarket"))
        session.add(TradeSignal(code="000002", name="大跌股", signal_date=TODAY, signal_type="buy"))
        session.add(StockDaily(code="sh600001", name="涨停股", trade_date=TODAY, close=11.0, change_pct=10.0))
        session.add(StockDaily(code="000002", name="大跌股", trade_date=TODAY, close=9.2, change_pct=-8.0))
    monkeypatch.setattr(AlertService, "_positions", lambda self: [])
    yield path
    alert_mod.reset_state()
    _reset_db_engine()


def _patch_push(monkeypatch):
    pushed = []
    monkeypatch.setattr(alert_mod, "enabled_channels", lambda config, kind=None: ["wechat"])
    monkeypatch.setattr(alert_mod, "broadcast",
                        lambda config, title, content, kind=None: pushed.append((title, content)) or {"wechat": True})
    return pushed


def test_min_severity_filters_low_events(db_path, monkeypatch):
    pushed = _patch_push(monkeypatch)
    service = AlertService({"database": {"sqlite_path": db_path}, "alerts": {"min_severity": "warning"}, "notifier": {}})
    service.run(datetime(2026, 9, 28, 10, 0))
    assert len(pushed) == 1
    assert "大跌" in pushed[0][1] and "封涨停" not in pushed[0][1]       # limit_up 是 info，低于 warning
    rows = {r["type"]: r for r in service.recent()}
    assert rows["封涨停"]["notified"] is False and "低于推送级别" in rows["封涨停"]["reason"]
    assert rows["大大跌".replace("大大", "大")]["notified"] is True


def test_min_severity_critical_pushes_nothing_and_default_pushes_all(db_path, monkeypatch):
    pushed = _patch_push(monkeypatch)
    service = AlertService({"database": {"sqlite_path": db_path}, "alerts": {"min_severity": "critical"}, "notifier": {}})
    service.run(datetime(2026, 9, 28, 10, 0))
    assert pushed == []
    assert all("低于推送级别" in r["reason"] for r in service.recent())
    assert len(service.recent()) == 2

    alert_mod.reset_state()
    with get_db_session(db_path) as session:
        session.query(AlertRecord).delete()
        from src.database.models import AlertCooldown
        session.query(AlertCooldown).delete()  # 第二个独立场景同时重置持久化冷却
    AlertService({"database": {"sqlite_path": db_path}, "alerts": {}, "notifier": {}}).run(datetime(2026, 9, 28, 10, 5))
    assert len(pushed) == 1 and "封涨停" in pushed[0][1] and "大跌" in pushed[0][1]


# ---------- 提醒日报 ----------

def _seed_records(db_path):
    now = datetime.now()
    rows = [
        ("600001", "涨停股", "limit_up", "info"),
        ("600001", "涨停股", "limit_open", "warning"),
        ("300003", "持仓股", "stop_loss", "critical"),
        ("300003", "持仓股", "near_stop", "warning"),
        ("300003", "持仓股", "big_drop", "warning"),
        ("000002", "大跌股", "big_drop", "warning"),
    ]
    with get_db_session(db_path) as session:
        for code, name, typ, sev in rows:
            session.add(AlertRecord(code=code, name=name, alert_type=typ, severity=sev, message=f"{name}({code}) {typ}",
                                    notified=True, triggered_at=now))
        session.add(AlertRecord(code="999999", name="昨日", alert_type="limit_up", severity="info", message="old",
                                notified=True, triggered_at=datetime(2020, 1, 1, 10, 0)))


def _digest_service(db_path):
    return AlertService({"database": {"sqlite_path": db_path}, "alerts": {}, "notifier": {}})


def test_digest_counts_and_push(db_path, monkeypatch):
    pushed = _patch_push(monkeypatch)
    _seed_records(db_path)
    result = _digest_service(db_path).digest()
    assert {"day", "total", "by_type", "by_severity", "top_stocks", "critical", "markdown", "pushed"} <= set(result)
    assert result["day"] == TODAY and result["total"] == 6
    assert result["by_severity"] == {"info": 1, "warning": 4, "critical": 1}
    assert result["by_type"].get("big_drop", result["by_type"].get("大跌")) == 2
    top = result["top_stocks"]
    assert "300003" in json.dumps(top, ensure_ascii=False)
    assert "300003" in json.dumps(top[0], ensure_ascii=False)   # 提醒最多的股票排第一
    assert len(result["critical"]) == 1 and "300003" in json.dumps(result["critical"], ensure_ascii=False)
    assert result["pushed"] is True
    assert len(pushed) == 1 and "盘中提醒日报" in pushed[0][0]
    assert result["markdown"]


def test_digest_specific_day_and_no_push(db_path, monkeypatch):
    pushed = _patch_push(monkeypatch)
    _seed_records(db_path)
    result = _digest_service(db_path).digest(push=False)
    assert result["total"] == 6 and result["pushed"] is False and pushed == []
    old = _digest_service(db_path).digest(day="2020-01-01", push=False)
    assert old["day"] == "2020-01-01" and old["total"] == 1


def test_digest_empty_does_not_push(db_path, monkeypatch):
    pushed = _patch_push(monkeypatch)
    result = _digest_service(db_path).digest()
    assert result["total"] == 0 and result["pushed"] is False
    assert result["critical"] == [] and result["top_stocks"] == []
    assert pushed == []


# ---------- 调度：提醒日报任务 ----------

def test_digest_job_registered_only_when_enabled():
    assert "alert_digest" in sched.JOBS
    default = sched.build_scheduler({}, BackgroundScheduler())
    assert "alert_digest" not in {j.id for j in default.get_jobs()}
    built = sched.build_scheduler({"alerts": {"daily_digest": True, "digest_time": "15:20"}}, BackgroundScheduler())
    assert "alert_digest" in {j.id for j in built.get_jobs()}
    assert callable(sched.JOBS["alert_digest"][1])


# ---------- API ----------

def test_notifier_settings_include_image_channels(env):  # noqa: F811
    client, _, _ = env
    data = client.get("/api/v1/settings/notifier").json()
    assert set(data["image_channels"]) == {"wechat", "telegram", "email", "discord", "ntfy"}


def test_alert_settings_new_fields(env):  # noqa: F811
    client, _, _ = env
    data = client.get("/api/v1/alerts/settings").json()
    assert data["min_severity"] in ("info", "warning", "critical")
    assert data["daily_digest"] is False or data["daily_digest"] is True
    assert isinstance(data["digest_time"], str)

    saved = client.put("/api/v1/alerts/settings", json={"min_severity": "warning", "daily_digest": True, "digest_time": "15:20"})
    assert saved.status_code == 200
    body = saved.json()
    assert body["min_severity"] == "warning" and body["daily_digest"] is True and body["digest_time"] == "15:20"
    again = client.get("/api/v1/alerts/settings").json()
    assert again["min_severity"] == "warning" and again["digest_time"] == "15:20"


@pytest.mark.parametrize("payload", [
    {"min_severity": "urgent"}, {"min_severity": 3},
    {"digest_time": "25:00"}, {"digest_time": "1520"}, {"digest_time": "9:5"}, {"digest_time": ""},
    {"daily_digest": "yes"},
])
def test_alert_settings_invalid(env, payload):  # noqa: F811
    client, _, _ = env
    assert client.put("/api/v1/alerts/settings", json=payload).status_code == 422
