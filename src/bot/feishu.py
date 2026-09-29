"""
飞书机器人（长连接模式）：通过 WebSocket 收消息，不需要公网 IP 和回调地址。

飞书开放平台创建企业自建应用 → 添加机器人能力 → 事件订阅选「使用长连接接收事件」并订阅
「接收消息 im.message.receive_v1」，开通「获取与发送单聊、群组消息」权限，把 App ID 和 App Secret 填进 bot.feishu。
单聊直接发，群聊需要 @机器人。海外版 Lark 把 bot.feishu.domain 设为 lark。
"""

from __future__ import annotations

import asyncio
import json
import re
import threading
import time
from typing import Any

from loguru import logger

from src.bot.dispatcher import Dispatcher, retry_delay, split_reply
from src.bot.models import BotMessage

MAX_BYTES = 20000


def parse_message(message: Any, sender: Any = None) -> BotMessage | None:
    """飞书 im.message.receive_v1 事件里的 message / sender → BotMessage；只处理文字，群里只处理 @机器人 的消息"""
    if message is None or getattr(message, "message_type", "") != "text":
        return None
    try:
        text = str(json.loads(message.content or "{}").get("text") or "")
    except (TypeError, ValueError):
        return None
    mentions = getattr(message, "mentions", None) or []
    for mention in mentions:
        if getattr(mention, "key", None):
            text = text.replace(mention.key, "")
    text = re.sub(r"@_user_\d+", "", text).strip()
    is_group = getattr(message, "chat_type", "") == "group"
    if not text or (is_group and not mentions):
        return None
    sender_id = getattr(sender, "sender_id", None)
    user_id = str(getattr(sender_id, "open_id", "") or getattr(sender_id, "user_id", "") or "")
    return BotMessage(platform="feishu", chat_id=str(message.chat_id or ""), user_id=user_id, user_name=user_id,
                      text=text, is_group=is_group, message_id=str(message.message_id or ""))


def to_card(markdown: str) -> str:
    """飞书卡片的 markdown 组件不支持 # 标题，改成加粗"""
    lines = [re.sub(r"^#{1,6}\s+(.*)$", r"**\1**", line) for line in markdown.splitlines()]
    return json.dumps({"config": {"wide_screen_mode": True},
                       "elements": [{"tag": "markdown", "content": "\n".join(lines)}]}, ensure_ascii=False)


class FeishuBot:
    def __init__(self, app_id: str, app_secret: str, dispatcher: Dispatcher, domain: str = "feishu") -> None:
        import lark_oapi  # 没装 SDK 时抛 ImportError，由调用方提示

        self._lark = lark_oapi
        self.app_id = app_id
        self.app_secret = app_secret
        self.domain = lark_oapi.LARK_DOMAIN if domain == "lark" else lark_oapi.FEISHU_DOMAIN
        self.dispatcher = dispatcher
        self.client = (lark_oapi.Client.builder().app_id(app_id).app_secret(app_secret)
                       .domain(self.domain).log_level(lark_oapi.LogLevel.WARNING).build())
        self.thread: threading.Thread | None = None

    def reply(self, message_id: str, text: str) -> None:
        from lark_oapi.api.im.v1 import ReplyMessageRequest, ReplyMessageRequestBody

        for chunk in split_reply(text, MAX_BYTES):
            request = (ReplyMessageRequest.builder().message_id(message_id)
                       .request_body(ReplyMessageRequestBody.builder().content(to_card(chunk)).msg_type("interactive").build())
                       .build())
            response = self.client.im.v1.message.reply(request)
            if not response.success():
                logger.error(f"[bot] 飞书回复失败: code={response.code} msg={response.msg}")

    def _on_message(self, data) -> None:
        event = getattr(data, "event", None)
        message = parse_message(getattr(event, "message", None), getattr(event, "sender", None))
        if message is not None:
            self.dispatcher.submit(message, lambda text: self.reply(message.message_id, text))

    def start(self) -> None:
        def run() -> None:
            # lark 的长连接客户端用模块级事件循环；在服务里导入时它会拿到 uvicorn 正在运行的循环，这里换成本线程自己的
            import lark_oapi.ws.client as ws_client

            ws_client.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(ws_client.loop)
            handler = (self._lark.EventDispatcherHandler.builder("", "")
                       .register_p2_im_message_receive_v1(self._on_message).build())
            failures = 0
            while True:
                # 连上之后 SDK 自己断线重连；首次连接失败（凭证错误、网络不通）会抛异常，这里逐次加长间隔重试
                client = ws_client.Client(self.app_id, self.app_secret, event_handler=handler, domain=self.domain,
                                          log_level=self._lark.LogLevel.WARNING, auto_reconnect=True)
                started = time.monotonic()
                try:
                    logger.info("[bot] 飞书长连接启动，等待消息")
                    client.start()
                except Exception as e:
                    failures = 1 if time.monotonic() - started > 300 else failures + 1
                    delay = retry_delay(failures)
                    logger.error(f"[bot] 飞书长连接失败，{delay:.0f} 秒后重试（检查 bot.feishu 的 app_id / app_secret）: {e}")
                    time.sleep(delay)

        self.thread = threading.Thread(target=run, name="bot-feishu", daemon=True)
        self.thread.start()
