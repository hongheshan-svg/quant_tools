"""韩文消费端：固定报告文案、护栏、审议及模板预览，保留原始枚举。"""
import re

import pytest

from tests.test_report_language import db_path, offline, _svc, RecLLM  # noqa: F401
from tests.test_decision_profile import inputs
from src.services import market_phase
from src.services.decision_profile import decide
from src.services.stock_diagnosis import render_markdown
from src.services.watchlist_report import render_dashboard, change_text, WatchlistReportService
from src.services.report_templates import preview
from src.services.strategy_synthesis import synthesize
from src.services.market_review import MarketReviewService
from src.services.market_context import MarketFacts


def test_korean_diagnosis_fixed_labels_enums_and_source_values(db_path):
    service, llm = _svc(db_path, lang='ko')
    result = service.diagnose('002594', force=True)
    text = render_markdown(result)
    assert 'Korean' in llm.systems[0]
    for label in ('보유 상태별 조언', '매매 계획', '시장 단계별 판단', '신호 기여 요인', '위험 요인', '투자 조언이 아닙니다'):
        assert label in text
    assert '比亚迪' in text and '002594' in text  # 原始证券名和代码不翻译
    assert result['action'] in ('buy', 'watch') and result['confidence'] in ('高', '中', '低')
    assert 'Position advice' not in text and '评分' not in text and '信心' not in text


@pytest.mark.parametrize('profile', ['conservative', 'balanced', 'aggressive'])
def test_korean_score_guardrail_keeps_threshold_numbers(profile):
    result = decide(inputs(score=40), profile, 'ko')
    assert result[0] == 'watch' and '40' in result[2][0] and '관망' in result[2][0]
    assert not re.search(r'[一-鿿]', result[2][0])
    quality = decide(inputs(data_quality={'score':10,'core_ok':True,'bar_count':30,'missing':['news']}), profile, 'ko')
    assert quality[0] == 'watch' and '10%' in ' '.join(quality[2]) and '관망' in ' '.join(quality[2])


@pytest.mark.parametrize('phase', ['premarket', 'non_trading'])
def test_korean_immediate_trade_is_guarded_and_negative_instruction_preserved(phase):
    ctx = {'phase':phase,'label':'盘前' if phase == 'premarket' else '非交易日', 'now':'2026-09-28 08:00',
           'effective_daily_bar_date':None, 'is_partial_bar':False}
    for instruction in ('지금 매수하세요', '즉시 전량 매도', '바로 추가 매수하세요'):
        _, confidence, decision, notes = market_phase.phase_guardrails('buy','高',{'immediate_action':instruction}, ctx,'2026-09-25','ko')
        assert confidence == '中' and decision['immediate_action'] != instruction and '개장' in decision['immediate_action']
        assert notes and not re.search(r'[一-鿿]', notes[0])
    negative = '지금 매수하지 마세요'
    _, confidence, decision, notes = market_phase.phase_guardrails('watch','高',{'immediate_action':negative}, ctx,'2026-09-25','ko')
    assert decision['immediate_action'] == negative and confidence == '高' and not notes


def test_korean_strategy_deliberation_instructs_model_and_preserves_original_opinions():
    original = [{'skill':'alpha','stance':'看多','score':90,'confidence':'高','reason':'상승 근거'},
                {'skill':'beta','stance':'看空','score':10,'confidence':'高','reason':'하락 근거'}]
    revised = [{**row, 'stance':'中性','score':50,'confidence':'低','reason':'추가 확인 필요'} for row in original]
    model = RecLLM({'opinions':revised})
    result = synthesize(original, llm=model, config={'enabled':True}, lang='ko')
    assert 'Korean' in model.systems[0] and 'stance=看多/看空/中性' in model.systems[0]
    assert result['original_opinions'] == original and result['deliberation']['resolution_status'] == 'resolved'
    assert result['original_conflicts'][0]['description'] == '전략 간 방향 판단이 상충합니다.'


def test_korean_watchlist_persists_language_and_previews_same_report(db_path):
    config = {'database':{'sqlite_path':db_path}, 'report':{'language':'ko'}}
    items = [{'code':'002594','name':'比亚迪','kind':'stock','action':'watch','action_label':'观望','score':60,
              'one_sentence':'변동성 관찰','battle_plan':{'stop_loss':22.5}, 'change':'첫 진단', 'risks':['유동성 위험']}]
    text = render_dashboard('2026-09-25', items, [], 'ko')
    assert '관심 종목 의사결정 대시보드' in text and '관망' in text and '손절 22.50' in text
    assert '比亚迪' in text and '评分' not in text and 'Stock Details' not in text
    service = WatchlistReportService(config)
    service._save('2026-09-25', text, items, [])
    assert service.latest()['language'] == 'ko'
    rendered = preview('watchlist', '{{ default }}', config=config)
    assert '관심 종목 의사결정 대시보드' in rendered and '自选股决策仪表盘' not in rendered
    change = change_text({'action':'buy','action_label':'买入','score':80}, {'action':'watch','action_label':'观望','score':60,'created_at':'2026-09-25'}, 'ko')
    assert '관망→매수' in change and '점수 60→80' in change


def test_korean_market_review_keeps_machine_stance_and_localizes_position(db_path, monkeypatch):
    from types import SimpleNamespace
    regime = SimpleNamespace(trade_date='2026-09-25', regime='防守', score=30., reasons=[], summary=lambda: 'source regime')
    monkeypatch.setattr('src.services.market_review.build_market_facts', lambda *args: MarketFacts(regime=regime))
    llm = RecLLM({'headline':'시장 복기','stance':'进攻','position':'90%','trend':'변동성 확대'})
    result = MarketReviewService({'database':{'sqlite_path':db_path},'report':{'language':'ko'}},llm).generate()
    assert result['stance'] == '防守' and result['position'] == '0~30%'
    assert '**방어적**' in result['markdown'] and '**추세**' in result['markdown']
    assert 'Next-day stance' not in result['markdown'] and '공격적' in result['guardrails'][0]
