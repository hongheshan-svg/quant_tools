"""F07/F08/F10/F14：真实存储链路、隔离上下文和诊断事件容错。"""
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import pytest

from tests.test_decision_signals import db_path  # noqa: F401
from src.database.db import get_db_session
from src.database.models import DecisionSignal, StockDaily, AlertRecord
from src.services.decision_signals import DecisionSignalService
from src.services.run_log import RunLog, execution_trace


def test_active_signal_alert_snapshot_and_unknown(db_path, monkeypatch):
    from src.services.alert_service import AlertService, AlertEvent
    config = {'database': {'sqlite_path': db_path}}
    now = datetime(2026, 9, 18, 16)
    with get_db_session(db_path) as session:
        for code, status, expiry, action in [('600519', 'active', '2026-09-21', 'sell'),
                ('000001', 'active', '2026-09-17', 'sell'), ('000002', 'replaced', '2026-09-21', 'sell')]:
            session.add(DecisionSignal(code=code, status=status, expires_on=expiry, action=action,
                diagnosis_id=7, trade_date='2026-09-14', invalidation='api_key=private-secret'))
    summary = DecisionSignalService(config).active_summary(['sh600519', '000001', '000002'], now)
    assert set(summary['signals']) == {'600519'}
    assert summary['signals']['600519']['defensive']
    assert 'private-secret' not in json.dumps(summary)
    service = AlertService(config)
    event = AlertEvent('600519', '样本', 'stop_loss', 'warning', '止损提醒')
    service._attach_signals([event], now)
    service._save([(event, '')], False, now)
    with get_db_session(db_path) as session:
        record = session.query(AlertRecord).one()
        context = json.loads(record.signal_context_json)
        assert context['signal']['diagnosis_id'] == 7
        assert '#7' in record.message and '2026-09-21' in record.message
        session.query(DecisionSignal).delete()
    with get_db_session(db_path) as session:
        assert json.loads(session.query(AlertRecord).one().signal_context_json) == context
    def broken(*a, **k):
        raise RuntimeError('database unavailable')
    monkeypatch.setattr('src.services.decision_signals.get_db_session', broken)
    assert DecisionSignalService(config).active_summary(['600519'], now)['status'] == 'unknown'
    event = AlertEvent('600519', '样本', 'stop_loss', 'warning', '止损提醒')
    service._attach_signals([event], now)
    assert event.signal_context['status'] == 'unknown' and '未知' in event.message


def test_risk_marks_only_held_active_defensive_signal(db_path, monkeypatch):
    from src.services.portfolio_risk import PortfolioRiskService
    from src.services.real_portfolio import RealPortfolioService
    monkeypatch.setattr('src.utils.timestamps.quote_now', lambda: datetime(2026, 9, 18, 16))
    config = {'database': {'sqlite_path': db_path}, 'risk': {}, 'trading': {}}
    real = RealPortfolioService(config)
    real.add_cash_flow('2026-09-14', 'in', 200)
    real.add_trade('2026-09-15', '600519', '买入', 10, 10)
    with get_db_session(db_path) as session:
        session.add_all([DecisionSignal(code=code, action='reduce', status='active', diagnosis_id=7,
            trade_date='2026-09-14', expires_on='2026-09-21') for code in ('600519', '000001')])
    monkeypatch.setattr(PortfolioRiskService, '_regime_limit', lambda s: ('未知', None))
    result = PortfolioRiskService(config, account='real').report()
    assert result['positions'][0]['defensive_signal'] is True
    assert set(result['decision_signals']['signals']) == {'600519'}


def test_batch_sources_and_health_time_are_independent(db_path):
    from src.services.data_capabilities import data_center
    from src.collectors.source_chain import source_health
    with get_db_session(db_path) as session:
        session.add_all([StockDaily(code=code, trade_date='2026-09-18', close=10,
            source=source, updated_at=datetime(2026, 9, 18, 16))
            for code, source in [('600519', 'local-a'), ('000001', 'local-b')]])
    source_health.record('个股日线', '腾讯', True, persist=False)
    result = data_center({'database': {'sqlite_path': db_path}})
    stock = result['snapshots'][0]
    assert stock['mixed_sources'] and stock['rows_on_date'] == 2
    assert {s['source'] for s in stock['sources']} == {'local-a', 'local-b'}
    assert all(s['last_fetched_at'] == '2026-09-18T16:00:00' for s in stock['sources'])
    assert all(m['fetched_at'] is None for m in result['matrix'])
    bao = next(m for m in result['matrix'] if m['provider'] == 'baostock')
    assert 'BJ' not in bao['exchanges'] and bao['scope'] == 'symbol'
    cs = next(m for m in result['matrix'] if m['provider'] == 'csindex')
    assert cs['asset_kinds'] == ['index'] and cs['adjustment'] == 'none'


