"""本轮完整流程的行为回归；合成数据、离线替身及临时数据库。"""

import json
import threading
import time
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from src.collectors.request_budget import bounded_call
from src.collectors.source_chain import SourceHealthRegistry
from src.database.db import get_db_session
from src.database.models import FinancialSnapshot, ResearchCache, PriceRevision, StockDaily
from src.services.alert_service import validate_rule
from src.services.chat_context import compress_context, token_count
from src.services.strategy_synthesis import synthesize
from src.strategy.screener import StrategyScreener, Features, Pick
from src.strategy.screening_pipeline import load_profiles, profile_rule, apply_risk, rerank, diversify
from tests.test_screener import config, TRADE_DATE  # noqa: F401


def pick(code="600001", score=80):
    return Pick(code, "合成股票", ["s"], ["策略"], score, ["原因"], [score], 10, 3, 2, True, screen_score=score)


def test_request_timeout_returns_and_releases_only_after_worker_finishes():
    release, ended = threading.Event(), threading.Event()
    def blocked():
        release.wait(2)
        ended.set()
    start = time.monotonic()
    try:
        with pytest.raises(TimeoutError):
            bounded_call(blocked, .02)
        assert time.monotonic() - start < .5
        assert not ended.is_set()
    finally:
        release.set()
        assert ended.wait(1)


def test_health_persists_actual_timestamps(config):
    registry = SourceHealthRegistry()
    registry.configure(config["database"]["sqlite_path"])
    registry.record("合成日线", "test", False, "失败", .2)
    saved = registry.snapshot()[0]["last_failure"]
    restored = SourceHealthRegistry()
    restored.configure(config["database"]["sqlite_path"])
    assert restored.snapshot()[0]["last_failure"] == saved


def test_stable_alert_id_ignores_reorder_note_and_enabled():
    rule = {"type": "price_cross", "code": "600519", "direction": "above", "price": 100}
    a = validate_rule(rule)
    b = validate_rule({**rule, "note": "改备注", "enabled": False})
    assert a["id"] == b["id"]
    assert validate_rule({**a, "id": "explicit"})["id"] == "explicit"


def test_profiles_have_dynamic_scores_and_missing_financials_fail_conditions():
    profiles = load_profiles("config/scoring_profiles.yaml")
    assert len(profiles) == 10
    f = Features("600001", "样本", 10, 3, 2e8, bars=61, pe=12, pb=1, roe=15, range_20=15, ret_20=8, vol_ratio=2)
    value = next(p for p in profiles if p["name"] == "quality_value")
    score = profile_rule(f, value)[0]
    f.pe = 24
    assert profile_rule(f, value)[0] < score
    f.roe = None
    assert profile_rule(f, value) is None


def test_risk_veto_independent_of_model_score_and_unknown_quality():
    p = pick()
    p.data_quality = {"score": 40}
    p.event_risks = ["减持公告", "立案公告"]
    f = Features("600001", "样本", 10, 9, 2e8, vol_ratio=7, turnover=20)
    assert not apply_risk(p, f, {})
    assert p.excluded_by_risk and p.risk_level == "high" and p.score < 80


@pytest.mark.parametrize("rows", [[{"code": "999999", "score": 99, "reason": "x"}], [{"code": "600001", "score": float("nan"), "reason": "x"}], []])
def test_model_cannot_change_candidate_universe_or_use_invalid_scores(rows):
    p = pick()
    llm = SimpleNamespace(chat_json=lambda *a, **kw: {"rankings": rows})
    picks, status = rerank([p], {}, llm)
    assert picks == [p] and p.score == 80 and status["status"] == "fallback"


def test_concentration_penalty_keeps_unknown_sectors_independent():
    a, b, c = pick("600001", 80), pick("600002", 79), pick("600003", 78)
    a.industry = b.industry = "银行"
    output = diversify([a, b, c], {"max_same_bucket": 1, "concentration_penalty": 5})
    assert output[1] is c and b.portfolio_penalty == 5 and c.portfolio_penalty == 0


