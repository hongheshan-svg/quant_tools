"""审计 F01/F02/F11/F12：时效、后验单一计算路径及样本生命周期。"""

from datetime import datetime

import pytest

from src.database.db import get_db_session
from src.database.models import StockDaily, StockDiagnosis, SkillOpinion, ResearchOutcome, DecisionSignal
from src.services.data_query_service import DataQueryService
from src.services.data_freshness import daily_quality
from src.services.outcome_engine import OutcomeEngine
from src.services.skill_consult import SkillOpinionService
from src.services.decision_signals import DecisionSignalService
from src.services.market_phase import phase_guardrails
from src.services.research_artifact import build_context_pack
from tests.test_decision_signals import db_path, calendar  # noqa: F401


def bar(day, value, basis='none', hour=16):
    return StockDaily(code='600519', trade_date=day, close=value, high=value, low=value,
                      price_adjustment=basis, updated_at=datetime.fromisoformat(day).replace(hour=hour))


@pytest.mark.parametrize('day,value,basis,now,reason', [
    ('2026-09-15', 12, 'none', datetime(2026, 9, 14, 16), 'insufficient_daily_bars'),
    ('2026-09-16', 12, 'none', datetime(2026, 9, 16, 16), 'missing_trading_day'),
    ('2026-09-15', 5, 'forward', datetime(2026, 9, 15, 16), 'incomparable_price_basis'),
])
def test_signal_compatibility_fields_respect_shared_window(db_path, day, value, basis, now, reason):
    config = {'database': {'sqlite_path': db_path}}
    with get_db_session(db_path) as session:
        session.add_all([bar('2026-09-14', 10), bar(day, value, basis)])
        signal = DecisionSignal(code='600519', trade_date='2026-09-14', action='buy',
                                horizon_days=5, status='active', target_price=11)
        session.add(signal)
        session.flush()
        sid = signal.id
    service = DecisionSignalService(config)
    service.evaluate(now)
    result = service.get(sid)
    assert result['ret_1d'] is None and result['status'] == 'active'
    assert service.is_hit(result) is None and service.stats()['hit_rate'] is None
    assert OutcomeEngine(config).list('signal', sid)[0]['reason'] == reason


def test_intraday_outcome_waits_for_completed_bar(db_path):
    engine = OutcomeEngine({'database': {'sqlite_path': db_path}})
    with get_db_session(db_path) as session:
        session.add_all([bar('2026-09-14', 10), bar('2026-09-15', 12, hour=11)])
    with get_db_session(db_path) as session:
        first = engine.evaluate(session, 'skill', 1, '600519', '2026-09-14', 1,
                                now=datetime(2026, 9, 15, 11), horizons=(1,))[0]
        assert first.status == 'pending' and first.return_pct is None
        stale = engine.evaluate(session, 'skill', 1, '600519', '2026-09-14', 1,
                                now=datetime(2026, 9, 15, 16), horizons=(1,))[0]
        assert stale.status == 'pending'
        row = session.query(StockDaily).filter_by(trade_date='2026-09-15').one()
        row.close, row.updated_at = 9, datetime(2026, 9, 15, 16)
    with get_db_session(db_path) as session:
        final = engine.evaluate(session, 'skill', 1, '600519', '2026-09-14', 1,
                                now=datetime(2026, 9, 15, 16), horizons=(1,))[0]
        assert final.status == 'evaluated' and final.return_pct == -10 and final.hit is False


def test_diagnosis_and_evidence_reject_cached_intraday_bar():
    phase = {'phase': 'postmarket', 'now': '2026-09-18 16:00', 'effective_daily_bar_date': '2026-09-18'}
    stamp = '2026-09-18T14:00:00'
    action, confidence, _, _ = phase_guardrails('buy', '高', {}, phase, '2026-09-18', fetched_at=stamp)
    assert action == 'watch' and confidence != '高'
    assert 'future_fetched_at' in daily_quality('2026-09-18', '2026-10-25T16:00', phase=phase)['limitations']
    pack = build_context_pack({'code': '600519', 'phase': phase,
        'quote': {'close': 10, 'trade_date': '2026-09-18', 'updated_at': stamp},
        'daily': {'bar_count': 60}, 'technical': {'score': 80}})
    assert all(pack['blocks'][key]['status'] == 'stale' for key in ('quote', 'daily', 'technical'))


@pytest.mark.parametrize('batch', [False, True])
def test_delete_cleans_owned_samples_and_rejects_late_insert(db_path, batch):
    config = {'database': {'sqlite_path': db_path}}
    with get_db_session(db_path) as session:
        session.add_all([StockDiagnosis(id=1, code='600519'), StockDiagnosis(id=2, code='000001')])
        session.add(DecisionSignal(code='600519', diagnosis_id=1, action='sell'))
    service = SkillOpinionService(config)
    opinion = {'skill': 'volume_breakout', 'stance': '看多', 'score': 80, 'confidence': '高'}
    assert service.record(1, '600519', '样本', '2026-09-14', [opinion]) == 1
    with get_db_session(db_path) as session:
        sample = session.query(SkillOpinion).one()
        session.add_all([ResearchOutcome(owner_type='skill', owner_id=sample.id, horizon=5, engine_version='audit'),
                         ResearchOutcome(owner_type='diagnosis', owner_id=1, horizon=1, engine_version='audit')])
    query = DataQueryService(db_path)
    assert (query.delete_diagnoses(ids=[1]) if batch else query.delete_diagnosis(1)) == 1
    assert service.record(1, '600519', '迟到', '2026-09-14', [{**opinion, 'skill': 'bull_trend'}]) == 0
    with get_db_session(db_path) as session:
        assert session.query(SkillOpinion).count() == session.query(ResearchOutcome).count() == 0
        assert session.get(StockDiagnosis, 2) is not None
        assert session.query(DecisionSignal).one().diagnosis_id is None


