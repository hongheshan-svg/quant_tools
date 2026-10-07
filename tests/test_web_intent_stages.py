"""确定性任务顺序、歧义确认、证券范围和流式阶段持久化回归。"""
import threading
import pytest

from tests.test_stock_chat import tools  # noqa: F401
from tests.test_chat_stream import StreamLLM, FakeTools
from src.services.web_intent import resolve
from src.services.stock_chat import StockChatSession
from src.services.chat_sessions import ChatSessionStore
from src.services.stock_search import StockSearch


@pytest.fixture
def names(monkeypatch):
    entries = [('600519', '贵州茅台', (), 'stock'), ('000001', '平安银行', (), 'stock'), ('601318', '中国平安', (), 'stock')]
    monkeypatch.setattr(StockSearch, '_ensure_index', lambda self: entries)
    return entries


def test_intent_order_and_followup(names):
    plan = resolve('分析茅台，然后看看大盘，接着持仓风险，最后选股', 'unused')
    assert [task['kind'] for task in plan['tasks']] == ['stock_analysis', 'market_review', 'portfolio_risk', 'strategy_screening']
    followup = resolve('它的行情呢', 'unused', plan['state'])
    assert followup['tasks'][0]['targets'][0]['code'] == '600519'
    assert resolve('分析茅台并复盘大盘', 'unused')['tasks'][-1]['kind'] == 'market_review'


def test_ambiguous_confirmation_and_new_topic(names):
    plan = resolve('分析平安，然后复盘大盘', 'unused')
    assert plan['requires_confirmation'] and len(plan['state']['pending']['candidates']) == 2
    confirmed = resolve('000001', 'unused', plan['state'])
    assert not confirmed['requires_confirmation']
    assert confirmed['tasks'][0]['targets'][0]['code'] == '000001'
    assert confirmed['tasks'][1]['kind'] == 'market_review'
    assert resolve('看看大盘', 'unused', plan['state'])['state']['pending'] is None
    assert resolve('分析平安银行', 'unused')['tasks'][0]['targets'][0]['code'] == '000001'
    assert resolve('分析999999', 'unused')['requires_confirmation']


def test_explicit_scope_cannot_be_overridden(names):
    plan = resolve('分析平安银行', 'unused', stock_context={'code': '600519'})
    assert plan['requires_confirmation'] and not plan['tasks']


def test_confirmation_rechecks_scope_and_does_not_mutate_pending(names):
    plan = resolve('分析平安，然后看看大盘', 'unused')
    blocked = resolve('000001', 'unused', plan['state'], {'code': '600519'})
    assert blocked['requires_confirmation'] and not blocked['tasks']
    assert plan['state']['pending']['tasks'][0].get('needs_confirmation')
    allowed = resolve('000001', 'unused', plan['state'], {'code': 'sz000001'})
    assert not allowed['requires_confirmation']


@pytest.mark.parametrize('question,expected', [
    ('先看看持仓再分析贵州茅台', ['portfolio_risk', 'stock_analysis']),
    ('先复盘大盘再看看持仓最后分析贵州茅台', ['market_review', 'portfolio_risk', 'stock_analysis']),
    ('先分析贵州茅台再看看持仓', ['stock_analysis', 'portfolio_risk']),
])
def test_order_inside_single_clause(names, question, expected):
    assert [task['kind'] for task in resolve(question, 'unused')['tasks']] == expected


def test_store_executes_order_without_network_and_replays_stages(tools, monkeypatch):
    config = tools.config
    llm = StreamLLM([{'answer': '个股结论'}, {'answer': '大盘结论'}, {'answer': '组合结论'}])
    chat = StockChatSession(config, llm=llm, tools=tools)
    store = ChatSessionStore(config, session_factory=lambda cfg: chat)
    record = store.create()
    events = list(store.ask_stream(record['id'], '分析茅台，然后复盘大盘，然后持仓风险'))
    assert len(chat.turns) == 1
    turn = store.get(record['id'])['turns'][0]
    assert [task['kind'] for task in turn['intent_plan']] == ['stock_analysis', 'market_review', 'portfolio_risk']
    assert all(word in turn['answer'] for word in ['个股结论', '大盘结论', '组合结论'])
    assert turn['stage_events'] and all(event['elapsed_ms'] >= 0 for event in turn['stage_events'])
    assert len([event for event in events if event['type'] == 'done']) == 1
    assert chat.config is config


def test_stages_record_failure_and_cancel():
    chat = StockChatSession({}, llm=StreamLLM([RuntimeError('离线失败')]), tools=FakeTools())
    events = list(chat.ask_stream('问题'))
    assert events[-1]['turn']['error'] and chat.turns[-1].stage_events[-1]['status'] == 'failed'
    assert chat.turns[-1].stage_events[-1]['reason'] == '离线失败'
    cancel = threading.Event()
    def on_call(*_): cancel.set()
    chat = StockChatSession({}, llm=StreamLLM([{'tool_calls': [{'name': 'quote', 'args': {'code': '600519'}}]}]), tools=FakeTools(on_call))
    list(chat.ask_stream('问题', cancel=cancel))
    assert chat.turns[-1].error == '已取消'
    assert all(event['status'] != 'started' for event in {event['stage_id']: event for event in chat.turns[-1].stage_events}.values())


def test_portfolio_tool_preserves_unavailable_quality(tools, monkeypatch):
    import json
    from src.services.portfolio_risk import PortfolioRiskService
    accounts = []
    monkeypatch.setattr(PortfolioRiskService, 'report', lambda self: accounts.append(self.account) or {'account': self.account, 'quality': {'valuation': 'partial'}, 'exposure': None, 'drawdown': {'current_drawdown': None}, 'cash_known': False})
    payload = json.loads(tools.call('portfolio_risk', {'account': 'real:家人'}))
    assert accounts == ['real:家人'] and payload['quality']['valuation'] == 'partial'
    assert payload['exposure'] is None and payload['drawdown']['current_drawdown'] is None


def test_confirmation_executes_both_resolved_targets_in_original_order(tools, names):
    llm = StreamLLM([{'answer': '茅台研究'}, {'answer': '平安研究'}])
    chat = StockChatSession(tools.config, llm=llm, tools=tools)
    store = ChatSessionStore(tools.config, session_factory=lambda cfg: chat)
    record = store.create()
    list(store.ask_stream(record['id'], '分析贵州茅台和平安'))
    events = list(store.ask_stream(record['id'], '000001'))
    answer = store.get(record['id'])['turns'][-1]
    assert [t['code'] for t in answer['intent_plan'][0]['targets']] == ['600519', '000001']
    assert '茅台研究' in answer['answer'] and '平安研究' in answer['answer']
    assert events[-1]['type'] == 'done'


def test_overseas_and_domestic_codes_in_same_intent(names):
    result = resolve('分析 AAPL 和贵州茅台，以及 005930.KS', 'unused')
    assert [t['code'] for task in result['tasks'] for t in task['targets']] == ['AAPL', '600519', '005930.KS']
