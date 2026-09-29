"""
Gotify 推送（markdown）
"""

import httpx
from loguru import logger

from src.notifier.base import send_paged

# Gotify 没有硬性上限，取保守值
MAX_CONTENT_BYTES = 16000


class GotifyNotifier:
    """Gotify 推送（markdown）"""

    def __init__(self, config: dict):
        cfg = (config.get("notifier") or {}).get("gotify") or {}
        self.enabled = cfg.get("enabled", False)
        self.server = str(cfg.get("server") or "").strip().rstrip("/")
        self.token = str(cfg.get("token") or "").strip()
        try:
            self.priority = int(cfg.get("priority") if cfg.get("priority") not in (None, "") else 5)
        except (TypeError, ValueError):
            self.priority = 5

    def send(self, title: str, content: str) -> bool:
        """发送消息，超长内容自动拆成多条；未启用、配置不完整或失败返回 False，不抛异常。"""
        if not self.enabled:
            logger.debug("Gotify推送未启用")
            return False
        if not (self.server and self.token):
            logger.warning("Gotify配置不完整")
            return False
        return send_paged(title, content, MAX_CONTENT_BYTES, self._send_one)

    def _send_one(self, title: str, content: str) -> bool:
        try:
            payload = {"title": title, "message": content, "priority": self.priority,
                       "extras": {"client::display": {"contentType": "text/markdown"}}}
            resp = httpx.post(f"{self.server}/message", json=payload, headers={"X-Gotify-Key": self.token}, timeout=10)
            if 200 <= resp.status_code < 300:
                logger.info("Gotify推送成功: {}", title)
                return True
            logger.error("Gotify推送失败: {} {}", resp.status_code, resp.text[:200])
            return False
        except Exception as e:
            logger.error("Gotify推送异常: {}", e)
            return False
