"""
Pushover 推送（纯文本）
"""

import httpx
from loguru import logger

from src.notifier.base import markdown_to_text, send_paged

# message 上限 1024 字符，按字节取保守值
MAX_CONTENT_BYTES = 1000


class PushoverNotifier:
    """Pushover 推送（纯文本）"""

    def __init__(self, config: dict):
        cfg = (config.get("notifier") or {}).get("pushover") or {}
        self.enabled = cfg.get("enabled", False)
        self.user_key = str(cfg.get("user_key") or "").strip()
        self.api_token = str(cfg.get("api_token") or "").strip()

    def send(self, title: str, content: str) -> bool:
        """发送消息，超长内容自动拆成多条；未启用、配置不完整或失败返回 False，不抛异常。"""
        if not self.enabled:
            logger.debug("Pushover推送未启用")
            return False
        if not (self.user_key and self.api_token):
            logger.warning("Pushover配置不完整")
            return False
        return send_paged(title, content, MAX_CONTENT_BYTES, self._send_one)

    def _send_one(self, title: str, content: str) -> bool:
        try:
            payload = {"token": self.api_token, "user": self.user_key, "title": title[:250],
                       "message": markdown_to_text(content)}
            resp = httpx.post("https://api.pushover.net/1/messages.json", data=payload, timeout=10)
            if resp.json().get("status") == 1:
                logger.info("Pushover推送成功: {}", title)
                return True
            logger.error("Pushover推送失败: {} {}", resp.status_code, resp.text[:200])
            return False
        except Exception as e:
            logger.error("Pushover推送异常: {}", e)
            return False