def test_financial_history_reads_immutable_snapshot_instead_of_later_cache(config):
    path = config["database"]["sqlite_path"]
    known = datetime.fromisoformat(TRADE_DATE) - timedelta(days=1)
    old = {"status": "available", "reports": [{"report_date": "2026-06-30", "roe": 10}]}
    later = {"status": "available", "reports": [{"report_date": "2026-06-30", "roe": 99}]}
    with get_db_session(path) as s:
        s.add(FinancialSnapshot(code="600001", payload_json=json.dumps(old), collected_at=known, fingerprint="a"))
        s.add(ResearchCache(key="fundamentals:600001", payload_json=json.dumps(later), updated_at=known + timedelta(days=3)))
    with get_db_session(path) as s:
        assert StrategyScreener(config)._financial_payloads(s, ["600001"], TRADE_DATE, True)["600001"] == old


def test_price_refresh_archives_revision_and_does_not_duplicate_legacy_alias(config, monkeypatch):
    from src.collectors import daily_history as m
    path = config["database"]["sqlite_path"]
    item = {"code": "600001", "name": "样本", "trade_date": TRADE_DATE, "open": 5, "close": 5, "high": 5.1, "low": 4.9, "volume": 1000, "amount": 5000, "change_pct": 0, "source": "tx", "price_adjustment": "forward"}
    monkeypatch.setattr(m, "fetch_daily_df_with_fallback", lambda *a: ("tx", object()))
    monkeypatch.setattr(m, "records_from_daily_df", lambda *a: [item])
    assert m.ensure_daily_history("600001", path, now=datetime.fromisoformat(TRADE_DATE), min_bars=1000) == 0
    assert m.ensure_daily_history("600001", path, now=datetime.fromisoformat(TRADE_DATE), refresh=True) == 1
    with get_db_session(path) as s:
        rows = s.query(StockDaily).filter(StockDaily.code.in_(["sh600001", "600001"]), StockDaily.trade_date == TRADE_DATE).all()
        assert len(rows) == 1 and rows[0].close == 5 and rows[0].price_revision
        assert s.query(PriceRevision).count() == 1


def test_llm_deliberation_preserves_original_minority_and_falls_back_on_invalid():
    opinions = [{"skill": "bull", "score": 90, "stance": "看多", "confidence": "高", "reason": "增长"},
                {"skill": "bear", "score": 10, "stance": "看空", "confidence": "高", "reason": "风险"}]
    llm = SimpleNamespace(chat_json=lambda *a, **kw: {"opinions": []})
    result = synthesize(opinions, llm=llm, config={"enabled": True})
    assert result["original_opinions"] == opinions and result["minority"]
    assert result["deliberation"]["status"] == "fallback" and result["confidence_cap"] == "低"


def test_context_compression_keeps_current_scope_and_recent_anchor():
    text, usage = compress_context(["旧消息" * 500, "[消息 recent] 最新证据"], "当前标的600519；本轮问题：风险？", 100)
    assert "600519" in text and "本轮问题" in text and "recent" in text
    assert token_count(text) <= 100 and usage["compressed"]


def test_screening_full_metadata_round_trip(config, monkeypatch):
    cfg = {**config, "screening": {"pipeline": {"enabled": True, "risk_veto_threshold": 100}, "profiles_file": "config/scoring_profiles.yaml"}}
    service = StrategyScreener(cfg)
    monkeypatch.setattr(service, "_regime", lambda *a: "均衡")
    result = service.run(TRADE_DATE)
    assert result.picks and result.picks[0].factor_scores and result.picks[0].context_pack
    restored = service.picks(TRADE_DATE)
    assert restored[0]["screen_score"] and "risk_flags" in restored[0] and restored[0]["context_pack"]


