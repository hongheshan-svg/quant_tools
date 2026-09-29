"""
钉钉机器人（Stream 模式）：通过 WebSocket 长连接收消息，不需要公网 IP 和回调地址。

钉钉开放平台创建企业内部应用 → 添加机器人能力，消息接收模式选「Stream 模式」，
把应用的 Client ID（AppKey）和 Client Secret 填进 bot.dingtalk。单聊直接发，群聊需要 @机器人。
"""

from __future__ import annotations

import asyncio
import re
import threading
import time
from typing import Any

from loguru import logger

from src.bot.dispatcher import Dispatcher, reply_title, retry_delay, split_reply
from src.bot.models import BotMessage

MAX_BYTES = 18000


def strip_at(text: str) -> str:
    return re.sub(r"^(@\S+\s*)+", "", text.strip()).strip()


def parse_message(data: dict[str, Any]) -> BotMessage | None:
    """钉钉 Stream 回调数据 → BotMessage；只处理文字消息"""
    text = ((data.get("text") or {}).get("content") or "").strip()
    if data.get("msgtype") != "text" or not text:
        return None
    return BotMessage(
        platform="dingtalk",
        chat_id=str(data.get("conversationId") or ""),
        user_id=str(data.get("senderStaffId") or data.get("senderId") or ""),
        user_name=str(data.get("senderNick") or ""),
        text=strip_at(text),
        is_group=str(data.get("conversationType")) == "2",
        message_id=str(data.get("msgId") or ""),
    )


class DingTalkBot:
    def __init__(self, client_id: str, client_secret: str, dispatcher: Dispatcher) -> None:
        import dingtalk_stream  # 没装 SDK 时抛 ImportError，由调用方提示

        self._sdk = dingtalk_stream
        self.client_id = client_id
        self.client_secret = client_secret
        self.dispatcher = dispatcher
        self.thread: threading.Thread | None = None

    def _handler(self):
        sdk = self._sdk
        dispatcher = self.dispatcher

        class Handler(sdk.ChatbotHandler):
            async def process(self, callback):
                data = callback.data or {}
                message = parse_message(data)
                if message is None:
                    return sdk.AckMessage.STATUS_OK, "ignored"
                incoming = sdk.ChatbotMessage.from_dict(data)

                def reply(text: str) -> None:
                    for chunk in split_reply(text, MAX_BYTES):
                        self.reply_markdown(reply_title(chunk), chunk, incoming)

                dispatcher.submit(message, reply)  # 先确认收到，处理完再通过会话 Webhook 回复
                return sdk.AckMessage.STATUS_OK, "OK"

        return Handler()

    def _client(self):
        class Client(self._sdk.DingTalkStreamClient):
            """SDK 连接失败后固定 10 秒重试；这里改成逐次加长，凭证填错时不刷屏"""
            failures = 0

            def open_connection(self):
                if self.failures:
                    time.sleep(max(0.0, retry_delay(self.failures) - 10))  # SDK 自己还会再等 10 秒
                connection = super().open_connection()
                self.failures = 0 if connection else self.failures + 1
                if self.failures == 1:
                    logger.error("[bot] 钉钉 Stream 连接失败，请检查 bot.dingtalk 的 client_id / client_secret 和网络")
                return connection

        client = Client(self._sdk.Credential(self.client_id, self.client_secret))
        client.register_callback_handler(self._sdk.ChatbotMessage.TOPIC, self._handler())
        return client

    def start(self) -> None:
        def run() -> None:
            client = self._client()
            logger.info("[bot] 钉钉 Stream 启动，等待消息")
            while True:  # client.start() 内部断线重连，一般不会返回
                try:
                    asyncio.run(client.start())
                except Exception as e:
                    logger.error(f"[bot] 钉钉 Stream 异常退出，稍后重连: {e}")
                time.sleep(10)

        self.thread = threading.Thread(target=run, name="bot-dingtalk", daemon=True)
        self.thread.start()
