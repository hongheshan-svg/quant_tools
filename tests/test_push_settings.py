"""推送增强：按消息类型路由、配置检查、测试消息、邮件推送、设置保存。"""

from __future__ import annotations

import yaml

from src import notifier as notifier_mod
from src.notifier import broadcast, diagnose, enabled_channels
from src.notifier import test_channel as send_test_message  # 避免被 pytest 当成测试
from src.notifier import mail as mail_mod
from src.notifier.base import markdown_to_html
from src.notifier.mail import EmailNotifier, recipients
from src.notifier.settings import parse_quiet_hours, save_notifier_settings

WECHAT = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=abc"
FEISHU = "https://open.feishu.cn/open-apis/bot/v2/hook/xyz"
EMAIL = {"enabled": True, "smtp_host": "smtp.qq.com", "smtp_port": 465, "use_ssl": True, "username": "me@qq.com",
         "password": "code", "to": ["a@x.com", "b@x.com"]}


def _config(**notifier):
    return {"notifier": {"wechat": {"enabled": True, "webhook_url": WECHAT}, "feishu": {"enabled": True, "webhook_url": FEISHU},
                         "dingtalk": {"enabled": True, "webhook_url": "https://oapi.dingtalk.com/robot/send?access_token=your-token"},
                         "email": EMAIL, **notifier}}


def test_routes_and_placeholders(monkeypatch):
    config = _config(routes={"alert": ["feishu"], "watchlist": ["email", "wechat"], "chat": []})
    assert enabled_channels(config) == ["wechat", "feishu", "email"]          # 钉钉还是占位符，不算配置完整
    assert enabled_channels(config, "alert") == ["feishu"]
    assert enabled_channels(config, "watchlist") == ["wechat", "email"]
    assert enabled_channels(config, "chat") == enabled_channels(config, "daily_report") == ["wechat", "feishu", "email"]

    sent = []

    class _Fake:
        def __init__(self, cfg):
            pass

        def send(self, title, content):
            sent.append(title)
            return True

    for name in ("wechat", "feishu", "email"):
        monkeypatch.setitem(notifier_mod.NOTIFIERS, name, type(name, (_Fake,), {}))
    assert broadcast(config, "盘中提醒", "x", kind="alert") == {"feishu": True}
    assert broadcast(config, "日报", "x") == {"wechat": True, "feishu": True, "email": True}


def test_diagnose_config():
    config = _config(
        wechat={"enabled": True, "webhook_url": "http://qyapi.weixin.qq.com/x"},
        feishu={"enabled": False, "webhook_url": "https://example.com/hook", "secret": "your-secret"},
        email={"enabled": True, "smtp_host": "smtp.qq.com", "to": []},
        routes={"alert": ["dingtalk"], "chat": ["line"]},
    )
    result = diagnose(config)
    issues = {c["channel"]: c["issues"] for c in result["channels"]}
    assert issues["wechat"] == ["Webhook 地址应以 https:// 开头"]
    assert issues["dingtalk"] == ["Webhook 地址还是示例占位符"]
    assert issues["feishu"] == ["Webhook 域名不是 open.feishu.cn/open.larksuite.com，请确认复制的是飞书机器人地址",
                                "加签密钥还是示例占位符（不需要加签就留空）"]
    assert issues["email"] == ["缺少收件人", "没有填写账号或授权码，大多数邮箱的 SMTP 需要登录"]
    assert result["routes"] == ["盘中提醒 只推送到 钉钉，但这些渠道都没启用或配置不完整，这类消息不会推送",
                                "AI 问股 的路由里有未知渠道：line",
                                "AI 问股 只推送到 line，但这些渠道都没启用或配置不完整，这类消息不会推送"]
    assert diagnose(_config())["channels"][0]["issues"] == []


def test_test_channel(monkeypatch):
    seen = {}

    class _Fake:
        def __init__(self, cfg):
            seen["enabled"] = cfg["notifier"]["wechat"]["enabled"]

        def send(self, title, content):
            seen["title"] = title
            return True

    monkeypatch.setitem(notifier_mod.NOTIFIERS, "wechat", _Fake)
    config = _config(wechat={"enabled": False, "webhook_url": WECHAT})
    assert send_test_message(config, "wechat") == {"ok": True, "error": ""}
    assert seen == {"enabled": True, "title": "推送测试"}                  # 未启用的渠道也能测试
    assert send_test_message(config, "dingtalk") == {"ok": False, "error": "配置不完整"}
    assert send_test_message(config, "sms")["error"] == "未知渠道 sms"


