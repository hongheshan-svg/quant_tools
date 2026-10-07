"""隔离审计复现：只写临时库，不调用外网、模型、交易或推送。"""
import json
import sys
import tempfile
from pathlib import Path
from datetime import datetime
from types import SimpleNamespace

ROOT = Path.cwd()
sys.path.insert(0, str(ROOT))
import pandas as pd
from pytest import MonkeyPatch
from loguru import logger
from src.database import db
from src.database.db import init_db, get_db_session
from src.database.models import StockInfo, StockDaily, FundDaily, DecisionSignal
from src.services.web_intent import resolve
from src.services.stock_search import StockSearch
from src.services.stock_diagnosis import StockDiagnosisService
from src.services.data_freshness import daily_quality
from src.services.real_portfolio import RealPortfolioService
from src.services.portfolio_risk import PortfolioRiskService
from src.services.etf_rotation import ETFRotationService
from src.services.data_capabilities import data_center

logger.remove()
logger.add(sys.stderr, level='WARNING')
results = {}
phase = {'phase': 'after_close', 'label': '盘后', 'now': '2026-09-18 16:00', 'effective_daily_bar_date': '2026-09-18', 'is_partial_bar': False}

def reset():
    if db._engine:
        db._engine.dispose()
    db._engine = db._SessionFactory = None
    StockSearch.reset()