def test_compression_character_limit_preserves_question_and_recent_evidence():
    text, usage = compress_context(['旧' * 500, '[recent] 新证据' * 50], '标的600519；本轮问题：风险？', 6000, 100)
    assert len(text) <= 100 and text.endswith('标的600519；本轮问题：风险？') and '[recent]' in text
    assert usage['compressed']


def test_collection_partial_result_becomes_failed_task_with_preserved_evidence():
    from api.tasks import TaskManager, collection_result_error
    tasks = TaskManager()
    try:
        result = {'all_sources_ok': False, 'missing_sources': ['market.fund_flow'], 'news': {'cailianshe': 3}}
        task = tasks.submit('collect', lambda: result, result_error=collection_result_error)
        for _ in range(100):
            row = tasks.get(task['id'])
            if row['status'] in ('done', 'error'):
                break
            time.sleep(.01)
        assert row['status'] == 'error' and row['result'] == result and 'fund_flow' in row['error']
    finally:
        tasks.shutdown()


def test_failed_refresh_preserves_prices_and_reports_failure(config, monkeypatch):
    from src.collectors import daily_history as module
    def failed(*args):
        raise RuntimeError('供应商失败')
    monkeypatch.setattr(module, 'fetch_daily_df_with_fallback', failed)
    with get_db_session(config['database']['sqlite_path']) as session:
        count = session.query(StockDaily).count()
    with pytest.raises(RuntimeError, match='保留原有'):
        module.ensure_daily_history('600001', config['database']['sqlite_path'], refresh=True)
    with get_db_session(config['database']['sqlite_path']) as session:
        assert session.query(StockDaily).count() == count


def test_context_pack_rejects_unknown_blocks_and_marks_empty_history_missing():
    from pydantic import ValidationError
    from src.schemas.research import AnalysisContextPack
    from src.services.research_artifact import build_context_pack
    with pytest.raises(ValidationError):
        AnalysisContextPack(subject={'code': '600519'}, blocks={'invented': {'status': 'available'}})
    with pytest.raises(ValidationError):
        AnalysisContextPack(subject={'code': 'sh600519'})
    pack = build_context_pack({'code': '600519', 'data_quality': {'bar_count': 0}})
    assert pack['blocks']['daily']['status'] == 'missing'


def test_redaction_hides_full_cookie_and_secrets_before_truncation():
    from src.utils.redaction import redact_text
    value = redact_text('call failed\n- cookie: session=private; auth=another\napi_key=secret-value', 500)
    assert 'private' not in value and 'another' not in value and 'secret-value' not in value


def test_llm_deliberation_resolves_conflict_preserving_originals():
    original = [{'skill': 'bull', 'score': 90, 'stance': '看多', 'confidence': '高', 'reason': '增长'},
                {'skill': 'bear', 'score': 10, 'stance': '看空', 'confidence': '高', 'reason': '风险'}]
    revised = [{**row, 'score': 55, 'stance': '中性', 'confidence': '中', 'reason': '补证前等待'} for row in original]
    calls = []
    def answer(*args, **kwargs):
        calls.append(args)
        return {'opinions': revised}
    result = synthesize(original, llm=SimpleNamespace(chat_json=answer), config={'enabled': True, 'max_rounds': 3})
    assert len(calls) == 1 and result['original_opinions'] == original and result['revised_opinions'] == revised
    assert result['deliberation']['resolution_status'] == 'resolved' and result['original_conflicts']
    assert result['conflicts'] and result['confidence_cap'] == '低'
    assert not result['revision_projection']['conflicts']
    assert result['revision_projection']['final_signal_overridden'] is False


