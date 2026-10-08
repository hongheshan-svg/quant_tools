"""OAuth 缓存和认证回退，全部使用临时目录和假 SDK。"""
import base64
import json
import os
import sys
from types import SimpleNamespace

import pytest

from src.services import longbridge_auth as auth
from tests.test_api import env  # noqa: F401


def config(**kwargs):
    return {'integrations': {'longbridge': kwargs}}


def seed(value='fixture'):
    return base64.b64encode(json.dumps({'access_token': value}).encode()).decode()


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(auth.Path, 'home', lambda: tmp_path)
    for key in auth._KEYS:
        monkeypatch.delenv('LONGBRIDGE_' + key.upper(), raising=False)


def sdk(callback=None):
    calls = []
    class Builder:
        def __init__(self, client_id): calls.append(client_id)
        def build(self, open_url):
            if callback:
                callback(open_url)
            return 'oauth-object'
    return SimpleNamespace(OAuthBuilder=Builder, Config=SimpleNamespace(
        from_oauth=lambda oauth: ('oauth', oauth), from_apikey=lambda *args: ('legacy', args))), calls


def test_oauth_cache_seed_reused_without_reverting_sdk_rotation():
    auth.restore_seed('client-1', seed('first'))
    path = auth.cache_path('client-1')
    path.write_text('{"access_token":"rotated"}')
    auth.restore_seed('client-1', seed('first'))
    assert json.loads(path.read_text())['access_token'] == 'rotated'
    auth.restore_seed('client-1', seed('new'))
    assert json.loads(path.read_text())['access_token'] == 'new'
    if os.name != 'nt':
        assert path.stat().st_mode & 0o777 == 0o600
    path.unlink()
    auth.restore_seed('client-1', seed('new'))
    assert not path.exists(), '删除或失效后不可恢复同一份旧部署缓存'


@pytest.mark.parametrize('value', ['../escape', '', 'a/b', 'a\\b', '.', 'x' * 129])
def test_client_id_cannot_escape_cache_directory(value):
    with pytest.raises(ValueError):
        auth.cache_path(value)


@pytest.mark.parametrize('value', ['bad!', base64.b64encode(b'[]').decode(), base64.b64encode(b'null').decode(), 'x' * 90001])
def test_invalid_seed_rejected_without_files(value, tmp_path):
    with pytest.raises(ValueError):
        auth.restore_seed('client', value)
    assert not list(tmp_path.rglob('*'))


def test_missing_cache_never_invokes_interactive_flow():
    fake, calls = sdk()
    with pytest.raises(auth.AuthenticationError):
        auth.build_config(config(oauth_client_id='client'), fake)
    assert not calls


def test_oauth_has_priority_and_reauth_falls_back_without_leaking_url():
    value = config(oauth_client_id='client', oauth_token_cache_b64=seed(), app_key='k', app_secret='s', access_token='t')
    fake, calls = sdk()
    assert auth.build_config(value, fake)[0] == 'oauth'
    assert calls == ['client']
    fake, _ = sdk(lambda open_url: open_url('https://login.example?secret=fixture'))
    assert auth.build_config(value, fake) == ('legacy', ('k', 's', 't'))
    value['integrations']['longbridge'].pop('access_token')
    with pytest.raises(auth.AuthenticationError) as error:
        auth.build_config(value, fake)
    assert 'fixture' not in str(error.value) and 'https:' not in str(error.value)


def test_old_sdk_does_not_restore_seed_and_supports_legacy_fallback(tmp_path):
    fake = SimpleNamespace(Config=SimpleNamespace(from_apikey=lambda *args: args))
    value = config(oauth_client_id='client', oauth_token_cache_b64=seed())
    with pytest.raises(auth.AuthenticationError, match='不支持 OAuth'):
        auth.build_config(value, fake)
    assert not list(tmp_path.rglob('*'))
    value['integrations']['longbridge'].update(app_key='k', app_secret='s', access_token='t')
    assert auth.build_config(value, fake) == ('k', 's', 't')


def test_environment_aliases_and_app_key_client_id(monkeypatch):
    monkeypatch.setenv('LONGBRIDGE_APP_KEY', 'client')
    monkeypatch.setenv('LONGBRIDGE_OAUTH_TOKEN_CACHE_B64', seed())
    fake, _ = sdk()
    assert auth.build_config({}, fake)[0] == 'oauth'
    assert auth.credentials(config(oauth_client_id='explicit'))['oauth_client_id'] == 'explicit'


def test_diagnostics_is_read_only_and_masks_seed_api(env, monkeypatch, tmp_path):
    fake, calls = sdk()
    monkeypatch.setitem(sys.modules, 'longbridge.openapi', fake)
    value = config(oauth_client_id='client', oauth_token_cache_b64=seed())
    result = auth.diagnostics(value)
    assert result['status'] == 'oauth_seed_ready'
    assert not calls and not list(tmp_path.rglob('.longbridge/*'))
    client, _, cfg = env
    cfg.update(value)
    response = client.get('/api/v1/integrations/longbridge/status')
    assert response.status_code == 200 and 'fixture' not in response.text and seed() not in response.text
    assert client.get('/api/v1/settings/integrations').json()['settings']['longbridge']['oauth_token_cache_b64'] == '******'
    assert client.put('/api/v1/settings/integrations', json={'longbridge': {'oauth_client_id': '../bad'}}).status_code == 400


def test_settings_validate_masked_seed_and_redact_supports_field():
    from src.services.integration_settings import merge_settings, settings
    from src.utils.redaction import redact
    value = config(oauth_client_id='client', oauth_token_cache_b64=seed())
    merged = merge_settings(value, {'longbridge': {'oauth_token_cache_b64': '******'}})
    assert merged['longbridge']['oauth_token_cache_b64'] == seed()
    assert seed() not in json.dumps(settings(value))
    assert seed() not in json.dumps(redact(value))
    with pytest.raises(ValueError):
        merge_settings(value, {'longbridge': {'oauth_token_cache_b64': 'bad'}})