def test_nav_retains_dividend_and_missing_whole_sessions(db_path, monkeypatch):
    from src.services.real_portfolio import RealPortfolioService
    from src.services.portfolio_risk import PortfolioRiskService
    config = {'database': {'sqlite_path': db_path}, 'risk': {}, 'trading': {}}
    monkeypatch.setattr('src.services.market_phase.current_phase', lambda: {'effective_daily_bar_date': '2026-09-18'})
    real = RealPortfolioService(config)
    real.add_cash_flow('2026-09-14', 'in', 200)
    real.add_trade('2026-09-15', '600519', '买入', 10, 10)
    real.add_corporate_action('2026-09-16', '600519', 'dividend', cash=10)
    with get_db_session(db_path) as session:
        session.add_all([bar('2026-09-15', 10), bar('2026-09-18', 10)])
    result = PortfolioRiskService(config, account='real')._drawdown()
    assert result['days'] == 5
    assert result['quality']['samples'][-1]['assets'] == 210
    assert result['quality']['status'] == 'partial'
    assert 'missing_historical_price' in result['quality']['limitations']
    assert result['current_drawdown'] is None


@pytest.mark.parametrize('case', ['missing_tail', 'intraday_tail', 'safe_missing', 'no_calendar'])
def test_rotation_calendar_cutoff_and_short_history(db_path, monkeypatch, case):
    from src.database.models import FundDaily
    from src.services.etf_rotation import ETFRotationService
    from src import trading_calendar
    import pandas as pd
    monkeypatch.setattr(trading_calendar, 'load', lambda *a, **k: True)
    if case == 'no_calendar':
        monkeypatch.setattr(trading_calendar, 'has_calendar_coverage', lambda *a: False)
    config = {'database': {'sqlite_path': db_path}, 'etf_rotation': {
        'risk_assets': ['510300'], 'safe_asset': '511880', 'start': '2026-09-01',
        'end': '2026-09-18', 'lookback_days': 2}}
    with get_db_session(db_path) as session:
        for i, day in enumerate(pd.bdate_range('2026-09-01', '2026-09-18')):
            for code in ('510300', '511880'):
                if day.day == 18 and (case == 'missing_tail' or case == 'safe_missing' and code == '511880'):
                    continue
                stamp = day.to_pydatetime().replace(hour=14 if case == 'intraday_tail' and day.day == 18 else 16)
                session.add(FundDaily(code=code, trade_date=day.date().isoformat(), close=10 - .1 * i if code == '510300' else 1,
                                     source='fixture', price_adjustment='forward', updated_at=stamp))
    if case == 'no_calendar':
        with pytest.raises(ValueError, match='交易日历'):
            ETFRotationService(config).run()
        return
    report = ETFRotationService(config).run()
    assert report['as_of'] == report['curve'][-1]['date'] == '2026-09-18'
    assert report['performance_available'] is False
    assert not any(report[key] for key in ('metrics', 'benchmark_metrics', 'annual_returns', 'trades', 'parameter_sweep'))
    assert report['next_action']['weights'] == {'CASH': 1}
    assert report['next_action']['execution_confirmed'] is False
    assert '511880' in report['curve'][-1]['missing_prices']


def test_legacy_signal_results_do_not_leak_into_current_statistics(db_path):
    service = DecisionSignalService({'database': {'sqlite_path': db_path}})
    with get_db_session(db_path) as session:
        row = DecisionSignal(code='600519', trade_date='2026-09-14', action='buy', status='hit_target',
                             ret_5d=20, max_adverse_pct=-1, evaluation_version='daily-return-v2')
        session.add(row)
        session.flush()
        sid = row.id
    old = service.get(sid)
    assert old['evaluation_current'] is False and old['ret_5d'] is None
    assert service.is_hit(old) is None and service.stats()['hit_rate'] is None
    assert service.stats()['avg_ret_5d'] is None


@pytest.mark.parametrize('status', ['closed', 'invalidated', 'replaced'])
def test_manual_or_replaced_signal_is_not_reactivated_or_evaluated(db_path, status):
    service = DecisionSignalService({'database': {'sqlite_path': db_path}})
    with get_db_session(db_path) as session:
        session.add_all([bar('2026-09-14', 10), bar('2026-09-15', 12)])
        session.add(DecisionSignal(code='600519', trade_date='2026-09-14', action='buy', status=status))
    assert service.evaluate(datetime(2026, 9, 15, 16))['evaluated'] == 0
    with get_db_session(db_path) as session:
        row = session.query(DecisionSignal).one()
        assert row.status == status and row.evaluated_at is None
        assert session.query(ResearchOutcome).count() == 0
