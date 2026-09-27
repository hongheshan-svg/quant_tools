"""
飞书自定义机器人推送（消息卡片 + markdown）
"""

import base64
import hashlib
import hmac
import time

import httpx
from loguru import logger

from src.notifier.base import paged_titles, split_by_bytes

# 飞书卡片请求体上限约 30KB，预留卡片结构
MAX_CONTENT_BYTES = 20000


class FeishuNotifier:
    """飞书自定义机器人推送"""

    def __init__(self, config: dict):
        fs_cfg = config.get("notifier", {}).get("feishu", {})
        self.enabled = fs_cfg.get("enabled", False)
        self.webhook_url = fs_cfg.get("webhook_url", "")
        self.secret = fs_cfg.get("secret", "")

    def _security_fields(self) -> dict:
        """签名校验：以「时间戳\\n密钥」为 HMAC 密钥对空消息签名（飞书规则）。"""
        if not self.secret:
            return {}
        timestamp = str(int(time.time()))
        string_to_sign = f"{timestamp}\n{self.secret}"
        sign = base64.b64encode(hmac.new(string_to_sign.encode("utf-8"), digestmod=hashlib.sha256).digest()).decode("utf-8")
        return {"timestamp": timestamp, "sign": sign}

    def send(self, title: str, content: str) -> bool:
        """发送 markdown 卡片，超长内容自动拆成多条；返回是否全部发送成功。"""
        if not self.enabled:
            logger.debug("飞书推送未启用")
            return False

        if not self.webhook_url:
            logger.warning("飞书 webhook URL 未配置")
            return False

        chunks = split_by_bytes(content, MAX_CONTENT_BYTES)
        # 逐条发送，某条失败不影响后续分段
        results = [self._send_one(t, c) for t, c in zip(paged_titles(title, len(chunks)), chunks)]
        return all(results)

    def _send_one(self, title: str, content: str) -> bool:
        payload = {
            **self._security_fields(),
            "msg_type": "interactive",
            "card": {
                "header": {"title": {"tag": "plain_text", "content": title}},
                "elements": [{"tag": "markdown", "content": content}],
            },
        }

        try:
            resp = httpx.post(self.webhook_url, json=payload, timeout=10)
            data = resp.json()
            if data.get("code", data.get("StatusCode")) == 0:
                logger.info(f"飞书推送成功: {title}")
                return True
            logger.error(f"飞书推送失败: {data}")
            return False
        except Exception as e:
            logger.error(f"飞书推送异常: {e}")
            return False
