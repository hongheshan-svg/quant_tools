"""海外研究报告使用原币及独立证据块，不要求 A 股专属诊断字段。"""
from src.services.report_language import tr, display


def institution_markdown(result: dict, lang='zh') -> str:
    block = (result.get('context_pack') or {}).get('blocks', {}).get('institution') or {}
    if not block.get('as_of'):
        return ''
    values = {key: item.get('value') for key, item in block.get('items', {}).items()}
    heading = tr(lang, '三大法人净买卖超', 'Institutional net flows', '3대 기관 순매수·순매도')
    unit = tr(lang, '股', 'shares', '주')
    labels = [('foreign_net', tr(lang, '外资（不含外资自营商）', 'Foreign investors (excluding foreign dealers)', '외국인 (외국 딜러 제외)')),
              ('trust_net', tr(lang, '投信', 'Investment trusts', '투자신탁')),
              ('dealer_net', tr(lang, '自营商', 'Dealers', '딜러')),
              ('total_net', tr(lang, '三大法人合计', 'Official total', '공식 합계'))]
    lines = [f"### {heading} · {block['as_of']} · {block.get('source') or ''}",
             tr(lang, '单位为股，正数净买超、负数净卖超。', 'Unit: shares. Positive = net buying; negative = net selling.', '단위: 주. 양수는 순매수, 음수는 순매도입니다.')]
    if block.get('status') != 'available' or values.get('refresh_status'):
        lines.append(tr(lang, '> 资料不完整、过期或刷新失败；仅作所标日期的历史参考。', '> Incomplete, stale, or refresh failed; use only for the stated date.', '> 불완전·오래된 자료 또는 갱신 실패: 표시된 날짜의 과거 참고자료입니다.'))
    for key, label in labels:
        value = values.get(key)
        lines.append(f'- {label}: {value:+,} {unit}' if isinstance(value, (int, float)) else f'- {label}: —')
    lines.append('[TWSE](https://www.twse.com.tw/zh/trading/foreign/t86.html) / [TPEx](https://www.tpex.org.tw/openapi/v1/tpex_3insti_daily_trading) · [OGDL-1.0](https://data.gov.tw/license)')
    return '\n\n'.join(lines)


def render_markdown(result: dict) -> str:
    lang = result.get('language') or 'zh'
    lines = [f"## {result.get('name') or result['code']} ({result['code']}) · {result.get('currency') or ''}",
             f"{display(lang, result.get('action_label') or '')} · {result.get('score', '—')} · {display(lang, result.get('confidence') or '')}",
             result.get('one_sentence') or '',
             tr(lang, f"行情日期：{result.get('trade_date', '—')}；研究时间：{result.get('created_at', '—')}",
                f"Quote date: {result.get('trade_date', '—')}; researched at: {result.get('created_at', '—')}",
                f"시세 기준일: {result.get('trade_date', '—')}; 연구 시간: {result.get('created_at', '—')}")]
    for key, zh, en, ko in [('trend', '趋势', 'Trend', '추세'), ('core_logic', '核心逻辑', 'Thesis', '핵심 논거'),
                          ('operation_advice', '观察计划', 'Research plan', '연구 계획'),
                          ('risk_warning', '风险', 'Risks', '위험'), ('invalidation', '失效条件', 'Invalidation', '무효화 조건')]:
        if result.get(key):
            lines.append(f"### {tr(lang, zh, en, ko)}\n\n{result[key]}")
    lines.append(institution_markdown(result, lang))
    limitations = (result.get('data_quality') or {}).get('limitations') or []
    if limitations:
        lines.append(tr(lang, '行情受限：', 'Quote limitations: ', '시세 제약: ') + ', '.join(limitations))
    lines.append(tr(lang, '> 仅供海外证券学习研究，使用原币价格；轻量证据不能替代完整尽调，不构成投资建议。',
                    '> Overseas research using original currencies. Lightweight evidence does not replace due diligence; not investment advice.',
                    '> 원래 통화를 사용하는 해외 증권 연구입니다. 제한된 자료는 실사를 대체하지 않으며 투자 조언이 아닙니다.'))
    return '\n\n'.join(line for line in lines if line)
