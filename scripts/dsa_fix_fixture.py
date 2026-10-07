"""14 类修复的浏览器验收数据；只能在隔离 smoke fixture 启动时显式调用。"""
import json
from datetime import datetime, timedelta
from src.database.db import get_db_session
from src.database.models import StockDiagnosis, StockDaily, FundDaily, TradeCalendar
from src.services.run_log import RunLog, execution_trace
from src.services.screening_sources import capture_sources, source_attempt, local_snapshot
from src.strategy.screener import StrategyScreener, ScreenResult


def seed(app):
    config = app.state.pipeline.config
    path = config['database']['sqlite_path']
    with execution_trace('isolated-fix-acceptance'):
        log = RunLog(activate=False)
        log.data_attempt('个股日线', {'source': '模拟首选', 'ok': False, 'ms': 12, 'error': '模拟超时'})
        log.data_attempt('个股日线', {'source': '模拟后备', 'ok': True, 'ms': 5})
        log.llm('决策', '隔离模型', True, 4)
        log._append('报告保存', True, 1, '报告 #1', 'save')
    with get_db_session(path) as session:
        row = session.get(StockDiagnosis, 1)
        result = json.loads(row.result_json)
        result['run_log'] = log.to_dict()
        row.result_json, row.run_log = json.dumps(result, ensure_ascii=False), json.dumps(result['run_log'], ensure_ascii=False)
        session.add(StockDaily(code='000002', trade_date='2026-09-30', close=10, source='模拟第二来源', updated_at=datetime(2026, 9, 30, 16)))
        session.query(TradeCalendar).delete()
        for i in range(60):
            day = datetime(2026, 9, 1) + timedelta(days=i)
            if day.weekday() < 5:
                session.add(TradeCalendar(trade_date=day.date().isoformat()))
                if day <= datetime(2026, 9, 18):
                    for code in ('510300', '511880'):
                        if code == '511880' and day.day == 18:
                            continue
                        session.add(FundDaily(code=code, trade_date=day.date().isoformat(), close=10 - i * .1 if code == '510300' else 1,
                            source='模拟前复权', price_adjustment='forward', updated_at=day.replace(hour=16)))
    from src import trading_calendar
    with get_db_session(path) as session:
        trading_calendar._set_days({row[0] for row in session.query(TradeCalendar.trade_date)})
    with capture_sources() as capture:
        source_attempt('季度基本面', {'source': '模拟首选', 'ok': False, 'error': '模拟超时', 'ms': 20})
        source_attempt('季度基本面', {'source': '模拟后备', 'ok': True, 'ms': 5})
        with get_db_session(path) as session:
            local_snapshot(session.query(StockDaily).filter_by(trade_date='2026-09-30').all())
        result = ScreenResult(trade_date='2026-09-30', status='partial')
        result.pipeline['sources'] = capture.finish()
        StrategyScreener(config)._record_run(result, 'partial')
    def diagnostic_task(progress):
        progress({'type': 'step', 'name': '隔离取数', 'ok': True})
        progress({'type': 'step', 'name': '模拟失败步骤', 'ok': False, 'detail': '用于验证历史恢复'})
        raise RuntimeError('隔离任务预期失败')
    app.state.tasks.submit('audit', diagnostic_task, label='隔离运行流验收')
