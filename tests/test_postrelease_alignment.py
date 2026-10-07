"""v1.2.0 发布后复核的八类问题回归，使用临时库与合成交易日。"""
import hashlib
import json
from datetime import datetime, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from src import trading_calendar
from src.database.db import get_db_session
from src.database.models import DecisionSignal, FundDaily, ResearchOutcome, SkillOpinion, StockDaily
from src.services import market_phase
from src.services.data_freshness import daily_quality
from src.services.decision_signals import DecisionSignalService
from src.services.outcome_engine import OutcomeEngine
from src.services.real_portfolio import RealPortfolioService
from src.services.research_artifact import local_context_pack
from src.services.skill_consult import SkillOpinionService
from src.services.stock_profile import StockProfileService
from src.services.strategy_skills import get_skill
from src.services.web_intent import resolve
from tests.test_decision_signals import db_path, calendar  # noqa: F401
from tests.test_web_intent_stages import names  # noqa: F401


def config(path):
    return {'database': {'sqlite_path': path}, 'risk': {}, 'alerts': {}}


def test_context_pack_uses_same_market_clock_as_quality(db_path, monkeypatch):
    phase = market_phase._real_current_phase(datetime(2026, 9, 30, 16))
    monkeypatch.setattr(market_phase, 'current_phase', lambda: phase)
    with get_db_session(db_path) as session:
        session.add(StockDaily(code='600519', trade_date='2026-09-25', close=10,
                               updated_at=datetime(2026, 9, 25, 16)))
    pack = local_context_pack('600519', db_path)
    assert pack['phase']['effective_daily_bar_date'] == '2026-09-30'
    assert pack['blocks']['quote']['status'] == pack['blocks']['daily']['status'] == 'stale'
    assert daily_quality('2026-09-25', phase={})['limitations'] == ['market_phase_unknown']


def test_calendar_recovery_evaluates_correct_window_without_freezing_fallback(db_path):
    with get_db_session(db_path) as session:
        for day, price in [('2026-09-25', 10), ('2026-09-28', 11), ('2026-09-29', 12)]:
            session.add(StockDaily(code='600519', trade_date=day, close=price,
                updated_at=datetime.fromisoformat(day + 'T16:00:00')))
    engine = OutcomeEngine(config(db_path))
    trading_calendar._set_days(set())
    with get_db_session(db_path) as session:
        first = engine.evaluate(session, 'diagnosis', 123, '600519', '2026-09-25', 1,
                                now=datetime(2026, 9, 30, 16), horizons=(1,))[0]
        assert first.status == 'pending' and first.return_pct is None
        assert first.reason == 'calendar_unverified'
    trading_calendar._set_days({'2026-09-25', '2026-09-29', '2026-09-30'})
    with get_db_session(db_path) as session:
        final = engine.evaluate(session, 'diagnosis', 123, '600519', '2026-09-25', 1,
                                now=datetime(2026, 9, 30, 16), horizons=(1,))[0]
        assert final.status == 'evaluated' and final.end_date == '2026-09-29' and final.return_pct == 20


@pytest.mark.parametrize('strategy_version,calendar_verified', [(None, True), ('current', False), ('obsolete', True)])
def test_unverified_or_legacy_samples_never_change_weight(db_path, strategy_version, calendar_verified):
    cfg = config(db_path)
    current = hashlib.sha256(get_skill('volume_breakout').instructions.encode()).hexdigest()
    with get_db_session(db_path) as session:
        for i in range(30):
            row = SkillOpinion(code='600519', skill='volume_breakout', stance='看多', score=80,
                trade_date=f'2026-09-{i+1:02}', hit=True, ret_5d=5,
                skill_version=current if strategy_version == 'current' else strategy_version)
            session.add(row)
            session.flush()
            session.add(ResearchOutcome(owner_type='skill', owner_id=row.id, horizon=5,
                engine_version=OutcomeEngine(cfg).version, status='evaluated', hit=True, return_pct=5,
                data_quality_json=json.dumps({'calendar_verified': calendar_verified})))
    assert SkillOpinionService(cfg).weights().get('volume_breakout', 1) == 1


