"""
Bark 推送（纯文本）
"""

import httpx
from loguru import logger

from src.notifier.base import markdown_to_text, send_paged

# Bark 服务端对 body 无硬性限制，取保守值
MAX_CONTENT_BYTES = 3000


class BarkNotifier:
    """Bark 推送（纯文本）"""

    def __init__(self, config: dict):
        cfg = (config.get("notifier") or {}).get("bark") or {}
        self.enabled = cfg.get("enabled", False)
        self.server = str(cfg.get("server") or "https://api.day.app").strip().rstrip("/")
        self.device_key = str(cfg.get("device_key") or "").strip()
        self.group = str(cfg.get("group") or "").strip()

    def send(self, title: str, content: str) -> bool:
        """发送消息，超长内容自动拆成多条；未启用、配置不完整或失败返回 False，不抛异常。"""
        if not self.enabled:
            logger.debug("Bark推送未启用")
            return False
        if not self.device_key:
            logger.warning("Bark配置不完整")
            return False
        return send_paged(title, content, MAX_CONTENT_BYTES, self._send_one)

    def _send_one(self, title: str, content: str) -> bool:
        try:
            payload = {"device_key": self.device_key, "title": title, "body": markdown_to_text(content)}
            if self.group:
                payload["group"] = self.group
            resp = httpx.post(f"{self.server}/push", json=payload, timeout=10)
            if resp.json().get("code") == 200:
                logger.info("Bark推送成功: {}", title)
                return True
            logger.error("Bark推送失败: {} {}", resp.status_code, resp.text[:200])
            return False
        except Exception as e:
            logger.error("Bark推送异常: {}", e)
            return False
