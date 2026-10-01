"""消息推送模块

- 渠道：企业微信、钉钉、飞书机器人、邮件（SMTP），以及 Telegram、Discord、Slack、PushPlus、Server酱、ntfy、Gotify、Pushover、Bark 和自定义 Webhook
- 路由：notifier.routes 按消息类型（daily_report 每日报告、alert 盘中提醒、watchlist 自选股仪表盘、chat AI 问股）
  指定推送到哪些渠道；没写或为空的类型推送到全部已启用渠道
- 配置检查：diagnose() 逐个渠道检查缺项和占位符；test_channel() 发一条测试消息
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

from loguru import logger

from src.notifier.bark import BarkNotifier
from src.notifier.dingtalk import DingTalkNotifier
from src.notifier.discord import DiscordNotifier
from src.notifier.feishu import FeishuNotifier
from src.notifier.gotify import GotifyNotifier
from src.notifier.mail import EmailNotifier, recipients
from src.notifier.ntfy import NtfyNotifier
from src.notifier.pushover import PushoverNotifier
from src.notifier.pushplus import PushPlusNotifier
from src.notifier.serverchan import ServerChanNotifier
from src.notifier.slack import SlackNotifier
from src.notifier.telegram import TelegramNotifier
from src.notifier.wechat import WeChatNotifier
from src.notifier.webhook import WebhookNotifier

NOTIFIERS = {
    "wechat": WeChatNotifier,
    "dingtalk": DingTalkNotifier,
    "feishu": FeishuNotifier,
    "email": EmailNotifier,
    "telegram": TelegramNotifier,
    "discord": DiscordNotifier,
    "slack": SlackNotifier,
    "pushplus": PushPlusNotifier,
    "serverchan": ServerChanNotifier,
    "ntfy": NtfyNotifier,
    "gotify": GotifyNotifier,
    "pushover": PushoverNotifier,
    "bark": BarkNotifier,
    "webhook": WebhookNotifier,
}
CHANNEL_LABELS = {
    "wechat": "企业微信", "dingtalk": "钉钉", "feishu": "飞书", "email": "邮件",
    "telegram": "Telegram", "discord": "Discord", "slack": "Slack", "pushplus": "PushPlus", "serverchan": "Server酱",
    "ntfy": "ntfy", "gotify": "Gotify", "pushover": "Pushover", "bark": "Bark", "webhook": "自定义 Webhook",
}
MESSAGE_KINDS = {"daily_report": "每日报告", "alert": "盘中提醒", "watchlist": "自选股仪表盘", "chat": "AI 问股",
                 "system_error": "系统错误"}
# 支持发送图片（send_image）的渠道；notifier.image.channels 只有其中的渠道生效
IMAGE_CHANNELS = {"wechat", "telegram", "email", "discord", "ntfy"}
WEBHOOK_HOSTS = {"wechat": ("qyapi.weixin.qq.com",), "dingtalk": ("oapi.dingtalk.com",), "feishu": ("open.feishu.cn", "open.larksuite.com")}


def _f(key: str, label: str, required: bool = False, secret: bool = False, placeholder: str = "",
       type: str = "text", default: Any = None) -> dict:
    field = {"key": key, "label": label, "required": required, "secret": secret, "placeholder": placeholder, "type": type}
    if default is not None:
        field["default"] = default
    return field


# 新渠道的配置字段，Web 设置页据此通用渲染；旧渠道（企业微信、钉钉、飞书、邮件）有专用表单
CHANNEL_FIELDS: dict[str, list[dict]] = {
    "telegram": [
        _f("bot_token", "Bot Token", True, True, "123456:ABC-DEF..."),
        _f("chat_id", "Chat ID", True, placeholder="用户、群组 ID 或 @频道名"),
        _f("api_base", "API 地址（可填反代）", placeholder="https://api.telegram.org", default="https://api.telegram.org"),
        _f("message_thread_id", "话题 ID（可空）", placeholder="超级群组的话题 message_thread_id"),
    ],
    "discord": [_f("webhook_url", "Webhook 地址", True, True, "https://discord.com/api/webhooks/...")],
    "slack": [_f("webhook_url", "Incoming Webhook 地址", True, True, "https://hooks.slack.com/services/...")],
    "pushplus": [
        _f("token", "Token", True, True),
        _f("topic", "群组编码（可空）", placeholder="一对多推送时填写"),
    ],
    "serverchan": [_f("sendkey", "SendKey", True, True, "SCT... 或 sctp123t...")],
    "ntfy": [
        _f("server", "服务器", placeholder="https://ntfy.sh", default="https://ntfy.sh"),
        _f("topic", "主题", True),
        _f("token", "访问令牌（可空）", secret=True, placeholder="私有主题需要"),
    ],
    "gotify": [
        _f("server", "服务器地址", True, placeholder="https://gotify.example.com"),
        _f("token", "应用 Token", True, True),
        _f("priority", "优先级", type="number", default=5),
    ],
    "pushover": [
        _f("user_key", "User Key", True, True),
        _f("api_token", "API Token", True, True),
    ],
    "bark": [
        _f("server", "服务器", placeholder="https://api.day.app", default="https://api.day.app"),
        _f("device_key", "Device Key", True, True),
        _f("group", "分组（可空）"),
    ],
    "webhook": [
        _f("url", "请求地址", True, True, "https://example.com/hook"),
        _f("method", "请求方法", placeholder="POST", default="POST"),
        _f("headers", "请求头（可空，JSON）", placeholder='{"Authorization": "Bearer xxx"}', type="textarea"),
        _f("body_template", "请求体模板（可空）", placeholder='{"text": $content_json}；可用 $title $content $title_json $content_json', type="textarea"),
    ],
}
URL_KEYS = ("api_base", "server", "url", "webhook_url")
# 有固定域名的 Webhook：必须 https 且域名匹配
STRICT_HOSTS = {"discord": ("discord.com", "discordapp.com"), "slack": ("hooks.slack.com",)}


def secret_fields(name: str) -> list[str]:
    """渠道里需要在接口中掩码的密钥字段。"""
    if name == "email":
        return ["password"]
    return [f["key"] for f in CHANNEL_FIELDS.get(name, []) if f["secret"]]


def _channel_cfg(config: dict, name: str) -> dict:
    return (config.get("notifier", {}) or {}).get(name) or {}


def is_configured(config: dict, name: str) -> bool:
    """配置是否完整；Webhook 还是示例占位符（含 your-）时不算。"""
    cfg = _channel_cfg(config, name)
    if name == "email":
        return EmailNotifier.is_configured(cfg)
    if name in CHANNEL_FIELDS:
        return all(str(cfg.get(f["key"]) or "").strip() and "your-" not in str(cfg.get(f["key"]))
                   for f in CHANNEL_FIELDS[name] if f["required"])
    url = str(cfg.get("webhook_url") or "")
    return bool(url) and "your-" not in url


def enabled_channels(config: dict, kind: str | None = None) -> list[str]:
    """已启用且配置完整的渠道；给了消息类型且 notifier.routes 里为它指定了渠道时，只返回其中的渠道。"""
    channels = [name for name in NOTIFIERS if _channel_cfg(config, name).get("enabled") and is_configured(config, name)]
    route = ((config.get("notifier", {}) or {}).get("routes") or {}).get(kind) if kind else None
    return [c for c in channels if c in route] if route else channels


def _use_image(config: dict, name: str, kind: str | None, content: str) -> bool:
    """该渠道这条消息是否应发分享图：渠道、消息类型都在 notifier.image 里，且字数不超过 max_chars。"""
    if name not in IMAGE_CHANNELS:
        return False
    cfg = (config.get("notifier", {}) or {}).get("image") or {}
    if name not in (cfg.get("channels") or []):
        return False
    if kind not in (cfg.get("kinds") if cfg.get("kinds") is not None else ["daily_report", "watchlist"]):
        return False
    try:
        limit = int(cfg.get("max_chars") or 8000)
    except (TypeError, ValueError):
        limit = 8000
    return len(content) <= limit


def _send_as_image(notifier: Any, name: str, title: str, content: str, config: dict | None = None) -> bool:
    """渲染分享图并发送；渲染失败或发送失败返回 False，由调用方回退成文字。"""
    try:
        from src.services.report_image import render_markdown_image, share_options

        png = render_markdown_image(title, content, **share_options(config))
        if notifier.send_image(title, png):
            return True
        logger.warning(f"[{name}] 图片推送失败，回退为文字: {title}")
    except Exception as e:
        logger.warning(f"[{name}] 生成分享图失败，回退为文字: {e}")
    return False


def broadcast(config: dict, title: str, content: str, kind: str | None = None) -> dict[str, bool]:
    """推送到消息类型对应的已启用渠道，返回 {渠道: 是否成功}；单个渠道失败不影响其他渠道。"""
    if os.environ.get("QUANT_NO_NOTIFY"):
        logger.info(f"已设置 QUANT_NO_NOTIFY，不推送: {title}")
        return {}
    results = {}
    for name in enabled_channels(config, kind):
        try:
            notifier = NOTIFIERS[name](config)
            if _use_image(config, name, kind, content) and _send_as_image(notifier, name, title, content, config):
                results[name] = True
                continue
            results[name] = notifier.send(title, content)
        except Exception as e:
            logger.error(f"[{name}] 推送异常: {e}")
            results[name] = False
    return results


def _diagnose_fields(name: str, cfg: dict) -> list[str]:
    issues: list[str] = []
    for f in CHANNEL_FIELDS[name]:
        value = str(cfg.get(f["key"]) or "").strip()
        if not value:
            if f["required"]:
                issues.append(f"缺少 {f['label']}")
            continue
        if "your-" in value:
            issues.append(f"{f['label']} 还是示例占位符")
        elif f["key"] in URL_KEYS:
            parsed = urlparse(value)
            if name in STRICT_HOSTS:
                if parsed.scheme != "https" or parsed.hostname not in STRICT_HOSTS[name]:
                    issues.append(f"{f['label']} 必须是 https 且域名为 {'/'.join(STRICT_HOSTS[name])}")
            elif parsed.scheme not in ("http", "https"):
                issues.append(f"{f['label']} 应以 http:// 或 https:// 开头")
    return issues


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
        elif name in CHANNEL_FIELDS:
            issues.extend(_diagnose_fields(name, cfg))
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
