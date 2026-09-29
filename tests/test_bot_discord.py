"""Discord 机器人：消息解析、拆分、启动降级、manager 接入与设置接口"""
import builtins
import sys

import pytest

from src.bot import manager
from src.bot.models import BotMessage
from tests.test_api import env, settings_store  # noqa: F401  复用 API 测试夹具

from src.bot import discord  # noqa: E402

BOT = "123"


def _payload(content="大盘", guild_id="g1", channel_id="c1", mentions=None, bot=False):
    return {"id": "m1", "content": content, "author": {"id": "u1", "name": "张三", "bot": bot},
            "channel_id": channel_id, "guild_id": guild_id, "mentions": mentions or []}


def test_ignores_bots():
    assert discord.parse_message(_payload(bot=True, guild_id=None), BOT) is None


def test_dm_always_parsed():
    m = discord.parse_message(_payload("大盘", guild_id=None), BOT)
    assert isinstance(m, BotMessage)
    assert (m.platform, m.text, m.user_id, m.is_group) == ("discord", "大盘", "u1", False)
    assert m.session_key.startswith("discord:") and m.session_key.endswith(":u1")


def test_guild_mention_mode():
    assert discord.parse_message(_payload("大家好"), BOT) is None
    m = discord.parse_message(_payload("<@123> 诊断 茅台", mentions=["123"]), BOT)
    assert m.text == "诊断 茅台" and m.is_group is True
    assert discord.parse_message(_payload("/大盘"), BOT) is not None


def test_guild_all_mode():
    assert discord.parse_message(_payload("大家好"), BOT, guild_mode="all").text == "大家好"


def test_allowed_channels():
    p = _payload("<@123> x", mentions=["123"])
    assert discord.parse_message(p, BOT, allowed_channels=["c2"]) is None
    assert discord.parse_message(p, BOT, allowed_channels=["c1"]) is not None
    assert discord.parse_message(p, BOT, allowed_channels=[]) is not None


def test_strips_mentions():
    p = _payload("<@!123>  持仓 <@123> ", mentions=["123"])
    assert "<@" not in discord.parse_message(p, BOT).text
    assert discord.parse_message(p, BOT).text.strip() == discord.parse_message(p, BOT).text
    assert discord.parse_message(_payload("<@123>", mentions=["123"]), BOT) is None
    assert discord.parse_message(_payload("<@!123>  ", mentions=["123"]), BOT) is None


def test_split_message():
    text = "\n".join(f"第{i}行" + "x" * 30 for i in range(100))
    parts = discord.split_message(text, limit=200)
    assert len(parts) > 1 and all(len(p) <= 200 for p in parts)
    assert "".join(parts).replace("\n", "") == text.replace("\n", "")
    long = "y" * 5000
    parts = discord.split_message(long, limit=2000)
    assert all(len(p) <= 2000 for p in parts) and "".join(parts) == long
    assert discord.split_message("短", 2000) == ["短"]
    discord.split_message("")  # 不崩


def test_start_without_library(monkeypatch):
    real_import = builtins.__import__

    def fake(name, *a, **k):
        if name == "discord" or name.startswith("discord."):
            raise ImportError("no discord")
        return real_import(name, *a, **k)

    monkeypatch.delitem(sys.modules, "discord", raising=False)
    monkeypatch.setattr(builtins, "__import__", fake)
    bot = discord.DiscordBot("tok", object())
    assert bot.start() is False


@pytest.fixture
def clean_manager(monkeypatch):
    monkeypatch.setattr(manager, "_running", {})
    return manager


class _Pipe:
    pass


@pytest.mark.parametrize("token", ["", None, "your-discord-token"])
def test_manager_skips_placeholder_token(clean_manager, monkeypatch, token):
    created = []

    class Fake:
        def __init__(self, *a, **k):
            created.append(1)

        def start(self):
            created.append(2)
            return True

    monkeypatch.setattr(discord, "DiscordBot", Fake)
    cfg = {"bot": {"discord": {"enabled": True, "token": token}}}
    assert clean_manager.start_bots(cfg, pipeline=_Pipe()) == []
    assert created == []


def test_manager_starts_with_token(clean_manager, monkeypatch):
    created = []

    class Fake:
        def __init__(self, *a, **k):
            created.append(1)

        def start(self):
            return True

    monkeypatch.setattr(discord, "DiscordBot", Fake)
    cfg = {"bot": {"discord": {"enabled": True, "token": "real-token"}}}
    assert "discord" in clean_manager.start_bots(cfg, pipeline=_Pipe())
    assert created


def test_bot_settings_discord_masked_and_kept(env):  # noqa: F811
    client, app, config = env
    config["bot"] = {"discord": {"enabled": False, "token": "disc-secret", "guild_mode": "mention", "allowed_channels": []}}
    got = client.get("/api/v1/settings/bot").json()
    assert got["bot"]["discord"]["token"] == "******"
    body = {"bot": {"discord": {"enabled": False, "token": "******", "guild_mode": "all", "allowed_channels": ["1", "2"]}}}
    assert client.put("/api/v1/settings/bot", json=body).status_code == 200
    written = settings_store.read_settings()["bot"]["discord"]
    assert written["token"] == "disc-secret"
    assert written["guild_mode"] == "all" and written["allowed_channels"] == ["1", "2"]
