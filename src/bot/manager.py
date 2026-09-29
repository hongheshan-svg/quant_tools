"""按 bot 配置在后台启动聊天机器人（server.py 的 Web 服务和 main.py 的定时任务都会调用，同一进程只启动一次）"""

from __future__ import annotations

import threading

from loguru import logger

from src.bot.dispatcher import Dispatcher
from src.bot.router import CommandRouter

_running: dict[str, object] = {}
_lock = threading.Lock()


def _placeholder(value) -> bool:
    return not value or str(value).startswith("your-")


def start_bots(config: dict, pipeline=None) -> list[str]:
    """启动已启用且配置完整的机器人，返回本次启动的平台。修改配置后要重启服务才会生效。"""
    cfg = config.get("bot") or {}
    platforms = {
        "dingtalk": (cfg.get("dingtalk") or {}, ("client_id", "client_secret")),
        "feishu": (cfg.get("feishu") or {}, ("app_id", "app_secret")),
        "discord": (cfg.get("discord") or {}, ("token",)),
    }
    wanted = [name for name, (pcfg, _keys) in platforms.items() if pcfg.get("enabled")]
    if not wanted:
        return []
    started: list[str] = []
    with _lock:
        dispatcher = None
        for name in wanted:
            pcfg, keys = platforms[name]
            if name in _running:
                continue
            if any(_placeholder(pcfg.get(k)) for k in keys):
                logger.warning(f"[bot] {name} 已启用但没有填写 {' / '.join(keys)}，跳过")
                continue
            if dispatcher is None:
                dispatcher = Dispatcher(CommandRouter(config, pipeline), workers=int(cfg.get("workers", 2)))
            try:
                if name == "dingtalk":
                    from src.bot.dingtalk import DingTalkBot

                    bot = DingTalkBot(pcfg["client_id"], pcfg["client_secret"], dispatcher)
                elif name == "discord":
                    from src.bot.discord import DiscordBot

                    bot = DiscordBot(pcfg["token"], dispatcher, guild_mode=pcfg.get("guild_mode") or "mention",
                                     allowed_channels=[str(c) for c in (pcfg.get("allowed_channels") or [])])
                    if bot.start() is False:  # discord.py 没装
                        continue
                else:
                    from src.bot.feishu import FeishuBot

                    bot = FeishuBot(pcfg["app_id"], pcfg["app_secret"], dispatcher, domain=pcfg.get("domain", "feishu"))
                if name != "discord":
                    bot.start()
            except ImportError as e:
                logger.warning(f"[bot] {name} 的 SDK 没有安装（pip install -r requirements.txt）: {e}")
                continue
            except Exception as e:
                logger.error(f"[bot] {name} 启动失败: {e}")
                continue
            _running[name] = bot
            started.append(name)
    if started:
        logger.info(f"[bot] 已启动: {', '.join(started)}")
    return started


def running_bots() -> list[str]:
    return list(_running)
