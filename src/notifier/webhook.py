"""
自定义 Webhook 推送
body_template 支持占位符 $title、$content（原文）和 $title_json、$content_json（json.dumps 后带引号的字符串）；
为空时发送 JSON {"title": ..., "content": ...}。
"""

import json
from string import Template

import httpx
from loguru import logger

from src.notifier.base import send_paged

# 目标接口未知，取保守值
MAX_CONTENT_BYTES = 20000


class WebhookNotifier:
    """自定义 Webhook 推送"""

    def __init__(self, config: dict):
        cfg = (config.get("notifier") or {}).get("webhook") or {}
        self.enabled = cfg.get("enabled", False)
        self.url = str(cfg.get("url") or "").strip()
        self.method = str(cfg.get("method") or "POST").strip().upper()
        self.headers = self._parse_headers(cfg.get("headers"))
        self.body_template = str(cfg.get("body_template") or "")

    @staticmethod
    def _parse_headers(raw) -> dict:
        if isinstance(raw, dict):
            return {str(k): str(v) for k, v in raw.items()}
        if isinstance(raw, str) and raw.strip():
            try:
                data = json.loads(raw)
            except ValueError:
                logger.warning("自定义 Webhook 的 headers 不是合法 JSON，已忽略")
                return {}
            if isinstance(data, dict):
                return {str(k): str(v) for k, v in data.items()}
        return {}

    def send(self, title: str, content: str) -> bool:
        """发送消息，超长内容自动拆成多条；未启用、配置不完整或失败返回 False，不抛异常。"""
        if not self.enabled:
            logger.debug("自定义 Webhook 推送未启用")
            return False
        if not self.url:
            logger.warning("自定义 Webhook 配置不完整")
            return False
        return send_paged(title, content, MAX_CONTENT_BYTES, self._send_one)

    def _request(self, **kwargs):
        if self.method == "POST":
            return httpx.post(self.url, timeout=10, **kwargs)
        return httpx.request(self.method, self.url, timeout=10, **kwargs)

    def _send_one(self, title: str, content: str) -> bool:
        try:
            headers = dict(self.headers)
            if self.body_template:
                body = Template(self.body_template).safe_substitute(
                    title=title, content=content, title_json=json.dumps(title, ensure_ascii=False),
                    content_json=json.dumps(content, ensure_ascii=False))
                if not any(k.lower() == "content-type" for k in headers):
                    headers["Content-Type"] = "application/json"
                resp = self._request(content=body.encode("utf-8"), headers=headers)
            else:
                resp = self._request(json={"title": title, "content": content}, headers=headers)
            if 200 <= resp.status_code < 300:
                logger.info("自定义 Webhook 推送成功: {}", title)
                return True
            logger.error("自定义 Webhook 推送失败: {} {}", resp.status_code, resp.text[:200])
            return False
        except Exception as e:
            logger.error("自定义 Webhook 推送异常: {}", e)
            return False
