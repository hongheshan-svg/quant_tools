"""
PushPlus 推送（markdown 模板）
"""

import httpx
from loguru import logger

from src.notifier.base import send_paged

# content 上限约 2 万字符
MAX_CONTENT_BYTES = 18000


class PushPlusNotifier:
    """PushPlus 推送（markdown 模板）"""

    def __init__(self, config: dict):
        cfg = (config.get("notifier") or {}).get("pushplus") or {}
        self.enabled = cfg.get("enabled", False)
        self.token = str(cfg.get("token") or "").strip()
        self.topic = str(cfg.get("topic") or "").strip()

    def send(self, title: str, content: str) -> bool:
        """发送消息，超长内容自动拆成多条；未启用、配置不完整或失败返回 False，不抛异常。"""
        if not self.enabled:
            logger.debug("PushPlus推送未启用")
            return False
        if not self.token:
            logger.warning("PushPlus配置不完整")
            return False
        return send_paged(title, content, MAX_CONTENT_BYTES, self._send_one)

    def _send_one(self, title: str, content: str) -> bool:
        try:
            payload = {"token": self.token, "title": title, "content": content, "template": "markdown"}
            if self.topic:
                payload["topic"] = self.topic
            resp = httpx.post("https://www.pushplus.plus/send", json=payload, timeout=10)
            if resp.json().get("code") == 200:
                logger.info("PushPlus推送成功: {}", title)
                return True
            logger.error("PushPlus推送失败: {} {}", resp.status_code, resp.text[:200])
            return False
        except Exception as e:
            logger.error("PushPlus推送异常: {}", e)
            return False
