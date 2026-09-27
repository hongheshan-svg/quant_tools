"""
钉钉机器人推送
"""

import base64
import hashlib
import hmac
import time
import urllib.parse

import httpx
from loguru import logger

from src.notifier.base import paged_titles, split_by_bytes

# 钉钉 markdown 单条上限约 20000 字节
MAX_CONTENT_BYTES = 18000


class DingTalkNotifier:
    """钉钉机器人推送"""

    def __init__(self, config: dict):
        dt_cfg = config.get("notifier", {}).get("dingtalk", {})
        self.enabled = dt_cfg.get("enabled", False)
        self.webhook_url = dt_cfg.get("webhook_url", "")
        self.secret = dt_cfg.get("secret", "")

    def _get_signed_url(self) -> str:
        """生成签名后的 URL"""
        if not self.secret:
            return self.webhook_url

        timestamp = str(round(time.time() * 1000))
        string_to_sign = f"{timestamp}\n{self.secret}"
        hmac_code = hmac.new(
            self.secret.encode("utf-8"),
            string_to_sign.encode("utf-8"),
            digestmod=hashlib.sha256,
        ).digest()
        sign = urllib.parse.quote_plus(base64.b64encode(hmac_code))
        return f"{self.webhook_url}&timestamp={timestamp}&sign={sign}"

    def send(self, title: str, content: str) -> bool:
        """
        发送 Markdown 消息，超长内容自动拆成多条

        Args:
            title: 消息标题
            content: 消息内容（Markdown 格式）

        Returns:
            是否全部发送成功
        """
        if not self.enabled:
            logger.debug("钉钉推送未启用")
            return False

        if not self.webhook_url:
            logger.warning("钉钉 webhook URL 未配置")
            return False

        chunks = split_by_bytes(content, MAX_CONTENT_BYTES)
        # 逐条发送，某条失败不影响后续分段
        results = [self._send_one(t, c) for t, c in zip(paged_titles(title, len(chunks)), chunks)]
        return all(results)

    def _send_one(self, title: str, content: str) -> bool:
        url = self._get_signed_url()
        payload = {
            "msgtype": "markdown",
            "markdown": {
                "title": title,
                "text": f"## {title}\n\n{content}",
            },
        }

        try:
            resp = httpx.post(url, json=payload, timeout=10)
            data = resp.json()
            if data.get("errcode") == 0:
                logger.info(f"钉钉推送成功: {title}")
                return True
            logger.error(f"钉钉推送失败: {data}")
            return False
        except Exception as e:
            logger.error(f"钉钉推送异常: {e}")
            return False

    def send_stock_signals(self, signals: list[dict]):
        """发送选股信号推送"""
        if not signals:
            return

        lines = ["### 今日 Top 选股\n"]
        for s in signals:
            rank = s.get("rank", "")
            name = s.get("name", "")
            code = s.get("code", "")
            score = s.get("composite_score", 0)
            rec = s.get("recommendation", "")
            rec_text = {"strong_buy": "强烈推荐", "buy": "推荐", "hold": "观望", "avoid": "回避"}.get(rec, rec)
            lines.append(f"**#{rank}** {name}({code}) 综合评分: **{score:.1f}** `{rec_text}`")

        content = "\n".join(lines)
        self.send("A股量化选股信号", content)
