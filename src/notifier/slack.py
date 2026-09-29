"""
Slack Incoming Webhook 推送（mrkdwn）
"""

import httpx
from loguru import logger

from src.notifier.base import markdown_to_slack, send_paged

# 建议单条不超过 4000 字符
MAX_CONTENT_BYTES = 3500


class SlackNotifier:
    """Slack Incoming Webhook 推送（mrkdwn）"""

    def __init__(self, config: dict):
        cfg = (config.get("notifier") or {}).get("slack") or {}
        self.enabled = cfg.get("enabled", False)
        self.webhook_url = str(cfg.get("webhook_url") or "").strip()

    def send(self, title: str, content: str) -> bool:
        """发送消息，超长内容自动拆成多条；未启用、配置不完整或失败返回 False，不抛异常。"""
        if not self.enabled:
            logger.debug("Slack推送未启用")
            return False
        if not self.webhook_url:
            logger.warning("Slack配置不完整")
            return False
        return send_paged(title, content, MAX_CONTENT_BYTES, self._send_one)

    def _send_one(self, title: str, content: str) -> bool:
        try:
            resp = httpx.post(self.webhook_url, json={"text": f"*{title}*\n\n{markdown_to_slack(content)}"}, timeout=10)
            if resp.status_code == 200:
                logger.info("Slack推送成功: {}", title)
                return True
            logger.error("Slack推送失败: {} {}", resp.status_code, resp.text[:200])
            return False
        except Exception as e:
            logger.error("Slack推送异常: {}", e)
            return False
