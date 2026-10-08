"""Longbridge 认证：复用 SDK OAuth 缓存，无头服务禁止交互式授权。"""
from __future__ import annotations

import base64
import hashlib
import importlib
import json
import os
from pathlib import Path
import re
import tempfile
import threading

MAX_CACHE_BYTES = 65536
MAX_SEED_LENGTH = 90000
_LOCK = threading.Lock()
_KEYS = ('app_key', 'app_secret', 'access_token', 'oauth_client_id', 'oauth_token_cache_b64')


class AuthenticationError(RuntimeError):
    """错误只包含本地状态，不携带 SDK 异常、授权 URL 或凭据。"""


def credentials(config: dict) -> dict[str, str]:
    section = (config.get('integrations') or {}).get('longbridge') or {}
    value = {key: str(section.get(key) or os.environ.get('LONGBRIDGE_' + key.upper()) or '').strip()
             for key in _KEYS}
    # 开发者门户有时把公开 OAuth client id 标为 App Key。
    if not value['oauth_client_id'] and value['app_key'] and not value['access_token']:
        value['oauth_client_id'] = value['app_key']
    return value


def validate_client_id(client_id: str) -> str:
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', client_id):
        raise ValueError('OAuth Client ID 只能包含字母、数字、下划线和连字符，最多 128 字符')
    return client_id


def cache_path(client_id: str) -> Path:
    return Path.home() / '.longbridge' / 'openapi' / 'tokens' / validate_client_id(client_id)


def _valid_payload(raw: bytes) -> bool:
    if not raw or len(raw) > MAX_CACHE_BYTES:
        return False
    try:
        value = json.loads(raw.decode('utf-8'))
        return isinstance(value, (dict, list)) and bool(value)
    except (ValueError, UnicodeError, RecursionError):
        return False


def decode_seed(seed: str) -> bytes:
    try:
        if len(seed) > MAX_SEED_LENGTH:
            raise ValueError()
        raw = base64.b64decode(seed, validate=True)
        if not _valid_payload(raw):
            raise ValueError()
        return raw
    except (ValueError, UnicodeError):
        raise ValueError('OAuth 缓存必须是有效 JSON 的 Base64，解码后不超过 64 KiB') from None


def valid_cache(path: Path) -> bool:
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_CACHE_BYTES:
            return False
        return _valid_payload(path.read_bytes())
    except OSError:
        return False


def _atomic_private_write(path: Path, payload: bytes) -> None:
    if path.is_symlink() or any(parent.is_symlink() for parent in path.parents):
        raise AuthenticationError('OAuth 缓存目录不能使用符号链接')
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(prefix='.oauth-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(payload)
        os.chmod(name, 0o600)
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def restore_seed(client_id: str, seed: str) -> None:
    """同一份部署密钥只导入一次，避免覆盖 SDK 自动续期后的 token。"""
    if not seed:
        return
    raw = decode_seed(seed)
    path = cache_path(client_id)
    marker = path.with_name(path.name + '.seed.sha256')
    digest = hashlib.sha256(raw).hexdigest().encode('ascii')
    with _LOCK:
        if marker.is_file() and not marker.is_symlink() and marker.stat().st_size == len(digest):
            if marker.read_bytes() == digest:
                return
        # 用户更换 seed 表示主动重新授权；同一 seed 在失效后也不重复恢复。
        _atomic_private_write(path, raw)
        _atomic_private_write(marker, digest)


def _reject_interactive(_url: str) -> None:
    raise AuthenticationError('OAuth 缓存需要重新授权；请在交互式终端运行授权脚本')


def _supports_oauth(sdk) -> bool:
    return callable(getattr(sdk, 'OAuthBuilder', None)) and callable(getattr(sdk.Config, 'from_oauth', None))


def configured(config: dict) -> bool:
    value = credentials(config)
    return bool(value['oauth_client_id'] or all(value[k] for k in _KEYS[:3]))


def build_config(config: dict, sdk=None):
    sdk = sdk or importlib.import_module('longbridge.openapi')
    value = credentials(config)
    oauth_error = 'Longbridge 尚未配置完整凭据'
    if value['oauth_client_id']:
        oauth_error = '当前 Longbridge SDK 不支持 OAuth；需升级兼容 SDK 或配置完整旧版凭据'
        if _supports_oauth(sdk):
            try:
                restore_seed(value['oauth_client_id'], value['oauth_token_cache_b64'])
                if not valid_cache(cache_path(value['oauth_client_id'])):
                    raise AuthenticationError('OAuth 缓存缺失或无效；请先运行交互式授权脚本')
                oauth = sdk.OAuthBuilder(value['oauth_client_id']).build(_reject_interactive)
                return sdk.Config.from_oauth(oauth)
            except Exception:
                oauth_error = 'OAuth 缓存缺失、无效或需重新授权；请检查认证状态并运行授权脚本'
    if all(value[k] for k in _KEYS[:3]):
        try:
            factory = getattr(sdk.Config, 'from_apikey', sdk.Config)
            return factory(*(value[k] for k in _KEYS[:3]))
        except Exception:
            raise AuthenticationError('Longbridge 旧版凭据初始化失败') from None
    raise AuthenticationError(oauth_error) from None


def diagnostics(config: dict) -> dict:
    """只读检查；不恢复缓存、不连接券商、不验证账户权限。"""
    value = credentials(config)
    legacy = all(value[k] for k in _KEYS[:3])
    try:
        sdk = importlib.import_module('longbridge.openapi')
    except Exception:
        return {'status': 'sdk_missing', 'oauth_supported': False, 'legacy_configured': legacy,
                'message': 'Longbridge SDK 不可用'}
    supported = _supports_oauth(sdk)
    status, message = ('legacy_ready', '旧版凭据已配置；查询时验证连接与权限') if legacy else ('not_configured', '尚未配置 Longbridge 认证')
    if value['oauth_client_id']:
        try:
            path = cache_path(value['oauth_client_id'])
            if not supported:
                status, message = 'oauth_unsupported', '当前 SDK 不支持 OAuth；可使用完整旧版凭据或升级兼容 SDK'
            elif valid_cache(path):
                status, message = 'oauth_cached', 'OAuth 本地缓存可读取；查询时验证有效性'
            elif value['oauth_token_cache_b64']:
                decode_seed(value['oauth_token_cache_b64'])
                marker = path.with_name(path.name + '.seed.sha256')
                digest = hashlib.sha256(decode_seed(value['oauth_token_cache_b64'])).hexdigest().encode('ascii')
                if marker.is_file() and marker.stat().st_size == len(digest) and marker.read_bytes() == digest:
                    status, message = 'oauth_cache_missing', '已导入的 OAuth 缓存失效或已删除，请重新授权并更换部署缓存'
                else:
                    status, message = 'oauth_seed_ready', 'OAuth 部署缓存已配置；首次查询时导入并验证'
            else:
                status, message = 'oauth_cache_missing', '请在交互式终端运行 scripts/authorize_longbridge.py 完成授权'
        except (ValueError, OSError):
            status, message = 'oauth_invalid', 'OAuth Client ID 或部署缓存格式无效'
        if legacy:
            message += '；OAuth 不可用时回退到旧版凭据'
    return {'status': status, 'oauth_supported': supported, 'legacy_configured': legacy, 'message': message}
