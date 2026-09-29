"""
Server酱推送（Turbo 版和 Server酱³）
"""

import re

import httpx
from loguru import logger

from src.notifier.base import send_paged

# desp 上限约 3 万字节
MAX_CONTENT_BYTES = 20000


class ServerChanNotifier:
    """Server酱推送（Turbo 版和 Server酱³）"""

    def __init__(self, config: dict):
        cfg = (config.get("notifier") or {}).get("serverchan") or {}
        self.enabled = cfg.get("enabled", False)
        self.sendkey = str(cfg.get("sendkey") or "").strip()

    def send(self, title: str, content: str) -> bool:
        """发送消息，超长内容自动拆成多条；未启用、配置不完整或失败返回 False，不抛异常。"""
        if not self.enabled:
            logger.debug("Server酱推送未启用")
            return False
        if not self.sendkey:
            logger.warning("Server酱配置不完整")
            return False
        return send_paged(title, content, MAX_CONTENT_BYTES, self._send_one)

    def _send_one(self, title: str, content: str) -> bool:
        try:
            resp = httpx.post(self._url(), data={"title": title, "desp": content}, timeout=10)
            if resp.json().get("code") == 0:
                logger.info("Server酱推送成功: {}", title)
                return True
            logger.error("Server酱推送失败: {} {}", resp.status_code, resp.text[:200])
            return False
        except Exception as e:
            logger.error("Server酱推送异常: {}", e)
            return False

    def _url(self) -> str:
        """Server酱³ 的 key 形如 sctp{数字}t...，域名里带这个数字；其余走 Turbo 版。"""
        m = re.match(r"sctp(\d+)t", self.sendkey)
        if m:
            return f"https://{m.group(1)}.push.ft07.com/send/{self.sendkey}.send"
        return f"https://sctapi.ftqq.com/{self.sendkey}.send"
