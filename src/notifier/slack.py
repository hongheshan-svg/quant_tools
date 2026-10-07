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
        self.bot_token = str(cfg.get("bot_token") or "").strip()
        self.channel_id = str(cfg.get("channel_id") or "").strip()

    def send(self, title: str, content: str) -> bool:
        """发送消息，超长内容自动拆成多条；未启用、配置不完整或失败返回 False，不抛异常。"""
        if not self.enabled:
            logger.debug("Slack推送未启用")
            return False
        if not self.webhook_url and not (self.bot_token and self.channel_id):
            logger.warning("Slack配置不完整")
            return False
        return send_paged(title, content, MAX_CONTENT_BYTES, self._send_one)

    def _send_one(self, title: str, content: str) -> bool:
        try:
            if self.bot_token and self.channel_id:
                return bool(self._api("chat.postMessage", {"channel": self.channel_id,
                    "text": f"*{title}*\n\n{markdown_to_slack(content)}"}).get("ok"))
            resp = httpx.post(self.webhook_url, json={"text": f"*{title}*\n\n{markdown_to_slack(content)}"}, timeout=10)
            if resp.status_code == 200:
                logger.info("Slack推送成功: {}", title)
                return True
            logger.error("Slack推送失败: {} {}", resp.status_code, resp.text[:200])
            return False
        except Exception:
            logger.warning("Slack 推送失败，请检查配置及连接")
            return False

    def _api(self, method: str, payload: dict) -> dict:
        return httpx.post("https://slack.com/api/" + method, headers={"Authorization": "Bearer " + self.bot_token},
                          json=payload, timeout=15).json()

    def send_image(self, title: str, image: bytes) -> bool:
        if not self.enabled or not self.bot_token or not self.channel_id:
            return False
        from urllib.parse import urlsplit
        try:
            result = httpx.get("https://slack.com/api/files.getUploadURLExternal",
                headers={"Authorization": "Bearer " + self.bot_token},
                params={"filename": "report.png", "length": len(image)}, timeout=15).json()
            if not result.get("ok"):
                return False
            url = urlsplit(result["upload_url"])
            if url.scheme != "https" or not (url.hostname or "").endswith(".slack.com") or url.username:
                return False
            upload = httpx.post(result["upload_url"], content=image, timeout=30)
            if upload.status_code != 200:
                return False
            return bool(self._api("files.completeUploadExternal", {"files": [{"id": result["file_id"], "title": title}],
                "channel_id": self.channel_id, "initial_comment": title}).get("ok"))
        except Exception:
            logger.warning("Slack 图片发送失败，请检查 Bot 权限和频道成员关系")
            return False
