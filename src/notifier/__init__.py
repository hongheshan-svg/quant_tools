"""消息推送模块"""

from loguru import logger

from src.notifier.dingtalk import DingTalkNotifier
from src.notifier.feishu import FeishuNotifier
from src.notifier.wechat import WeChatNotifier

NOTIFIERS = {
    "wechat": WeChatNotifier,
    "dingtalk": DingTalkNotifier,
    "feishu": FeishuNotifier,
}


def enabled_channels(config: dict) -> list[str]:
    """已启用且配置了 webhook 的推送渠道。"""
    notifier_cfg = config.get("notifier", {}) or {}
    return [
        name
        for name in NOTIFIERS
        if (notifier_cfg.get(name) or {}).get("enabled") and (notifier_cfg.get(name) or {}).get("webhook_url")
    ]


def broadcast(config: dict, title: str, content: str) -> dict[str, bool]:
    """推送到所有已启用的渠道，返回 {渠道: 是否成功}；单个渠道失败不影响其他渠道。"""
    results = {}
    for name in enabled_channels(config):
        try:
            results[name] = NOTIFIERS[name](config).send(title, content)
        except Exception as e:
            logger.error(f"[{name}] 推送异常: {e}")
            results[name] = False
    return results
