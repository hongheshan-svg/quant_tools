"""
Discord 机器人（Gateway 长连接）：通过 WebSocket 收消息，不需要公网 IP 和回调地址。

Discord 开发者后台创建 Application → Bot，复制 Bot Token 填进 bot.discord.token，
并在 Bot 页开启 Message Content Intent，再用 OAuth2 链接把机器人邀请进服务器。
私信总是回复；服务器频道里默认（guild_mode=mention）只回复 @机器人 或以 / 开头的消息，all 则回复所有消息。
"""

from __future__ import annotations

import asyncio
import re
import threading
import time
from typing import Any

from loguru import logger

from src.bot.dispatcher import Dispatcher, retry_delay
from src.bot.models import BotMessage

MAX_CHARS = 2000  # Discord 单条消息上限，按字符计


def parse_message(payload: dict[str, Any], bot_user_id: str, guild_mode: str = "mention",
                  allowed_channels: list[str] | None = None) -> BotMessage | None:
    """Discord 消息 payload → BotMessage；忽略机器人消息、不在允许频道的消息，服务器里按 guild_mode 过滤"""
    author = payload.get("author") or {}
    if author.get("bot"):
        return None
    channel_id = str(payload.get("channel_id") or "")
    allowed = [str(c) for c in (allowed_channels or []) if str(c).strip()]
    if allowed and channel_id not in allowed:
        return None
    content = str(payload.get("content") or "")
    is_group = bool(payload.get("guild_id"))
    bot_id = str(bot_user_id or "")
    if is_group and guild_mode != "all":
        mentioned = bool(bot_id) and bot_id in [str(m) for m in (payload.get("mentions") or [])]
        if not (mentioned or content.lstrip().startswith("/")):
            return None
    text = re.sub(r"<@!?\d+>", "", content).strip()
    if not text:
        return None
    user_id = str(author.get("id") or "")
    return BotMessage(platform="discord", chat_id=channel_id, user_id=user_id,
                      user_name=str(author.get("name") or user_id), text=text,
                      is_group=is_group, message_id=str(payload.get("id") or ""))


def split_message(text: str, limit: int = MAX_CHARS) -> list[str]:
    """按行拆成每段不超过 limit 字符；单行超长时硬切"""
    chunks: list[str] = []
    current = ""
    for line in text.split("\n"):
        while len(line) > limit:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(line[:limit])
            line = line[limit:]
        if not current:
            current = line
        elif len(current) + 1 + len(line) <= limit:
            current += "\n" + line
        else:
            chunks.append(current)
            current = line
    if current:
        chunks.append(current)
    chunks = [c for c in chunks if c.strip()]
    return chunks or [text[:limit]]


class DiscordBot:
    def __init__(self, token: str, dispatcher: Dispatcher, guild_mode: str = "mention",
                 allowed_channels: list[str] | None = None) -> None:
        self.token = token
        self.dispatcher = dispatcher
        self.guild_mode = guild_mode if guild_mode in ("mention", "all") else "mention"
        self.allowed_channels = [str(c) for c in (allowed_channels or [])]
        self.thread: threading.Thread | None = None
        self.loop: asyncio.AbstractEventLoop | None = None

    def _payload(self, message) -> dict[str, Any]:
        return {
            "id": str(message.id),
            "content": message.content or "",
            "author": {"id": str(message.author.id), "name": getattr(message.author, "name", ""),
                       "bot": bool(getattr(message.author, "bot", False))},
            "channel_id": str(message.channel.id),
            "guild_id": str(message.guild.id) if message.guild else None,
            "mentions": [str(u.id) for u in message.mentions],
        }

    def _build_client(self, discord):
        intents = discord.Intents.default()
        intents.messages = True
        intents.message_content = True
        intents.dm_messages = True
        intents.guilds = True
        client = discord.Client(intents=intents)
        bot = self

        @client.event
        async def on_ready():
            logger.info(f"[bot] Discord 已登录: {client.user}")

        @client.event
        async def on_message(message):
            if client.user is None or message.author.id == client.user.id:
                return
            parsed = parse_message(bot._payload(message), str(client.user.id), bot.guild_mode, bot.allowed_channels)
            if parsed is None:
                return
            loop = asyncio.get_running_loop()
            channel = message.channel

            def reply(text: str) -> None:
                # Dispatcher 在线程池里调用，切回事件循环发送
                for chunk in split_message(text):
                    asyncio.run_coroutine_threadsafe(channel.send(chunk), loop).result(timeout=30)

            bot.dispatcher.submit(parsed, reply)

        return client

    def start(self) -> bool:
        """在独立线程和事件循环里运行；discord.py 没安装时返回 False"""
        try:
            import discord
        except ImportError as e:
            logger.warning(f"[bot] discord.py 没有安装（pip install -r requirements.txt）: {e}")
            return False

        def run() -> None:
            failures = 0
            while True:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                self.loop = loop
                started = time.monotonic()
                try:
                    client = self._build_client(discord)
                    logger.info("[bot] Discord 长连接启动，等待消息")
                    loop.run_until_complete(client.start(self.token))
                except discord.LoginFailure as e:
                    failures = failures + 1
                    delay = retry_delay(failures)
                    logger.error(f"[bot] Discord 登录失败，{delay:.0f} 秒后重试（检查 bot.discord.token）: {e}")
                    time.sleep(delay)
                except Exception as e:
                    failures = 1 if time.monotonic() - started > 300 else failures + 1
                    delay = retry_delay(failures)
                    logger.error(f"[bot] Discord 长连接失败，{delay:.0f} 秒后重试: {e}")
                    time.sleep(delay)
                finally:
                    try:
                        loop.close()
                    except Exception:
                        pass

        self.thread = threading.Thread(target=run, name="bot-discord", daemon=True)
        self.thread.start()
        return True
