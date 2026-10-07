"""AstrBot 适配器：兼容时间戳与 HMAC 请求体签名。"""
import hashlib
import hmac
import html
import json
import time
import httpx


class AstrBotNotifier:
    def __init__(self, config: dict):
        self.cfg = (config.get("notifier") or {}).get("astrbot") or {}

    def send(self, title: str, content: str) -> bool:
        if not self.cfg.get("enabled") or not self.cfg.get("url"):
            return False
        payload = {"content": "<article><h1>" + html.escape(title) + "</h1><pre>" + html.escape(content) + "</pre></article>"}
        timestamp = str(int(time.time()))
        canonical = json.dumps(payload, sort_keys=True)
        token = str(self.cfg.get("token") or "")
        signature = hmac.new(token.encode(), f"{timestamp}.{canonical}".encode(), hashlib.sha256).hexdigest() if token else ""
        try:
            response = httpx.post(self.cfg["url"], json=payload,
                headers={"X-Signature": signature, "X-Timestamp": timestamp}, timeout=15)
            return response.status_code == 200
        except Exception:
            return False
