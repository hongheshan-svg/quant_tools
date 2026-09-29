"""把平台收到的消息放进线程池处理：平台回调要尽快返回（否则会重发），诊断和问股要几十秒"""

from __future__ import annotations

import re
import threading
from collections import OrderedDict
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from loguru import logger

from src.bot.models import BotMessage
from src.notifier.base import split_by_bytes

SEEN_LIMIT = 1000
MAX_RETRY_SECONDS = 600


def retry_delay(failures: int, base: float = 10) -> float:
    """连接失败后的等待秒数：10、20、40…最多 10 分钟（凭证填错时不刷屏）"""
    return min(MAX_RETRY_SECONDS, base * 2 ** max(0, failures - 1))


class Dispatcher:
    def __init__(self, router, workers: int = 2) -> None:
        self.router = router
        self.executor = ThreadPoolExecutor(max_workers=max(1, workers), thread_name_prefix="bot")
        self._seen: OrderedDict[str, None] = OrderedDict()
        self._lock = threading.Lock()

    def _duplicate(self, key: str) -> bool:
        with self._lock:
            if key in self._seen:
                return True
            self._seen[key] = None
            while len(self._seen) > SEEN_LIMIT:
                self._seen.popitem(last=False)
            return False

    def submit(self, message: BotMessage, reply: Callable[[str], None]):
        """排队处理；同一条消息（平台重发）只处理一次。返回 Future，重复消息返回 None"""
        if message.message_id and self._duplicate(f"{message.platform}:{message.message_id}"):
            logger.debug(f"[bot] 忽略重复消息 {message.message_id}")
            return None
        logger.info(f"[bot] {message.platform} {message.user_name or message.user_id}: {message.text[:80]}")
        return self.executor.submit(self._run, message, reply)

    def _run(self, message: BotMessage, reply: Callable[[str], None]) -> None:
        try:
            answer = self.router.handle(message, progress=reply)
        except Exception as e:  # router 已经兜底，这里防止回复失败时吞掉线程
            logger.exception(f"[bot] 处理消息失败: {e}")
            answer = f"处理失败：{e}"
        try:
            reply(answer)
        except Exception as e:
            logger.error(f"[bot] 回复失败: {e}")


def split_reply(text: str, max_bytes: int) -> list[str]:
    return [chunk for chunk in split_by_bytes(text, max_bytes) if chunk.strip()] or [text]


def reply_title(text: str, limit: int = 20) -> str:
    """钉钉 Markdown 消息的标题（通知栏里显示）：取第一行非空文字"""
    for line in text.splitlines():
        line = re.sub(r"[#*`>\-\s]+", " ", line).strip()
        if line:
            return line[:limit]
    return "A股量化"
