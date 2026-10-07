"""隔离浏览器验收服务：临时数据库、合成行情，关闭调度、模型、交易和通知。"""
import sys, json, tempfile, yaml
import os, shutil
from pathlib import Path
from datetime import datetime, timedelta
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.database.db import init_db, get_db_session
from src.database.models import StockDaily, StockInfo, StockDiagnosis, Watchlist
from src.services.research_artifact import build_context_pack
from src.services.intelligence import IntelligenceService
from src import trading_calendar, settings_store
from api.app import create_app
from api.auth import AuthStore
import uvicorn

folder = Path(tempfile.mkdtemp(prefix='quant-alignment-ui-'))
example = yaml.safe_load((ROOT / 'config/settings.yaml.example').read_text())
(folder / 'config').mkdir()
for filename in ('scoring_profiles.yaml.example', 'screening_rules.yaml.example', 'stock_pool.yaml'):
    shutil.copy(ROOT / 'config' / filename, folder / 'config' / filename)
os.chdir(folder)
config = {'database': {'sqlite_path': str(folder / 'preview.db')}, 'web': {'scheduler': False}, 'llm': {}, 'risk': {}, 'trading': {}, 'alerts': {}, 'notifier': {}, 'diagnosis': {'fundamentals': False, 'shareholders': False}}
config['screening'] = example['screening']
config['screening']['pipeline'].update(financial_enrichment=False, llm_rerank=False, post_analysis_top_k=0)
init_db(config['database']['sqlite_path'])
trading_calendar.load = lambda *args, **kwargs: True
settings_store.SETTINGS_PATH = folder / 'settings.yaml'
from src import config_loader
from src.services.data_source_settings import DEFAULTS
config['data_sources'] = DEFAULTS.copy()
settings_store.SETTINGS_PATH.write_text(yaml.safe_dump(config, allow_unicode=True))
config_loader.load_config = lambda *a, **kw: {**config, **settings_store.read_settings()}
config_loader.reload_config = lambda *a, **kw: config_loader.load_config()
from src.collectors.source_chain import source_health
source_health.record('实时行情', '腾讯财经(HTTP)', True, elapsed=0.62)
source_health.record('个股日线', 'tx', True, elapsed=0.84)
from src.collectors import daily_history
import pandas as pd
preview_bars = pd.DataFrame({'date': ['2026-09-29', '2026-09-30'], 'open': [1590, 1595], 'high': [1605, 1610], 'low': [1580, 1590], 'close': [1600, 1602], 'volume': [100000, 110000], 'amount': [160000000, 176220000]})
daily_history.DAILY_SOURCES['tencent'] = ('tx', lambda *a: preview_bars)

with get_db_session(config['database']['sqlite_path']) as session:
    session.add(StockInfo(code='600519', name='研究验收示例'))
    session.add(Watchlist(code='600519', name='研究验收示例'))
    session.add(StockInfo(code='000001', name='平安银行'))
    session.add(StockInfo(code='601318', name='中国平安'))
    session.add(Watchlist(code='000001', name='银行验收示例'))
    session.add(StockDaily(code='000001', name='银行验收示例', trade_date='2026-09-30', open=12, high=12.5, low=11.8, close=12.2, volume=500000, amount=6100000, change_pct=1.67, source='模拟行情', price_adjustment='none'))
    for index in range(95):
        day = datetime(2026, 6, 25) + timedelta(days=index)
        if day.weekday() < 5:
            session.add(StockDaily(code='600519', name='研究验收示例', trade_date=day.strftime('%Y-%m-%d'), open=1500 + index, close=1505 + index, high=1510 + index, low=1495 + index, change_pct=0.4, volume=100000, amount=150000000, source='模拟行情', price_adjustment='forward'))
    context = {'code': '600519', 'name': '研究验收示例', 'quote': {'trade_date': '2026-09-25', 'close': 1600, 'change_pct': 0.4}, 'text': '【近期走势】稳步上升\n【技术面】MA20 支撑，成交量温和\n【大盘环境】均衡\n【相关资讯】季度业绩已披露', 'data_quality': {'score': 78, 'missing': ['资金流'], 'bar_count': 68}, 'news_evidence': [{'title': '季度业绩披露', 'source': '模拟公告', 'date': '2026-09-25'}], 'phase': {'phase': 'after_close'}}
    result = {'code': '600519', 'name': '研究验收示例', 'action': 'watch', 'action_label': '观望', 'score': 68, 'confidence': '中', 'one_sentence': '趋势仍在，等待量能和最新资金数据确认。', 'risks': ['资金流数据未覆盖'], 'catalysts': ['均线保持上行'], 'created_at': '2026-10-02 08:00', 'trade_date': '2026-09-25', 'invalidation': '跌破 MA20 或出现新的风险公告时重新评估', 'guardrails': [], 'data_quality': {'score': 78, 'missing': ['资金流']}, 'trend_prediction': '趋势向上', 'battle_plan': {'buy_price': 1580, 'stop_loss': 1550, 'target_price': 1680}, 'position_advice': {'no_position': '等待放量确认后分批考虑', 'has_position': '关注 MA20 与风险公告，保留现金缓冲'}, 'phase_decision': {'immediate_action': '复核成交量与季度业绩', 'next_check_time': '下一交易日 10:00'}, 'context_pack': build_context_pack(context)}
    session.add(StockDiagnosis(code='600519', name='研究验收示例', trade_date='2026-09-25', action='watch', score=68, result_json=json.dumps(result, ensure_ascii=False)))