@pytest.mark.parametrize('change', [
    {'stance': '看多', 'score': 95, 'confidence': '高'},
    {'stance': '看空', 'score': 20, 'confidence': '高'},
    {'stance': '看空', 'score': 0, 'confidence': '中'},
])
def test_mediator_cannot_reverse_or_strengthen_opinion(change):
    original = [{'skill': 'bull', 'score': 80, 'stance': '看多', 'confidence': '中', 'reason': '增长'},
                {'skill': 'bear', 'score': 20, 'stance': '看空', 'confidence': '中', 'reason': '风险'}]
    revised = [original[0], {**original[1], **change}]
    baseline = synthesize(original)
    result = synthesize(original, llm=SimpleNamespace(chat_json=lambda *a, **kw: {'opinions': revised}), config={'enabled': True})
    assert result['deliberation']['status'] == 'fallback'
    assert result['consensus'] == baseline['consensus'] and result['confidence_cap'] == '低'


def test_benchmark_missing_is_not_zero_and_complete_uses_fixed_horizon(config, monkeypatch):
    from src.services.evaluation_protocol import benchmark_samples
    from src.database.models import FundDaily
    from src import trading_calendar
    monkeypatch.setattr(trading_calendar, 'next_trade_day', lambda day: datetime.fromisoformat(str(day)).date() + timedelta(days=1))
    with get_db_session(config['database']['sqlite_path']) as session:
        assert benchmark_samples(session, ['2026-09-28'])['avg_1d'] is None
        session.add_all([FundDaily(code='sh000300', trade_date='2026-09-29', open=100, close=101),
                         FundDaily(code='sh000300', trade_date='2026-09-30', open=101, close=102)])
    with get_db_session(config['database']['sqlite_path']) as session:
        result = benchmark_samples(session, ['2026-09-28'])
    assert result['status'] == 'available' and result['avg_1d'] == 2 and result['samples'] == 1


def test_settings_validate_and_save_profiles_atomically(tmp_path, monkeypatch):
    import copy
    import yaml
    from pathlib import Path
    from fastapi import HTTPException
    from api.v1.system import ScreeningSettingsBody, save_screening_settings
    from src.services.config_check import load_example
    from src import settings_store
    import api.app as app_module
    example = load_example()
    cfg = {'screening': copy.deepcopy(example['screening'])}
    profiles = load_profiles('config/scoring_profiles.yaml', include_disabled=True)
    rules = Path('config/screening_rules.yaml.example').read_text()
    monkeypatch.chdir(tmp_path)
    (tmp_path / 'config').mkdir()
    (tmp_path / 'config/screening_rules.yaml.example').write_text(rules)
    (tmp_path / 'config/scoring_profiles.yaml.example').write_text(yaml.safe_dump({'profiles': profiles}, allow_unicode=True))
    monkeypatch.setattr(settings_store, 'SETTINGS_PATH', tmp_path / 'config/settings.yaml')
    monkeypatch.setattr(app_module, 'apply_config', lambda *a: None)
    request = SimpleNamespace(app=None)
    bad = copy.deepcopy(profiles)
    bad[0]['weights'] = {'unknown': 1}
    with pytest.raises(HTTPException):
        save_screening_settings(ScreeningSettingsBody(screening={}, profiles=bad), request, cfg)
    assert not (tmp_path / 'config/scoring_profiles.yaml').exists()
    collision = copy.deepcopy(profiles)
    collision[0]['name'] = 'volume_breakout'
    with pytest.raises(HTTPException):
        save_screening_settings(ScreeningSettingsBody(screening={}, profiles=collision), request, cfg)
    profiles[0]['weights']['momentum'] = .7
    saved = save_screening_settings(ScreeningSettingsBody(screening={}, profiles=profiles), request, cfg)
    assert saved['profiles'][0]['weights']['momentum'] == .7
    assert not list((tmp_path / 'config').glob('*.tmp'))
    with pytest.raises(HTTPException):
        save_screening_settings(ScreeningSettingsBody(screening={'profiles_file': str(tmp_path / 'outside.yaml')}, profiles=profiles), request, cfg)


