"""
Web 访问控制（参考 daily_stock_analysis 的管理员密码登录）

- web.auth_enabled 为 false（默认）时不需要登录，但只接受本机请求（127.0.0.1/::1），
  这样没设密码时即使监听了 0.0.0.0，局域网里的其他设备也访问不了
- 开启后第一次访问先设置密码；密码用 PBKDF2 加盐保存在 data/web_auth.json（不在 settings.yaml 里）
- 登录后发放签名 Cookie（HMAC，web.session_days 天有效）；机器人、脚本也可以用 web.api_token 作为 Bearer Token
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from pathlib import Path
from typing import Any

AUTH_FILE = Path("data/web_auth.json")
COOKIE_NAME = "qt_session"
PBKDF2_ROUNDS = 200_000
LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost", "testclient"}
PUBLIC_PATHS = ("/api/v1/health", "/api/v1/auth/")


class AuthStore:
    def __init__(self, path: Path = AUTH_FILE):
        self.path = path

    def _load(self) -> dict[str, Any]:
        if self.path.exists():
            try:
                return json.loads(self.path.read_text(encoding="utf-8"))
            except ValueError:
                return {}
        return {}

    def _save(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data), encoding="utf-8")

    def _secret(self) -> bytes:
        data = self._load()
        if not data.get("secret"):
            data["secret"] = secrets.token_hex(32)
            self._save(data)
        return bytes.fromhex(data["secret"])

    def has_password(self) -> bool:
        return bool(self._load().get("password_hash"))

    def set_password(self, password: str) -> None:
        salt = secrets.token_bytes(16)
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ROUNDS)
        data = self._load()
        data.update({"salt": salt.hex(), "password_hash": digest.hex()})
        self._save(data)

    def verify_password(self, password: str) -> bool:
        data = self._load()
        if not data.get("password_hash"):
            return False
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(data["salt"]), PBKDF2_ROUNDS)
        return hmac.compare_digest(digest.hex(), data["password_hash"])

    def issue_session(self, days: float) -> str:
        expires = int(time.time() + days * 86400)
        signature = hmac.new(self._secret(), str(expires).encode(), hashlib.sha256).hexdigest()
        return f"{expires}.{signature}"

    def verify_session(self, token: str | None) -> bool:
        if not token or "." not in token:
            return False
        expires, signature = token.split(".", 1)
        if not expires.isdigit() or int(expires) < time.time():
            return False
        expected = hmac.new(self._secret(), expires.encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(signature, expected)


def is_public_path(path: str) -> bool:
    return not path.startswith("/api/") or any(path == p or path.startswith(p) for p in PUBLIC_PATHS)


def check_request(web_cfg: dict, store: AuthStore, path: str, client_host: str, cookie: str | None, authorization: str | None) -> str:
    """访问是否允许：返回空字符串表示放行，否则返回拒绝原因。"""
    if is_public_path(path):
        return ""
    api_token = str(web_cfg.get("api_token") or "")
    if api_token and authorization == f"Bearer {api_token}":
        return ""
    if not web_cfg.get("auth_enabled"):
        return "" if client_host in LOCAL_HOSTS else "未开启登录时只允许本机访问，请在设置里开启 Web 登录"
    return "" if store.verify_session(cookie) else "请先登录"