def test_review_reveals_unable_coverage_and_stays_observational(db_path):
    cfg = config(db_path)
    with get_db_session(db_path) as session:
        for index in range(20):
            row = DecisionSignal(code='000001', action='buy', status='expired', trade_date='2026-09-01',
                evaluation_version=OutcomeEngine(cfg).version, ret_5d=5 if index < 3 else None)
            session.add(row)
            session.flush()
            session.add(ResearchOutcome(owner_type='signal', owner_id=row.id, horizon=5,
                engine_version=OutcomeEngine(cfg).version, status='evaluated' if index < 3 else 'unable',
                hit=True if index < 3 else None, return_pct=5 if index < 3 else None,
                data_quality_json='{"calendar_verified": true}'))
    review = DecisionSignalService(cfg).review('sz000001')
    assert (review['total'], review['completed'], review['unable']) == (20, 3, 17)
    assert review['unable_rate'] == 85 and review['confidence_adjustment'] == 'observe'
    assert not review['bias'] and '不调整信心' in review['text']


@pytest.mark.parametrize('question,codes', [
    ('分析贵州茅台和平安', ['600519', '000001']), ('分析平安和贵州茅台', ['000001', '600519']),
])
def test_confirmation_preserves_resolved_targets_and_order(names, question, codes):
    plan = resolve(question, 'unused')
    final = resolve('000001', 'unused', plan['state'])
    assert not final['requires_confirmation']
    assert [t['code'] for t in final['tasks'][0]['targets']] == codes


def test_profile_supports_etf_and_excludes_expired_signals(db_path, monkeypatch):
    cfg = config(db_path)
    phase = market_phase._real_current_phase(datetime(2026, 9, 30, 16))
    monkeypatch.setattr(market_phase, 'current_phase', lambda: phase)
    monkeypatch.setattr('src.utils.timestamps.quote_now', lambda: datetime(2026, 9, 30, 16))
    with get_db_session(db_path) as session:
        session.add(FundDaily(code='510300', trade_date='2026-09-30', close=4,
                              updated_at=datetime(2026, 9, 30, 16)))
        session.add(DecisionSignal(code='600519', action='buy', status='active', trade_date='2026-09-01', expires_on='2026-09-10'))
    assert RealPortfolioService(cfg).add_trade('2026-09-25', '510300', 'buy', 3.5, 100)['ok']
    service = StockProfileService(SimpleNamespace(config=cfg, db_path=db_path))
    assert service._portfolio('510300', 'etf')['data']['held'] is True
    assert not service._signals('600519').get('data', {}).get('active')
    assert service._portfolio('sh000300', 'index')['status'] == 'unavailable'


def test_market_clock_normalizes_same_instant_and_session():
    instant = datetime(2026, 9, 30, 8, tzinfo=timezone.utc)
    shanghai = instant.astimezone(ZoneInfo('Asia/Shanghai'))
    assert market_phase._real_current_phase(instant) == market_phase._real_current_phase(shanghai)
    assert trading_calendar.in_trade_session(instant) == trading_calendar.in_trade_session(shanghai)
    assert market_phase._real_current_phase(instant)['phase'] == 'postmarket'


def test_skill_coverage_prevents_weighting_even_with_thirty_winning_outcomes(db_path):
    cfg = config(db_path)
    version = hashlib.sha256(get_skill('volume_breakout').instructions.encode()).hexdigest()
    with get_db_session(db_path) as session:
        for index in range(100):
            row = SkillOpinion(code=f'600{index:03d}', skill='volume_breakout', skill_version=version,
                trade_date='2026-09-30', stance='看多', score=75, created_at=datetime.now())
            session.add(row)
            session.flush()
            session.add(ResearchOutcome(owner_type='skill', owner_id=row.id, horizon=5,
                engine_version=OutcomeEngine(cfg).version, status='evaluated' if index < 30 else 'unable',
                hit=True if index < 30 else None, return_pct=5 if index < 30 else None,
                data_quality_json='{"calendar_verified":true}'))
    result = SkillOpinionService(cfg).performance()[0]
    assert (result['samples'], result['total'], result['unable_rate'], result['weight']) == (30, 100, 70, 1)
    assert set(result['horizons']) == {'1', '3', '5', '10'}
