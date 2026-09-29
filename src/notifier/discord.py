"""
Discord Webhook 推送（markdown）
"""

import httpx
from loguru import logger

from src.notifier.base import send_paged

# 单条上限 2000 字符，按字节取保守值（中文每字 3 字节）
MAX_CONTENT_BYTES = 1800


class DiscordNotifier:
    """Discord Webhook 推送（markdown）"""

    def __init__(self, config: dict):
        cfg = (config.get("notifier") or {}).get("discord") or {}
        self.enabled = cfg.get("enabled", False)
        self.webhook_url = str(cfg.get("webhook_url") or "").strip()

    def send(self, title: str, content: str) -> bool:
        """发送消息，超长内容自动拆成多条；未启用、配置不完整或失败返回 False，不抛异常。"""
        if not self.enabled:
            logger.debug("Discord推送未启用")
            return False
        if not self.webhook_url:
            logger.warning("Discord配置不完整")
            return False
        return send_paged(title, content, MAX_CONTENT_BYTES, self._send_one)

    def send_image(self, title: str, png: bytes) -> bool:
        """Webhook 上传图片文件（multipart，content 为标题）；失败返回 False，不抛异常。"""
        if not self.enabled or not self.webhook_url:
            return False
        try:
            resp = httpx.post(self.webhook_url, data={"content": f"**{title}**"},
                              files={"file": ("report.png", png, "image/png")}, timeout=30)
            if 200 <= resp.status_code < 300:
                logger.info("Discord图片推送成功: {}", title)
                return True
            logger.error("Discord图片推送失败: {} {}", resp.status_code, resp.text[:200])
            return False
        except Exception as e:
            logger.error("Discord图片推送异常: {}", e)
            return False

    def _send_one(self, title: str, content: str) -> bool:
        try:
            resp = httpx.post(self.webhook_url, json={"content": f"**{title}**\n\n{content}"}, timeout=10)
            if 200 <= resp.status_code < 300:
                logger.info("Discord推送成功: {}", title)
                return True
            logger.error("Discord推送失败: {} {}", resp.status_code, resp.text[:200])
            return False
        except Exception as e:
            logger.error("Discord推送异常: {}", e)
            return False
