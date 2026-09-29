"""
Telegram Bot 推送（纯文本，不设 parse_mode）
"""

import httpx
from loguru import logger

from src.notifier.base import markdown_to_text, send_paged

# 单条上限 4096 字符，按字节取保守值
MAX_CONTENT_BYTES = 3800


class TelegramNotifier:
    """Telegram Bot 推送（纯文本，不设 parse_mode）"""

    def __init__(self, config: dict):
        cfg = (config.get("notifier") or {}).get("telegram") or {}
        self.enabled = cfg.get("enabled", False)
        self.bot_token = str(cfg.get("bot_token") or "").strip()
        self.chat_id = str(cfg.get("chat_id") or "").strip()
        self.api_base = str(cfg.get("api_base") or "https://api.telegram.org").strip().rstrip("/")
        self.thread_id = str(cfg.get("message_thread_id") or "").strip()

    def send(self, title: str, content: str) -> bool:
        """发送消息，超长内容自动拆成多条；未启用、配置不完整或失败返回 False，不抛异常。"""
        if not self.enabled:
            logger.debug("Telegram推送未启用")
            return False
        if not (self.bot_token and self.chat_id):
            logger.warning("Telegram配置不完整")
            return False
        return send_paged(title, content, MAX_CONTENT_BYTES, self._send_one)

    def _send_one(self, title: str, content: str) -> bool:
        try:
            payload = {"chat_id": self.chat_id, "text": f"{title}\n\n{markdown_to_text(content)}"}
            if self.thread_id.isdigit():
                payload["message_thread_id"] = self.thread_id
            resp = httpx.post(f"{self.api_base}/bot{self.bot_token}/sendMessage", json=payload, timeout=10)
            if resp.json().get("ok") is True:
                logger.info("Telegram推送成功: {}", title)
                return True
            logger.error("Telegram推送失败: {} {}", resp.status_code, resp.text[:200])
            return False
        except Exception as e:
            logger.error("Telegram推送异常: {}", e)
            return False
