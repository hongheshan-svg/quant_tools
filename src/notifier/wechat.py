"""
企业微信机器人推送
"""

import httpx
from loguru import logger

from src.notifier.base import paged_titles, split_by_bytes

# 企业微信 markdown 单条上限 4096 字节，预留标题和页码
MAX_CONTENT_BYTES = 3800


class WeChatNotifier:
    """企业微信机器人推送"""

    def __init__(self, config: dict):
        wechat_cfg = config.get("notifier", {}).get("wechat", {})
        self.enabled = wechat_cfg.get("enabled", False)
        self.webhook_url = wechat_cfg.get("webhook_url", "")

    def send(self, title: str, content: str) -> bool:
        """
        发送消息，超长内容自动拆成多条

        Args:
            title: 消息标题
            content: 消息内容（支持 Markdown）

        Returns:
            是否全部发送成功
        """
        if not self.enabled:
            logger.debug("企业微信推送未启用")
            return False

        if not self.webhook_url:
            logger.warning("企业微信 webhook URL 未配置")
            return False

        chunks = split_by_bytes(content, MAX_CONTENT_BYTES)
        # 逐条发送，某条失败不影响后续分段
        results = [self._send_one(t, c) for t, c in zip(paged_titles(title, len(chunks)), chunks)]
        return all(results)

    def _send_one(self, title: str, content: str) -> bool:
        payload = {
            "msgtype": "markdown",
            "markdown": {
                "content": f"## {title}\n\n{content}",
            },
        }

        try:
            resp = httpx.post(self.webhook_url, json=payload, timeout=10)
            data = resp.json()
            if data.get("errcode") == 0:
                logger.info(f"企业微信推送成功: {title}")
                return True
            logger.error(f"企业微信推送失败: {data}")
            return False
        except Exception as e:
            logger.error(f"企业微信推送异常: {e}")
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
