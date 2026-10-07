"""
飞书自定义机器人推送（消息卡片 + markdown）
"""

import base64
import hashlib
import hmac
import time
import json

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
        self.app_id = fs_cfg.get("app_id", "")
        self.app_secret = fs_cfg.get("app_secret", "")
        self.chat_id = fs_cfg.get("chat_id", "")
        self.domain = "https://open.larksuite.com" if fs_cfg.get("domain") == "lark" else "https://open.feishu.cn"
        self._token = ""
        self._expires = 0

    def _headers(self) -> dict:
        if not self._token or time.monotonic() >= self._expires:
            data = httpx.post(self.domain + "/open-apis/auth/v3/tenant_access_token/internal",
                json={"app_id": self.app_id, "app_secret": self.app_secret}, timeout=10).json()
            if data.get("code") != 0 or not data.get("tenant_access_token"):
                raise RuntimeError("飞书应用认证失败")
            self._token = data["tenant_access_token"]
            self._expires = time.monotonic() + max(1, int(data.get("expire", 7200)) - 60)
        return {"Authorization": "Bearer " + self._token}

    def _app_message(self, kind: str, content: dict) -> bool:
        response = httpx.post(self.domain + "/open-apis/im/v1/messages", params={"receive_id_type": "chat_id"},
            headers=self._headers(), json={"receive_id": self.chat_id, "msg_type": kind,
            "content": json.dumps(content, ensure_ascii=False)}, timeout=15)
        return response.status_code == 200 and response.json().get("code") == 0

    def send_image(self, title: str, image: bytes) -> bool:
        if not self.enabled or not all((self.app_id, self.app_secret, self.chat_id)):
            return False
        try:
            result = httpx.post(self.domain + "/open-apis/im/v1/images", headers=self._headers(),
                data={"image_type": "message"}, files={"image": ("report.png", image, "image/png")}, timeout=30).json()
            return result.get("code") == 0 and self._app_message("image", {"image_key": result["data"]["image_key"]})
        except Exception:
            logger.warning("飞书应用图片推送失败，请检查权限及群聊成员关系")
            return False

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

        if not self.webhook_url and not all((self.app_id, self.app_secret, self.chat_id)):
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
            if all((self.app_id, self.app_secret, self.chat_id)):
                return self._app_message("interactive", payload["card"])
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