class _FakeSMTP:
    instances = []

    def __init__(self, host, port, timeout=None):
        self.host, self.port, self.calls = host, port, []
        _FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self):
        self.calls.append("starttls")

    def login(self, user, password):
        self.calls.append(("login", user, password))

    def sendmail(self, sender, to, message):
        self.calls.append(("sendmail", sender, tuple(to)))
        self.message = message


def test_email_notifier(monkeypatch):
    _FakeSMTP.instances.clear()
    monkeypatch.setattr(mail_mod.smtplib, "SMTP_SSL", _FakeSMTP)
    monkeypatch.setattr(mail_mod.smtplib, "SMTP", _FakeSMTP)
    assert EmailNotifier({"notifier": {"email": EMAIL}}).send("自选股决策仪表盘", "## 结论\n- **买入** 1 只")
    smtp = _FakeSMTP.instances[0]
    assert (smtp.host, smtp.port) == ("smtp.qq.com", 465)
    assert smtp.calls == [("login", "me@qq.com", "code"), ("sendmail", "me@qq.com", ("a@x.com", "b@x.com"))]
    assert "text/html" in smtp.message and "text/plain" in smtp.message

    EmailNotifier({"notifier": {"email": {**EMAIL, "use_ssl": False, "smtp_port": 587, "to": "c@x.com；d@x.com"}}}).send("t", "c")
    assert _FakeSMTP.instances[1].calls[0] == "starttls" and _FakeSMTP.instances[1].calls[-1][2] == ("c@x.com", "d@x.com")

    def boom(*a, **k):
        raise OSError("connection refused")

    monkeypatch.setattr(mail_mod.smtplib, "SMTP_SSL", boom)
    assert EmailNotifier({"notifier": {"email": EMAIL}}).send("t", "c") is False
    assert EmailNotifier({"notifier": {"email": {**EMAIL, "enabled": False}}}).send("t", "c") is False
    assert recipients("a@x.com, b@x.com;c@x.com") == ["a@x.com", "b@x.com", "c@x.com"]


def test_markdown_to_html():
    html = markdown_to_html("## 标题\n- **买入** <b>\n- 观望\n\n> 仅供参考\n---\n普通段落")
    assert "<h3>标题</h3>" in html
    assert "<ul>\n<li><b>买入</b> &lt;b&gt;</li>\n<li>观望</li>\n</ul>" in html        # 列表和转义
    assert "<blockquote style='color:#666'>仅供参考</blockquote>" in html and "<hr>" in html and "<p>普通段落</p>" in html


def test_settings_helpers(tmp_path):
    assert parse_quiet_hours("22:00-08:00") == ["22:00", "08:00"]
    assert parse_quiet_hours("22:00～08:00") == ["22:00", "08:00"]
    assert parse_quiet_hours("9:00-11:30") == [] and parse_quiet_hours("") == []

    path = tmp_path / "settings.yaml"
    path.write_text(yaml.dump({"llm": {"primary": {"model": "x"}}, "notifier": {"daily_report_enabled": False}}), encoding="utf-8")
    save_notifier_settings({"wechat": {"enabled": True, "webhook_url": WECHAT}, "routes": {"alert": ["wechat"]}}, path)
    saved = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert saved["llm"] == {"primary": {"model": "x"}}                                  # 其他配置不变
    assert saved["notifier"] == {"daily_report_enabled": False, "wechat": {"enabled": True, "webhook_url": WECHAT},
                                 "routes": {"alert": ["wechat"]}}
    save_notifier_settings({"quiet_hours": []}, tmp_path / "new" / "settings.yaml")      # 文件不存在时新建
    assert yaml.safe_load((tmp_path / "new" / "settings.yaml").read_text(encoding="utf-8")) == {"notifier": {"quiet_hours": []}}
