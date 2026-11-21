"""真实临时子进程；不调用模型、不访问网络。"""
import json
import os
from pathlib import Path
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from src.analyzers import local_runtime
from src.analyzers.llm_client import build_route, LLMClient
from tests.test_api import env  # noqa: F401
from tests.test_decision_signals import db_path  # noqa: F401


def executable(tmp_path, source):
    target = tmp_path / 'fake-model'
    target.write_text('#!' + sys.executable + '\n' + source)
    target.chmod(0o700)
    return str(target)


@pytest.mark.parametrize('url', ['https://remote.example/v1', 'http://localhost.evil/v1', 'http://user:pass@localhost/v1',
    'http://127.0.0.1/v1?token=x', 'file:///tmp/v1', 'http://127.0.0.1/admin'])
def test_hermes_rejects_remote_credentials_and_path(url):
    with pytest.raises(ValueError): local_runtime.validate_hermes_url(url)


def test_local_routes_reject_keys_and_use_correct_capabilities(tmp_path):
    for name in local_runtime.PROVIDERS:
        route = build_route({'provider': name, 'model': 'default'})
        assert route.provider == name and not route.api_key and route.api_base is None
        with pytest.raises(ValueError): build_route({'provider': name, 'model': 'default', 'api_key': 'secret'})
    client = LLMClient({'cache_enabled': False, 'cache_path': str(tmp_path / 'cache'), 'usage_path': str(tmp_path / 'usage')})
    route = build_route({'provider': 'hermes', 'model': 'hermes-agent'})
    with pytest.raises(ValueError): client._complete(route, {'tools': [{'type': 'function'}], 'messages': []})
    with pytest.raises(ValueError): list(client._stream_once(route, {}, {}))


@pytest.mark.skipif(os.name != 'posix', reason='临时 shebang 程序的集成测试仅在 POSIX 运行；协议断言跨平台')
@pytest.mark.parametrize('provider', ['codex_cli', 'claude_code_cli', 'opencode_cli'])
def test_cli_executes_fixed_commands_with_isolated_prompt(tmp_path, monkeypatch, provider):
    source = '''import json, os, pathlib, sys
assert 'QUANT__LLM__PRIMARY__API_KEY' not in os.environ
args = sys.argv[1:]
if '--output-last-message' in args:
    assert '--sandbox' in args and 'read-only' in args and '--ephemeral' in args
    prompt = sys.stdin.read()
    pathlib.Path(args[args.index('--output-last-message')+1]).write_text('回答:' + prompt)
elif '--safe-mode' in args:
    assert '--tools' in args and args[args.index('--tools')+1] == ''
    print(json.dumps({'result': '回答:' + sys.stdin.read()}))
else:
    assert '--pure' in args
    assert json.loads(pathlib.Path('opencode.json').read_text())['permission']['*'] == 'deny'
    prompt = pathlib.Path(args[args.index('--file')+1]).read_text()
    print(json.dumps({'type': 'text', 'part': {'text': '回答:' + prompt}}))
'''
    binary = executable(tmp_path, source)
    monkeypatch.setenv('QUANT__LLM__PRIMARY__API_KEY', 'must-not-leak')
    config = {'integrations': {'runtime': {provider: binary, 'timeout_seconds': 2}}}
    result = local_runtime.generate(provider, 'default', [{'role': 'user', 'content': '证券问题'}], config=config)
    assert '证券问题' in result


@pytest.mark.skipif(os.name != 'posix', reason='POSIX 子进程验收')
def test_cli_timeout_and_cancel_terminate_process(tmp_path):
    binary = executable(tmp_path, 'import time\ntime.sleep(20)\n')
    config = {'integrations': {'runtime': {'codex_cli': binary, 'timeout_seconds': 1}}}
    before = time.monotonic()
    with pytest.raises(RuntimeError, match='超时'):
        local_runtime.generate('codex_cli', 'default', [{'role': 'user', 'content': 'q'}], config=config)
    assert time.monotonic() - before < 4
    event = threading.Event(); event.set()
    with pytest.raises(RuntimeError, match='取消'):
        local_runtime.generate('codex_cli', 'default', [{'role': 'user', 'content': 'q'}], config=config, cancel_event=event)


def test_hermes_never_sends_credentials_tools_or_proxy(monkeypatch):
    calls = []
    class Client:
        def __init__(self, **kwargs): calls.append(kwargs)
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def post(self, url, **kwargs):
            calls.append((url, kwargs))
            return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {'choices': [{'message': {'content': 'ok'}}]})
    monkeypatch.setattr('httpx.Client', Client)
    assert local_runtime.generate('hermes', 'hermes-agent', [{'role': 'user', 'content': 'q'}], config={}) == 'ok'
    assert calls[0]['trust_env'] is False and calls[0]['follow_redirects'] is False
    assert set(calls[1][1]['json']) == {'model', 'messages', 'stream'}


def test_invalid_local_settings_are_rejected_before_save_or_import(env):
    client, _, config = env
    from src.services.config_check import check_config
    for section in ({'provider': 'hermes', 'model': 'default', 'base_url': 'https://remote.example/v1'},
                    {'provider': 'codex_cli', 'model': 'default', 'api_key': 'do-not-save'}):
        response = client.put('/api/v1/settings/llm', json={'llm': {'primary': section}})
        assert response.status_code == 400
        assert not check_config({**config, 'llm': {'primary': section}})['ok']
    invalid = {**config, 'integrations': {'runtime': {'hermes_url': 'https://remote.example/v1'}}}
    assert not check_config(invalid)['ok']


@pytest.mark.parametrize('provider', local_runtime.PROVIDERS)
def test_chat_ui_uses_buffered_generation_for_local_providers(tmp_path, monkeypatch, provider):
    from src.services.stock_chat import StockChatSession
    captured = []
    def generate(name, model, messages, **kwargs):
        captured.append((name, messages, kwargs))
        return '{"answer":"증거가 부족하므로 관망합니다."}'
    monkeypatch.setattr(local_runtime, 'generate', generate)
    llm = LLMClient({'primary': {'provider': provider, 'model': 'default'}, 'cache_enabled': False,
                     'cache_path': str(tmp_path / 'cache.db'), 'usage_path': str(tmp_path / 'usage.db')})
    class Tools:
        def call(self, name, args): return 'cached evidence'
    events = list(StockChatSession({'report': {'language': 'ko'}}, llm=llm, tools=Tools()).ask_stream('AAPL 연구'))
    assert events[-1]['type'] == 'done'
    assert '관망' in events[-1]['turn']['answer']
    assert len(captured) == 1 and captured[0][0] == provider
    assert 'Korean' in captured[0][1][0]['content']
    assert 'stream' not in captured[0][2] and 'tools' not in captured[0][2]


def test_removed_app_server_endpoints_return_gone_and_status_omits_it(env):
    """Codex App Server 只读问股已移除：旧接口明确返回 410，本地模型状态里不再列出它。"""
    client, _, _ = env
    for path, body in (('/api/v1/integrations/runtime/chat', {'question': '检查自选股'}),
                       ('/api/v1/integrations/runtime/cancel', {'session_id': 'a' * 32})):
        response = client.post(path, json=body)
        assert response.status_code == 410 and '已移除' in response.json()['detail']
    providers = [row['provider'] for row in client.get('/api/v1/integrations/runtime').json()]
    assert 'codex_app_server' not in providers and 'codex_cli' in providers
