"""机器人消息模型：屏蔽各平台的消息格式差异"""

from dataclasses import dataclass


@dataclass
class BotMessage:
    platform: str          # dingtalk / feishu
    chat_id: str           # 会话 ID：单聊为与该用户的会话，群聊为群
    user_id: str
    user_name: str
    text: str              # 已去掉 @机器人 的文字
    is_group: bool = False
    message_id: str = ""

    @property
    def session_key(self) -> str:
        """AI 问股的会话键：群里每个人各自一段对话"""
        return f"{self.platform}:{self.chat_id}:{self.user_id}"