service = IntelligenceService(config)
source = service.save_source({'name': '模拟公告订阅', 'url': 'https://example.invalid/feed', 'symbol': '600519', 'sector': '消费', 'enabled': True})
service.ingest([{'title': '研究验收示例季度业绩披露', 'summary': '此为隔离模拟数据，不涉及真实投资建议', 'url': 'https://example.invalid/notice', 'published': datetime(2026, 9, 25)}], source)
from src.database.models import StrategyBacktest, ScreeningRun, StrategyPick
from src.strategy.screener import BACKTEST_ENGINE_VERSION
with get_db_session(config['database']['sqlite_path']) as session:
    candidate = {'trade_date': '2026-09-25', 'code': '600519', 'name': '研究验收示例', 'score': 80, 'screen_score': 82,
                 'strategies': ['balanced_alpha'], 'labels': ['均衡多因子'], 'reasons': ['隔离模拟：有效历史与量能条件满足'],
                 'close': 1600, 'change_pct': .4, 'fits_regime': True, 'amount_yi': 1.5, 'factor_scores': {'value': None, 'quality': None, 'momentum': 70},
                 'factor_coverage': .5, 'data_quality': {'score': 90, 'source': '模拟日线', 'flags': ['财务证据未覆盖']},
                 'risk_level': 'low', 'risk_penalty': 2, 'risk_flags': ['财务证据未覆盖'], 'portfolio_penalty': 0,
                 'industry': '消费', 'post_analysis': {'status': 'available', 'report_id': 1}, 'context_pack': result['context_pack']}
    session.add(StrategyPick(trade_date='2026-09-25', strategy='balanced_alpha', code='600519', name='研究验收示例', score=82, close=1600, change_pct=.4, reason=candidate['reasons'][0], metadata_json=json.dumps(candidate, ensure_ascii=False)))
    report = {'start': '2026-09-01', 'end': '2026-09-25', 'dates': 12, 'skipped_dates': 1, 'elapsed': 1.2,
              'created_at': '2026-10-02 08:00', 'status': 'partial', 'engine_version': BACKTEST_ENGINE_VERSION,
              'note': '隔离模拟验收：1 天行情覆盖不足，本次不更新排序权重',
              'methodology': '次日开盘观察入场，持有 1/3/5 个交易日后收盘观察退出；缺行情不顺延，一字涨停不假定买入。样本复利未模拟共享资金、费用及退出成交。',
              'strategies': [{'strategy': 'volume_breakout', 'label': '放量突破', 'regimes': '进攻/均衡', 'days': 12, 'picks': 40,
                              'evaluated': 35, 'unavailable': 3, 'entry_blocked': 2, 'avg_1d': 0.8, 'win_1d': 54.3,
                              'avg_3d': 1.2, 'avg_5d': 1.5, 'limit_up_rate': 8.6, 'avg_1d_fit': 0.9,
                              'total_return': 2.4, 'max_drawdown': -1.2, 'weight': 1}], 'weights': {}}
    session.add(StrategyBacktest(start_date=report['start'], end_date=report['end'], result_json=json.dumps(report, ensure_ascii=False)))
    session.add(ScreeningRun(trade_date='2026-09-30', status='partial', result_json=json.dumps({'stats': {'universe': 2, 'with_history': 1},
                              'notes': ['隔离模拟验收：行情覆盖与历史质量不足，保留上次成功选股结果；本次候选不进入 AI 预测']} , ensure_ascii=False)))
# 两份相反结论的报告，用于验证历史研究概览绑定报告身份。
with get_db_session(config['database']['sqlite_path']) as session:
    newer = {**result, 'created_at': '2026-10-02 09:00', 'action': 'avoid', 'action_label': '回避', 'score': 30, 'one_sentence': '新证据出现后回避，历史报告应保持原观点。'}
    session.add(StockDiagnosis(code='600519', name='研究验收示例', trade_date='2026-09-25', action='avoid', score=30, result_json=json.dumps(newer, ensure_ascii=False)))
app = create_app(config, start_scheduler=False, static_dir=ROOT / 'apps/web/dist', auth=AuthStore(folder / 'auth.json'))
source_health.record('实时行情', '模拟腾讯', True, elapsed=.2)
source_health.record('实时行情', '模拟失败源', False, '模拟连接超时；保留后备源', elapsed=15)
app.state.pipeline.collect = lambda: {'all_sources_ok': False, 'missing_sources': ['market.fund_flow'], 'news': {'cailianshe': 3}, 'status': 'partial'}
from src.services.stock_chat import StockChatSession
class OfflineLLM:
    def chat_stream(self, user_message, system_message='', **kwargs):
        yield json.dumps({'answer': '隔离问股回答：已按指定范围完成只读研究。'}, ensure_ascii=False)
app.state.chat_store._factory = lambda cfg: StockChatSession(cfg, llm=OfflineLLM())
uvicorn.run(app, host='127.0.0.1', port=int(sys.argv[1]) if len(sys.argv) > 1 else 8766, log_level='warning')
