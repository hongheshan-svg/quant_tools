"""
ntfy 推送（JSON 发布，标题不进请求头）
"""

import httpx
from loguru import logger

from src.notifier.base import send_paged

# 默认消息上限 4096 字节
MAX_CONTENT_BYTES = 3800


class NtfyNotifier:
    """ntfy 推送（JSON 发布，标题不进请求头）"""

    def __init__(self, config: dict):
        cfg = (config.get("notifier") or {}).get("ntfy") or {}
        self.enabled = cfg.get("enabled", False)
        self.server = str(cfg.get("server") or "https://ntfy.sh").strip().rstrip("/")
        self.topic = str(cfg.get("topic") or "").strip()
        self.token = str(cfg.get("token") or "").strip()

    def send(self, title: str, content: str) -> bool:
        """发送消息，超长内容自动拆成多条；未启用、配置不完整或失败返回 False，不抛异常。"""
        if not self.enabled:
            logger.debug("ntfy推送未启用")
            return False
        if not self.topic:
            logger.warning("ntfy配置不完整")
            return False
        return send_paged(title, content, MAX_CONTENT_BYTES, self._send_one)

    def send_image(self, title: str, png: bytes) -> bool:
        """POST 二进制图片到 {server}/{topic}，标题走查询参数（避免请求头非 ASCII）；失败返回 False，不抛异常。"""
        if not self.enabled or not self.topic:
            return False
        try:
            headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
            headers.update({"Content-Type": "image/png", "Filename": "report.png"})
            resp = httpx.post(f"{self.server}/{self.topic}", content=png, params={"title": title},
                              headers=headers, timeout=30)
            if 200 <= resp.status_code < 300:
                logger.info("ntfy图片推送成功: {}", title)
                return True
            logger.error("ntfy图片推送失败: {} {}", resp.status_code, resp.text[:200])
            return False
        except Exception as e:
            logger.error("ntfy图片推送异常: {}", e)
            return False

    def _send_one(self, title: str, content: str) -> bool:
        try:
            headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
            payload = {"topic": self.topic, "title": title, "message": content, "markdown": True}
            resp = httpx.post(self.server, json=payload, headers=headers, timeout=10)
            if 200 <= resp.status_code < 300:
                logger.info("ntfy推送成功: {}", title)
                return True
            logger.error("ntfy推送失败: {} {}", resp.status_code, resp.text[:200])
            return False
        except Exception as e:
            logger.error("ntfy推送异常: {}", e)
            return False