def test_source_capture_isolated_bounded_and_freezes_late_workers():
    from src.services.screening_sources import capture_sources, source_attempt
    from src.collectors.request_budget import bounded_call
    def work(label):
        with capture_sources() as capture:
            bounded_call(lambda: source_attempt('季度基本面', {'source': label, 'ok': False, 'error': 'token=private-secret'}), 1)
            source_attempt('季度基本面', {'source': label + '-backup', 'ok': True})
            frozen = capture.finish()
            capture.append('late', {'source': 'late', 'ok': True})
            return frozen
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(work, ['a', 'b']))
    assert results[0]['run_id'] != results[1]['run_id']
    for label, result in zip(['a', 'b'], results):
        assert [r['source'] for r in result['attempts']] == [label, label + '-backup']
        assert result['fallback_datasets'] == ['季度基本面']
        assert result['success'] == result['failure'] == 1
        assert 'private-secret' not in json.dumps(result)


def test_screening_source_history_survives_new_instance_and_failure(db_path, monkeypatch):
    from src.strategy.screener import StrategyScreener, ScreenResult
    from src.services.screening_sources import source_attempt
    config = {'database': {'sqlite_path': db_path}}
    def run(self, *a):
        source_attempt('季度基本面', {'source': 'sina', 'ok': False, 'error': 'timeout'})
        source_attempt('季度基本面', {'source': 'indicator', 'ok': True})
        return ScreenResult(trade_date='2026-09-18', status='partial')
    monkeypatch.setattr(StrategyScreener, '_run', run)
    result = StrategyScreener(config).run()
    history = StrategyScreener(config).source_history()
    assert history['items'][0]['sources'] == result.pipeline['sources']
    assert history['summary']['fallback_runs'] == 1
    def fail(self, *a):
        source_attempt('日线', {'source': 'sina', 'ok': False})
        raise RuntimeError('api_key=private-secret')
    monkeypatch.setattr(StrategyScreener, '_run', fail)
    with pytest.raises(RuntimeError):
        StrategyScreener(config).run()
    history = StrategyScreener(config).source_history()
    assert history['summary']['runs'] == 2
    assert history['items'][0]['status'] == 'error'
    assert history['items'][0]['sources']['failure'] == 1
    assert 'private-secret' not in json.dumps(history)


def test_observer_failure_is_nonfatal_and_flow_is_stable():
    from src.collectors.source_chain import SourceHealthRegistry
    from src.services.run_diagnostics import snapshot
    def broken(event):
        raise RuntimeError('api_key=private-secret')
    with execution_trace('trace-test', broken):
        log = RunLog()
        log.add('success', True, 5)
        SourceHealthRegistry().record('日线', 'sina', True, persist=False)
        log.data_attempt('日线', {'source': 'sina', 'ok': False, 'error': 'token=private-secret'})
    first, second = snapshot(log.to_dict()), snapshot(log.to_dict())
    assert first == second
    assert first['status'] == 'degraded'
    assert first['edges'][0]['from'] == 'trace-test:step:1'
    assert 'private-secret' not in first['copy_text']
    assert first['nodes'][0]['started_at'] < first['nodes'][0]['ended_at']
    assert snapshot()['status'] == 'unknown'


def test_failed_task_flow_survives_restart_and_is_bounded(db_path):
    from api.tasks import TaskManager
    from src.services.run_diagnostics import snapshot
    manager = TaskManager(workers=1, db_path=db_path)
    def fail(progress):
        for i in range(205):
            progress({'type': 'step', 'name': f'阶段 {i}', 'ok': True})
        raise RuntimeError('token=private-secret')
    task = manager.submit('audit', fail)
    manager._pool.shutdown(wait=True)
    restored = TaskManager(workers=1, db_path=db_path)
    result = restored.get(task['id'])
    restored._pool.shutdown(wait=True)
    flow = snapshot(task=result)
    assert flow['status'] == 'failed' and flow['truncated']
    assert len(flow['events']) == 200 and len({n['id'] for n in flow['nodes']}) == 200
    assert 'private-secret' not in json.dumps(flow)
