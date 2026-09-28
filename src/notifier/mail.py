"""
邮件推送（SMTP）：纯文本 + HTML 两种格式一起发送，收件人可以有多个。
"""

from __future__ import annotations

import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr

from loguru import logger

from src.notifier.base import markdown_to_html

DEFAULT_SSL_PORT = 465
TIMEOUT_SECONDS = 15


def recipients(value) -> list[str]:
    if isinstance(value, str):
        value = value.replace("；", ",").replace(";", ",").split(",")
    return [str(v).strip() for v in value or [] if str(v).strip()]


class EmailNotifier:
    """SMTP 邮件推送"""

    def __init__(self, config: dict):
        cfg = config.get("notifier", {}).get("email", {}) or {}
        self.enabled = bool(cfg.get("enabled", False))
        self.host = str(cfg.get("smtp_host") or "").strip()
        self.port = int(cfg.get("smtp_port") or DEFAULT_SSL_PORT)
        self.use_ssl = bool(cfg.get("use_ssl", True))
        self.username = str(cfg.get("username") or "").strip()
        self.password = str(cfg.get("password") or "")
        self.sender = str(cfg.get("sender") or self.username).strip()
        self.to = recipients(cfg.get("to"))

    @staticmethod
    def is_configured(cfg: dict) -> bool:
        return bool(str(cfg.get("smtp_host") or "").strip() and recipients(cfg.get("to")))

    def send(self, title: str, content: str) -> bool:
        if not self.enabled:
            logger.debug("邮件推送未启用")
            return False
        if not self.host or not self.to:
            logger.warning("邮件推送缺少 SMTP 服务器或收件人")
            return False
        message = MIMEMultipart("alternative")
        message["Subject"] = title
        message["From"] = formataddr(("A股量化系统", self.sender)) if self.sender else ""
        message["To"] = ", ".join(self.to)
        message.attach(MIMEText(content, "plain", "utf-8"))
        message.attach(MIMEText(markdown_to_html(f"# {title}\n\n{content}"), "html", "utf-8"))
        try:
            smtp_cls = smtplib.SMTP_SSL if self.use_ssl else smtplib.SMTP
            with smtp_cls(self.host, self.port, timeout=TIMEOUT_SECONDS) as smtp:
                if not self.use_ssl:
                    smtp.starttls()
                if self.username:
                    smtp.login(self.username, self.password)
                smtp.sendmail(self.sender or self.username, self.to, message.as_string())
            logger.info(f"邮件推送成功: {title} → {len(self.to)} 个收件人")
            return True
        except Exception as e:
            logger.error(f"邮件推送失败: {e}")
            return False
