"""消息推送模块

- 渠道：企业微信、钉钉、飞书机器人和邮件（SMTP）
- 路由：notifier.routes 按消息类型（daily_report 每日报告、alert 盘中提醒、watchlist 自选股仪表盘、chat AI 问股）
  指定推送到哪些渠道；没写或为空的类型推送到全部已启用渠道
- 配置检查：diagnose() 逐个渠道检查缺项和占位符；test_channel() 发一条测试消息
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from urllib.parse import urlparse

from loguru import logger

from src.notifier.dingtalk import DingTalkNotifier
from src.notifier.feishu import FeishuNotifier
from src.notifier.mail import EmailNotifier, recipients
from src.notifier.wechat import WeChatNotifier

NOTIFIERS = {
    "wechat": WeChatNotifier,
    "dingtalk": DingTalkNotifier,
    "feishu": FeishuNotifier,
    "email": EmailNotifier,
}
CHANNEL_LABELS = {"wechat": "企业微信", "dingtalk": "钉钉", "feishu": "飞书", "email": "邮件"}
MESSAGE_KINDS = {"daily_report": "每日报告", "alert": "盘中提醒", "watchlist": "自选股仪表盘", "chat": "AI 问股"}
WEBHOOK_HOSTS = {"wechat": ("qyapi.weixin.qq.com",), "dingtalk": ("oapi.dingtalk.com",), "feishu": ("open.feishu.cn", "open.larksuite.com")}


def _channel_cfg(config: dict, name: str) -> dict:
    return (config.get("notifier", {}) or {}).get(name) or {}


def is_configured(config: dict, name: str) -> bool:
    """配置是否完整；Webhook 还是示例占位符（含 your-）时不算。"""
    cfg = _channel_cfg(config, name)
    if name == "email":
        return EmailNotifier.is_configured(cfg)
    url = str(cfg.get("webhook_url") or "")
    return bool(url) and "your-" not in url


def enabled_channels(config: dict, kind: str | None = None) -> list[str]:
    """已启用且配置完整的渠道；给了消息类型且 notifier.routes 里为它指定了渠道时，只返回其中的渠道。"""
    channels = [name for name in NOTIFIERS if _channel_cfg(config, name).get("enabled") and is_configured(config, name)]
    route = ((config.get("notifier", {}) or {}).get("routes") or {}).get(kind) if kind else None
    return [c for c in channels if c in route] if route else channels


def broadcast(config: dict, title: str, content: str, kind: str | None = None) -> dict[str, bool]:
    """推送到消息类型对应的已启用渠道，返回 {渠道: 是否成功}；单个渠道失败不影响其他渠道。"""
    results = {}
    for name in enabled_channels(config, kind):
        try:
            results[name] = NOTIFIERS[name](config).send(title, content)
        except Exception as e:
            logger.error(f"[{name}] 推送异常: {e}")
            results[name] = False
    return results


def diagnose(config: dict) -> dict[str, Any]:
    """推送配置检查：{"channels": [{channel, label, enabled, configured, issues}], "routes": [...问题]}。"""
    channels = []
    for name in NOTIFIERS:
        cfg = _channel_cfg(config, name)
        issues: list[str] = []
        if name == "email":
            if not str(cfg.get("smtp_host") or "").strip():
                issues.append("缺少 SMTP 服务器")
            if not recipients(cfg.get("to")):
                issues.append("缺少收件人")
            if not cfg.get("username") or not cfg.get("password"):
                issues.append("没有填写账号或授权码，大多数邮箱的 SMTP 需要登录")
            try:
                int(cfg.get("smtp_port") or 465)
            except (TypeError, ValueError):
                issues.append("端口不是数字")
        else:
            url = str(cfg.get("webhook_url") or "").strip()
            parsed = urlparse(url)
            if not url:
                issues.append("缺少 Webhook 地址")
            elif "your-" in url:
                issues.append("Webhook 地址还是示例占位符")
            elif parsed.scheme != "https":
                issues.append("Webhook 地址应以 https:// 开头")
            elif parsed.hostname not in WEBHOOK_HOSTS[name]:
                issues.append(f"Webhook 域名不是 {'/'.join(WEBHOOK_HOSTS[name])}，请确认复制的是{CHANNEL_LABELS[name]}机器人地址")
            if "your-" in str(cfg.get("secret") or ""):
                issues.append("加签密钥还是示例占位符（不需要加签就留空）")
        channels.append({"channel": name, "label": CHANNEL_LABELS[name], "enabled": bool(cfg.get("enabled")),
                         "configured": is_configured(config, name), "issues": issues})

    route_issues = []
    enabled = {c["channel"] for c in channels if c["enabled"] and c["configured"]}
    for kind, route in ((config.get("notifier", {}) or {}).get("routes") or {}).items():
        label = MESSAGE_KINDS.get(kind, kind)
        unknown = [c for c in route or [] if c not in NOTIFIERS]
        if unknown:
            route_issues.append(f"{label} 的路由里有未知渠道：{'、'.join(unknown)}")
        if route and not set(route) & enabled:
            route_issues.append(f"{label} 只推送到 {'、'.join(CHANNEL_LABELS.get(c, c) for c in route)}，但这些渠道都没启用或配置不完整，这类消息不会推送")
    return {"channels": channels, "routes": route_issues}


def test_channel(config: dict, name: str) -> dict[str, Any]:
    """用当前配置给一个渠道发测试消息（不要求已启用）。"""
    if name not in NOTIFIERS:
        return {"ok": False, "error": f"未知渠道 {name}"}
    if not is_configured(config, name):
        return {"ok": False, "error": "配置不完整"}
    notifier_cfg = dict(config.get("notifier", {}) or {})
    notifier_cfg[name] = {**_channel_cfg(config, name), "enabled": True}
    try:
        ok = NOTIFIERS[name]({**config, "notifier": notifier_cfg}).send(
            "推送测试", f"这是一条来自 A股量化系统的测试消息（{datetime.now():%Y-%m-%d %H:%M:%S}）。收到说明{CHANNEL_LABELS[name]}推送配置正确。")
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}
    return {"ok": ok, "error": "" if ok else "发送失败，详见日志"}