def test_outcome_refuses_price_basis_change(config, monkeypatch):
    from src.services.outcome_engine import OutcomeEngine
    from src import trading_calendar
    monkeypatch.setattr(trading_calendar, 'trade_days_only', lambda days: days)
    path = config['database']['sqlite_path']
    with get_db_session(path) as session:
        session.add_all([StockDaily(code='600519', trade_date='2026-09-28', close=100, source='tx', price_adjustment='forward', price_revision='r1'),
                         StockDaily(code='600519', trade_date='2026-09-29', close=50, source='em', price_adjustment='none', price_revision='r2')])
    with get_db_session(path) as session:
        engine = OutcomeEngine(config)
        base, bars, _ = engine.price_path(session, '600519', '2026-09-28', datetime(2026, 9, 30))
    assert base == 100 and bars == [] and engine._path_failure == 'incomparable_price_basis'
    assert engine._price_quality['base_revision'] == 'r1'


def test_serious_notice_veto_cannot_be_overridden_by_model_score():
    p = pick(score=100)
    p.event_risks = ['公司收到立案调查通知']
    f = Features('600001', '样本', 10, 0, 2e8)
    assert not apply_risk(p, f, {'risk_veto_threshold': 100})
    assert p.excluded_by_risk and '重大公告风险否决' in p.risk_flags


def test_candidate_notice_enrichment_preserves_local_evidence_and_reports_failure(config, monkeypatch):
    from src.collectors import stock_news
    service = StrategyScreener({**config, 'screening': {'pipeline': {'enabled': True}}})
    p = pick()
    p.context_pack = {'blocks': {'news': {'items': {'evidence': {'value': [{'title': '本地已知新闻'}]}}}}}
    from src.strategy.screener import ScreenResult
    result = ScreenResult(trade_date=TRADE_DATE)
    monkeypatch.setattr(stock_news, 'get_stock_news', lambda *a, **kw: {'news': [], 'notices': [], 'states': {'news': 'fetch_failed', 'notices': 'fetch_failed'}})
    service._enrich_candidate_context([p], result)
    assert p.context_pack['blocks']['news']['status'] == 'partial'
    assert p.context_pack['blocks']['news']['items']['evidence']['value'][0]['title'] == '本地已知新闻'
    assert p.context_pack['blocks']['notices']['status'] == 'fetch_failed' and result.pipeline['candidate_context']['failed'] == 1


def test_partial_screening_never_calls_candidate_network_or_model(config, monkeypatch):
    cfg = {**config, 'screening': {'minimum_universe': 5000, 'pipeline': {'enabled': True, 'llm_rerank': True}}}
    service = StrategyScreener(cfg)
    monkeypatch.setattr(service, '_regime', lambda *a: '均衡')
    def unexpected(*a):
        pytest.fail('覆盖不足时禁止候选联网和模型调用')
    monkeypatch.setattr(service, '_enrich_candidate_context', unexpected)
    result = service.run(TRADE_DATE)
    assert result.status == 'partial' and result.pipeline['llm_rerank']['status'] == 'skipped'


def test_health_configuration_is_idempotent(config):
    registry = SourceHealthRegistry()
    path = config['database']['sqlite_path']
    registry.configure(path)
    registry.record('测试数据集', 'test', True)
    registry.configure(path)
    assert registry.snapshot()[0]['total_success'] == 1


def test_interval_collection_uses_hard_budget_and_child_does_not_recurse(config, monkeypatch):
    from src import scheduler
    from src.services import analysis_process
    calls = []
    monkeypatch.setattr(analysis_process, 'isolated_result', lambda operation, cfg, args: calls.append((operation, cfg, args)) or {'status': 'available'})
    cfg = {**config, 'data_sources': {'isolate_collection': True, 'collect_timeout_seconds': 12}}
    assert scheduler._isolate_collection_job('stock_data', cfg)
    assert calls[0][0] == 'collection_job' and calls[0][1]['diagnosis']['timeout_seconds'] == 12
    assert not scheduler._isolate_collection_job('stock_data', {**cfg, '_isolated_collection': True})
    assert len(calls) == 1
