"""真实临时子进程与模拟 App Server 协议；不调用模型、不访问网络。"""
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
from src.services import codex_runtime
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


SERVER = '''import json, sys
injected = 0
thread = 'provider-thread'
def send(value):
    print(json.dumps(value), flush=True)
for line in sys.stdin:
    message = json.loads(line)
    method = message.get('method')
    params = message.get('params', {})
    if method == 'initialized': continue
    if method == 'initialize': result = {}
    elif method == 'thread/start':
        assert params['approvalPolicy'] == 'never' and params['ephemeral']
        assert all(t['name'] in {'context_pack','resolve_stock','quote','daily_bars','diagnosis','watchlist','screening'} for t in params['dynamicTools'])
        result = {'thread': {'id': thread}, 'activePermissionProfile': {'id': 'quant_research'}}
    elif method == 'thread/inject_items':
        injected = len(params['items'])
        result = {}
    elif method == 'turn/start':
        send({'id': message['id'], 'result': {'turn': {'id': 'turn'}}})
        send({'id': 700, 'method': 'item/tool/call', 'params': {'threadId': thread, 'turnId': 'turn', 'tool': 'watchlist', 'arguments': {}}})
        continue
    elif message.get('id') == 700:
        assert message['result']['success']
        send({'method': 'item/completed', 'params': {'threadId': thread, 'turnId': 'turn', 'item': {'type': 'agentMessage', 'phase': 'final_answer', 'text': '恢复消息数:' + str(injected)}}})
        send({'method': 'turn/completed', 'params': {'threadId': thread, 'turn': {'id': 'turn', 'status': 'completed'}}})
        continue
    else: raise RuntimeError('unsupported')
    send({'id': message['id'], 'result': result})
'''


@pytest.mark.skipif(os.name != 'posix', reason='POSIX 子进程验收')
def test_app_server_tool_loop_and_resume_persist_only_finished_messages(tmp_path, db_path, monkeypatch):
    binary = executable(tmp_path, SERVER)
    monkeypatch.setenv('CODEX_HOME', str(tmp_path / 'empty-home'))
    monkeypatch.setattr('src.services.chat_tools.ChatTools.call', lambda self, name, args: '本地自选证据')
    config = {'database': {'sqlite_path': db_path}, 'integrations': {'runtime': {'codex_cli': binary, 'timeout_seconds': 3}}}
    session = codex_runtime.create_session(config)
    first = codex_runtime.ask(config, '问题一', session)
    assert first['answer'] == '恢复消息数:0' and first['tools'] == [{'name': 'watchlist', 'success': True}]
    assert codex_runtime.ask(config, '问题二', session)['answer'] == '恢复消息数:2'
    saved = codex_runtime.OverseasResearchService(config).cached('runtime-chat:' + session)
    assert len(saved['messages']) == 4 and saved['last_run']['resumed']
    assert not codex_runtime.cancel(session)


@pytest.mark.skipif(os.name != 'posix', reason='POSIX 子进程验收')
def test_app_server_permission_mismatch_never_starts_turn(tmp_path, db_path, monkeypatch):
    binary = executable(tmp_path, SERVER.replace("'id': 'quant_research'", "'id': 'unsafe'"))
    monkeypatch.setenv('CODEX_HOME', str(tmp_path / 'empty-home'))
    config = {'database': {'sqlite_path': db_path}, 'integrations': {'runtime': {'codex_cli': binary, 'timeout_seconds': 3}}}
    session = codex_runtime.create_session(config)
    with pytest.raises(RuntimeError, match='只读权限'):
        codex_runtime.ask(config, '问题', session)
    saved = codex_runtime.OverseasResearchService(config).cached('runtime-chat:' + session)
    assert saved['messages'] == [] and saved['last_run']['status'] == 'failed'


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


@pytest.mark.skipif(os.name != 'posix', reason='POSIX 子进程验收')
def test_app_server_disallowed_tool_stops_without_persisting_messages(tmp_path, db_path, monkeypatch):
    binary = executable(tmp_path, SERVER.replace("'tool': 'watchlist'", "'tool': 'execute_order'"))
    monkeypatch.setenv('CODEX_HOME', str(tmp_path / 'empty-home'))
    config = {'database': {'sqlite_path': db_path}, 'integrations': {'runtime': {'codex_cli': binary, 'timeout_seconds': 3}}}
    session = codex_runtime.create_session(config)
    with pytest.raises(RuntimeError, match='只读范围'):
        codex_runtime.ask(config, '问题', session)
    saved = codex_runtime.OverseasResearchService(config).cached('runtime-chat:' + session)
    assert saved['messages'] == [] and saved['last_run']['tools'] == []


def test_app_server_queued_cancellation_does_not_start_process(db_path, monkeypatch):
    config = {'database': {'sqlite_path': db_path}}
    session = codex_runtime.create_session(config)
    codex_runtime.reserve(session)
    assert codex_runtime.cancel(session)
    monkeypatch.setattr(codex_runtime, 'AppServer', lambda *args: pytest.fail('取消后不应启动程序'))
    with pytest.raises(RuntimeError, match='取消'):
        codex_runtime.ask(config, '问题', session)
    assert session not in codex_runtime._active
    saved = codex_runtime.OverseasResearchService(config).cached('runtime-chat:' + session)
    assert saved['messages'] == [] and saved['last_run']['status'] == 'cancelled'