with tempfile.TemporaryDirectory(prefix='quant-dsa-audit-probe-') as folder, MonkeyPatch.context() as patch:
    patch.setattr('src.trading_calendar.load', lambda *a, **k: True)
    patch.setattr('src.trading_calendar.is_trade_day', lambda value: pd.Timestamp(value).weekday() < 5)
    patch.setattr('src.trading_calendar.trade_days_only', lambda days: [day for day in days if pd.Timestamp(day).weekday() < 5])
    patch.setattr('src.trading_calendar.has_calendar_coverage', lambda *a: True)
    patch.setattr('src.trading_calendar.next_trade_day', lambda value: (pd.Timestamp(value) + pd.offsets.BDay()).date())
    patch.setattr('src.trading_calendar.prev_trade_day', lambda value: (pd.Timestamp(value) - pd.offsets.BDay()).date())
    patch.setattr('src.services.market_phase.current_phase', lambda: phase)
    names = [('600519', '贵州茅台', (), 'stock'), ('000001', '平安银行', (), 'stock'), ('601318', '中国平安', (), 'stock')]
    patch.setattr(StockSearch, '_ensure_index', lambda self: names)
    pending = resolve('分析平安', 'unused', stock_context={'code': '600519'})
    confirmed = resolve('000001', 'unused', pending['state'], stock_context={'code': '600519'})
    results['intent_scope_after_confirmation'] = {'requested_scope': '600519', 'initial_confirmation': pending['requires_confirmation'], 'confirmed_tasks': confirmed['tasks'], 'requires_confirmation': confirmed['requires_confirmation']}
    results['intent_order_in_same_clause'] = {'question': '先看看持仓再分析贵州茅台', 'task_order': [task['kind'] for task in resolve('先看看持仓再分析贵州茅台', 'unused')['tasks']]}

    config = {'database': {'sqlite_path': str(Path(folder) / 'ledger.db')}, 'risk': {}, 'diagnosis': {}}
    reset()
    init_db(config['database']['sqlite_path'])
    with get_db_session(config['database']['sqlite_path']) as session:
        session.add(StockInfo(code='600519', name='隔离示例'))
        for day in ['2026-09-15', '2026-09-18']:
            session.add(StockDaily(code='600519', trade_date=day, close=10, source='audit-local-source', price_adjustment='unadjusted', updated_at=datetime.fromisoformat(day + 'T16:00:00')))
    quality = {'score': 100, 'core_ok': True, 'bar_count': 80, 'missing': []}
    context = {'code': '600519', 'name': '贵州茅台', 'phase': phase, 'quote': {'trade_date': '2026-09-18', 'close': 10, 'updated_at': '2026-09-18T14:00:00+08:00'}, 'regime': SimpleNamespace(regime='主升', position_factor=1, summary=lambda: {}), 'data_quality': quality, 'tech': SimpleNamespace(risks=[]), 'role': {}}
    diagnosis = StockDiagnosisService(config)._apply_guardrails({'action': 'buy', 'score': 90, 'confidence': '高'}, context)
    results['diagnosis_cached_before_close'] = {'daily_quality': daily_quality(context['quote']['trade_date'], context['quote']['updated_at'], phase=phase), 'diagnosis_action': diagnosis['action'], 'confidence': diagnosis['confidence'], 'guardrails': diagnosis['guardrails'], 'phase_limitations': diagnosis['phase_decision']['data_limitations']}
    results['future_acquisition_timestamp'] = daily_quality('2026-09-18', '2026-10-25T16:00:00+08:00', phase=phase)

    real = RealPortfolioService(config)
    assert real.add_cash_flow('2026-09-14', 'in', 200)['ok']
    assert real.add_trade('2026-09-15', '600519', '买入', 10, 10)['ok']
    assert real.add_corporate_action('2026-09-16', '600519', 'dividend', cash=10)['ok']
    replay = PortfolioRiskService(config, account='real')._drawdown()
    results['corporate_action_date_without_quotes'] = {'live_cash': real.cash(), 'expected_final_assets_at_price_10': real.cash() + 100, 'drawdown': replay, 'known_missing_trade_days': ['2026-09-16', '2026-09-17']}
    patch.setattr(PortfolioRiskService, '_regime_limit', lambda self: ('均衡', 100))
    with get_db_session(config['database']['sqlite_path']) as session:
        session.add(DecisionSignal(code='600519', name='隔离示例', action='sell', score=20, confidence='高', trade_date='2026-09-18', expires_on='2026-10-30', horizon_days=30, status='active', created_at=datetime(2026, 9, 18, 16)))
    risk = PortfolioRiskService(config, account='real').report()
    results['defensive_signal_missing_from_portfolio'] = {'seeded_active_signal': 'sell', 'positions': risk['positions'], 'warnings': risk['warnings'], 'report_keys': sorted(risk)}
    center = data_center(config)
    results['data_center_actual_source'] = {'seeded_source': 'audit-local-source', 'daily_snapshot': next(item for item in center['snapshots'] if item['dataset'] == '个股日线')}

    reset()
    config['database']['sqlite_path'] = str(Path(folder) / 'rotation.db')
    config['etf_rotation'] = {'risk_assets': ['510300'], 'safe_asset': None, 'start': '2026-08-03', 'end': '2026-09-18', 'lookback_days': 5, 'min_years': 8}
    init_db(config['database']['sqlite_path'])
    with get_db_session(config['database']['sqlite_path']) as session:
        for i, day in enumerate(pd.bdate_range('2026-08-03', '2026-09-17')):
            session.add(FundDaily(code='510300', trade_date=day.date().isoformat(), close=10 * 1.003 ** i, source='audit-local-source', price_adjustment='forward', updated_at=day.to_pydatetime().replace(hour=16)))
    rotation = ETFRotationService(config).run()
    results['rotation_missing_cutoff'] = {key: rotation[key] for key in ['status', 'as_of', 'limitations', 'parameters', 'next_action', 'metrics']}
    results['rotation_missing_cutoff']['curve_end'] = rotation['curve'][-1]['date']
    with get_db_session(config['database']['sqlite_path']) as session:
        session.add(FundDaily(code='510300', trade_date='2026-09-18', close=11.4, source='audit-local-source', price_adjustment='forward', updated_at=datetime(2026, 9, 18, 14)))
    rotation = ETFRotationService(config).run()
    results['rotation_cached_before_close'] = {key: rotation[key] for key in ['status', 'as_of', 'limitations', 'next_action', 'ranking']}
    patch.setattr('src.trading_calendar.has_calendar_coverage', lambda *a: False)
    rotation = ETFRotationService(config).run()
    results['rotation_calendar_unavailable'] = {key: rotation[key] for key in ['status', 'limitations', 'metrics', 'next_action']}
    patch.setattr('src.trading_calendar.has_calendar_coverage', lambda *a: True)
    config['etf_rotation']['safe_asset'] = '511880'
    with get_db_session(config['database']['sqlite_path']) as session:
        rows = session.query(FundDaily).filter(FundDaily.code == '510300').order_by(FundDaily.trade_date).all()
        for i, row in enumerate(rows):
            row.close = 10 * .997 ** i
        for day in pd.bdate_range('2026-08-03', '2026-09-17'):
            session.add(FundDaily(code='511880', trade_date=day.date().isoformat(), close=1, source='audit-local-source', price_adjustment='forward', updated_at=day.to_pydatetime().replace(hour=16)))
    rotation = ETFRotationService(config).run()
    results['rotation_defensive_latest_quote_missing'] = {key: rotation[key] for key in ['status', 'as_of', 'limitations', 'next_action', 'ranking']}
    results['rotation_defensive_latest_quote_missing']['safe_asset_last_quote'] = '2026-09-17'
    reset()

path = Path('/tmp/quant-dsa-followup-probe.json')
path.write_text(json.dumps(results, ensure_ascii=False, indent=2, allow_nan=False) + '\n')
print(json.dumps(results, ensure_ascii=False, indent=2, allow_nan=False))
