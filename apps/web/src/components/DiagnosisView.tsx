// AI 诊断结果：结论、价格计划、分析员观点、资金/筹码/业绩、利好风险、检查清单
import { useEffect, useState } from 'react'
import { api } from '@/api/endpoints'
import type { DecisionProfile, Diagnosis, PhaseDecision, ReassessResult, SignalAttribution } from '@/api/types'
import { useT } from '@/i18n'
import { cn } from '@/utils/cn'
import { fmtNum, verdictClass } from '@/utils/format'
import { FUND_LABELS } from '@/utils/fund'
import { Badge, Button, Modal, Spinner } from './ui'

const PROFILES: DecisionProfile[] = ['conservative', 'balanced', 'aggressive']
export const PROFILE_LABELS: Record<DecisionProfile, string> = { conservative: '保守', balanced: '均衡', aggressive: '进取' }

const CHECK = { pass: '✅', warn: '⚠️', fail: '❌' } as Record<string, string>

function PhaseCard({ pd }: { pd: PhaseDecision }) {
  const t = useT()
  const label = pd.phase_label ?? ''
  const conds = pd.watch_conditions ?? []
  const limits = pd.data_limitations ?? []
  return (
    <div className="rounded-md border border-line p-3" data-testid="phase-decision">
      <div className="mb-1 flex items-center gap-2 text-xs text-muted">
        {t('阶段决策')}
        {label && <Badge tone="accent">{t(label)}</Badge>}
      </div>
      {pd.trading_window && <div>{t('操作窗口：')}{pd.trading_window}</div>}
      {pd.immediate_action && <div>{t('立即行动：')}{pd.immediate_action}</div>}
      {conds.length > 0 && (
        <div>
          {t('观察条件：')}
          <ul className="list-disc pl-5">{conds.map((c) => <li key={c}>{c}</li>)}</ul>
        </div>
      )}
      {pd.next_check_time && <div>{t('下次检查：')}{pd.next_check_time}</div>}
      {limits.length > 0 && <div className="mt-1 text-xs text-warn">{t('数据限制：')}{limits.join('；')}</div>}
    </div>
  )
}

const ATTR_ROWS = [
  ['technical', '技术面'],
  ['news', '资讯'],
  ['fundamentals', '基本面'],
  ['market', '大盘'],
] as const

function AttributionCard({ attr }: { attr: SignalAttribution }) {
  const t = useT()
  return (
    <div className="rounded-md border border-line p-3" data-testid="signal-attribution">
      <div className="mb-1 text-xs text-muted">{t('信号归因')}</div>
      <div className="space-y-1">
        {ATTR_ROWS.filter(([k]) => attr[k] != null).map(([k, label]) => (
          <div key={k} className="flex items-center gap-2">
            <span className="w-14 shrink-0 text-xs text-muted">{t(label)}</span>
            <div className="h-2 flex-1 rounded bg-line/50">
              <div className="h-2 rounded bg-accent" style={{ width: `${Math.max(0, Math.min(100, attr[k] ?? 0))}%` }} />
            </div>
            <span className="num w-10 text-right text-xs">{attr[k]}%</span>
          </div>
        ))}
      </div>
      {attr.strongest_bullish && <div className="mt-1 text-xs"><span className="text-muted">{t('最强看多：')}</span><span className="text-up">{attr.strongest_bullish}</span></div>}
      {attr.strongest_bearish && <div className="text-xs"><span className="text-muted">{t('最强看空：')}</span><span className="text-down">{attr.strongest_bearish}</span></div>}
    </div>
  )
}

type Column = { data?: ReassessResult; error?: string; saving?: boolean; saved?: string }

// 按三种决策风格重新评估：只用诊断时保存的快照，不重新调用模型；每列可保存为该风格的决策信号
function ReassessModal({ id, onClose }: { id: number; onClose: () => void }) {
  const t = useT()
  const [cols, setCols] = useState<Partial<Record<DecisionProfile, Column>>>({})
  const patch = (p: DecisionProfile, v: Column) => setCols((c) => ({ ...c, [p]: { ...c[p], ...v } }))

  useEffect(() => {
    let alive = true
    for (const p of PROFILES) {
      api.reassessDiagnosis(id, p).then(
        (data) => alive && patch(p, { data }),
        (e) => alive && patch(p, { error: e instanceof Error ? e.message : String(e) }),
      )
    }
    return () => { alive = false }
  }, [id])

  async function save(p: DecisionProfile) {
    patch(p, { saving: true })
    try {
      const r = await api.reassessDiagnosis(id, p, true)
      const text = r.status === 'created' ? t('已保存为决策信号')
        : r.status === 'existing' ? t('该风格的决策信号已存在')
          : t('该风格下的建议不是方向性建议，不生成决策信号')
      patch(p, { saving: false, saved: text })
    } catch (e) {
      patch(p, { saving: false, saved: e instanceof Error ? e.message : String(e) })
    }
  }

  return (
    <Modal open wide title="按其他风格评估" onClose={onClose}>
      <p className="mb-3 text-xs text-muted">{t('只使用诊断时保存的数据快照重新计算护栏，不会重新调用 AI；与原结论不同的会高亮。')}</p>
      <div className="grid gap-3 md:grid-cols-3">
        {PROFILES.map((p) => {
          const col = cols[p]
          const r = col?.data
          return (
            <div key={p} className={cn('rounded-md border p-3', r?.changed ? 'border-warn/60 bg-warn/5' : 'border-line')} data-testid={`reassess-${p}`}>
              <div className="mb-2 flex items-center gap-2 font-medium">
                {t(PROFILE_LABELS[p])}
                {r?.changed && <Badge tone="warn">{t('与原结论不同')}</Badge>}
              </div>
              {col?.error && <p className="text-xs text-danger">{col.error}</p>}
              {!col?.error && !r && <Spinner />}
              <div className="space-y-2">
                {r && (
                  <>
                  <div className="flex items-center gap-2">
                    <span className={cn('text-lg font-semibold', verdictClass(r.action_label))}>{t(r.action_label)}</span>
                    <Badge>{t('信心')} {t(r.confidence || '-')}</Badge>
                  </div>
                  {r.guardrails.length > 0
                    ? <ul className="list-disc pl-4 text-xs text-warn">{r.guardrails.map((g) => <li key={g}>{g}</li>)}</ul>
                    : <p className="text-xs text-muted">{t('没有触发护栏')}</p>}
                  </>
                )}
                <Button loading={col?.saving} disabled={!r} onClick={() => void save(p)}>{t('保存为决策信号')}</Button>
                {col?.saved && <p className="text-xs text-accent">{col.saved}</p>}
              </div>
            </div>
          )
        })}
      </div>
    </Modal>
  )
}

export function DiagnosisView({ d, diagnosisId }: { d: Diagnosis; diagnosisId?: number }) {
  const t = useT()
  const [compare, setCompare] = useState(false)
  if (d.error) return <p className="text-sm text-danger">{t('诊断失败：')}{d.error}</p>
  const plan = d.battle_plan ?? {}
  const agents = (d.agents ?? []).filter((a) => !a.error)
  const pd = d.phase_decision
  const hasPhase = !!pd && !!(pd.trading_window || pd.immediate_action || pd.next_check_time || pd.watch_conditions?.length || pd.data_limitations?.length)
  const attr = d.signal_attribution
  const hasAttr = !!attr && Object.keys(attr).length > 0
  const recordId = diagnosisId ?? d.diagnosis_id ?? d.id
  return (
    <div className="space-y-4 text-sm">
      <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_180px]">
        <div className="rounded-xl border border-accent/25 bg-accent/5 p-4">
          <div className="mb-3 flex flex-wrap items-center gap-2">
            <span className={cn('text-xl font-semibold', verdictClass(d.action_label))}>{t(d.action_label)}</span>
            <Badge>{t('信心')} {t(d.confidence || '-')}</Badge>
            {d.kind && <Badge>{t(FUND_LABELS[d.kind])}</Badge>}
            {d.trend_prediction && <Badge tone="accent">{d.trend_prediction}</Badge>}
            {d.decision_profile && <span title={t('决策风格')}><Badge tone="accent">{t(PROFILE_LABELS[d.decision_profile] ?? d.decision_profile)}</Badge></span>}
            {recordId != null && d.decision_profile && <Button onClick={() => setCompare(true)}>{t('按其他风格评估')}</Button>}
          </div>
          {d.one_sentence && <p className="text-base leading-relaxed font-medium">{d.one_sentence}</p>}
          <p className="mt-3 text-xs text-muted">{t('诊断于 {a}（行情 {b}）', { a: d.created_at, b: d.trade_date })}{d.cached ? t('，30 分钟内的结果') : ''}</p>
        </div>
        <div className="flex flex-col items-center justify-center rounded-xl border border-line bg-panel-2 p-3">
          <span className="text-xs text-muted">{t('诊断评分')}</span>
          <div className="relative mt-2 size-24">
            <svg viewBox="0 0 100 100" className="size-24 -rotate-90" aria-hidden="true"><circle cx="50" cy="50" r="42" fill="none" stroke="currentColor" strokeWidth="6" className="text-line" /><circle cx="50" cy="50" r="42" fill="none" stroke="currentColor" strokeWidth="6" strokeLinecap="round" pathLength="100" strokeDasharray={`${Number.isFinite(d.score) ? Math.max(0, Math.min(100, d.score)) : 0} 100`} className="text-accent" /></svg>
            <span className="num absolute inset-0 flex items-center justify-center text-xl font-semibold">{Number.isFinite(d.score) ? t('{n} 分', { n: d.score }) : '--'}</span>
          </div>
        </div>
      </div>
      <p className="text-xs text-muted">
        {t('数据完整度')} {d.data_quality?.score ?? '--'}%{d.data_quality?.missing?.length ? t('（缺少：{list}）', { list: d.data_quality.missing.join('、') }) : ''}
      </p>
      {d.guardrails?.length > 0 && <div className="rounded-md border border-warn/40 bg-warn/10 px-3 py-2 text-warn">{t('护栏：')}{d.guardrails.join('；')}</div>}
      <div className="grid gap-3 md:grid-cols-2">
        <div className="rounded-md border border-line p-3">
          <div className="mb-1 text-xs text-muted">{t('价格计划')}</div>
          <div className="mt-2 grid grid-cols-3 gap-2">
            {[['买入', plan.buy_price, 'text-accent'], ['止损', plan.stop_loss, 'text-down'], ['目标', plan.target_price, 'text-up']].map(([label, value, tone]) => <div key={String(label)} className="rounded-lg border border-line bg-panel-2 p-2"><p className="text-xs text-muted">{t(String(label))}</p><p className={cn('num mt-1 text-lg font-semibold', String(tone))}>{fmtNum(value)}</p></div>)}
          </div>
          {plan.suggested_position && <div className="mt-1 text-xs">{plan.suggested_position}</div>}
        </div>
        <div className="rounded-md border border-line p-3">
          <div className="mb-1 text-xs text-muted">{t('操作建议')}</div>
          <div>{t('空仓：')}{d.position_advice?.no_position ?? '-'}</div>
          <div>{t('持仓：')}{d.position_advice?.has_position ?? '-'}</div>
        </div>
      </div>
      {(hasPhase || hasAttr) && (
        <div className="grid gap-3 md:grid-cols-2">
          {hasPhase && <PhaseCard pd={pd!} />}
          {hasAttr && <AttributionCard attr={attr!} />}
        </div>
      )}
      {agents.length > 0 && (
        <div>
          <div className="mb-1 font-medium text-accent">{t('分析员观点')} {d.disagreement ? <span className="text-warn">{t('（分歧：{text}）', { text: d.disagreement })}</span> : <span className="text-xs text-muted">{agents.length < 2 ? t('（有效观点不足）') : t('（观点基本一致）')}</span>}</div>
          <ul className="space-y-1">
            {agents.map((a) => (
              <li key={a.role}>
                <span className="text-muted">{a.label}：</span>
                <span className={verdictClass(a.view)}>{t(a.view ?? '')}</span> <span className="num">{t('{n}分', { n: a.score ?? '--' })}</span>
                <span className="text-xs text-muted">{t('（信心{v}）', { v: t(a.confidence ?? '') })}{a.key_points?.join('；')}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
      {(d.skill_opinions?.length ?? 0) > 0 && (
        <div>
          <div className="mb-1 font-medium text-accent">
            {t('策略会诊')}{' '}
            {d.skill_consensus?.status === 'insufficient' && <p className="text-sm text-muted">{t('有效策略观点不足，不能形成共识')}</p>}
            {d.skill_consensus?.stance && d.skill_consensus.status !== 'insufficient' && (
              <span className="text-xs font-normal">
                {t('共识')} <span className={verdictClass(d.skill_consensus.stance)}>{t(d.skill_consensus.stance)}</span>{' '}
                <span className="num">{t('{n}分', { n: d.skill_consensus.score ?? '--' })}</span>
                {d.skill_consensus.agreement === '分歧' ? <span className="text-warn">{t('（分歧）')}</span> : <span className="text-muted">{t('（一致）')}</span>}
              </span>
            )}
          </div>
          <ul className="space-y-1">
            {d.skill_opinions!.map((o) => (
              <li key={o.skill}>
                <span className="text-muted">{o.display_name}：</span>
                <span className={verdictClass(o.stance)}>{t(o.stance ?? '')}</span> <span className="num">{t('{n}分', { n: o.score ?? '--' })}</span>
                <span className="text-xs text-muted">{t('（信心{v}，权重 {w}）', { v: t(o.confidence ?? ''), w: o.weight.toFixed(2) })}{o.reason}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
      <div className="grid gap-1 text-xs">
        {d.theme_role?.theme && <div><span className="text-muted">{t('主线地位：')}</span>{d.theme_role.theme}（{t(d.theme_role.phase ?? '')}）{t(d.theme_role.role ?? '')}</div>}
        <div><span className="text-muted">{t('大盘：')}</span>{d.market_regime}</div>
        {d.fund_flow && <div><span className="text-muted">{t('资金：')}</span>{d.fund_flow}</div>}
        {d.earnings && <div><span className="text-muted">{t('业绩：')}</span>{d.earnings}</div>}
        {d.shareholders && <div><span className="text-muted">{t('股东：')}</span>{d.shareholders}</div>}
        {d.valuation && <div><span className="text-muted">{t('估值：')}</span>{d.valuation}</div>}
        {d.calibration && <div><span className="text-muted">{t('历史表现：')}</span>{d.calibration.replace('【历史表现】', '')}</div>}
      </div>
      <div className="grid gap-3 md:grid-cols-2">
        <div>
          <div className="mb-1 font-medium text-up">{t('利好催化')}</div>
          <ul className="list-disc pl-5">{(d.catalysts ?? []).map((c) => <li key={c}>{c}</li>)}</ul>
        </div>
        <div>
          <div className="mb-1 font-medium text-down">{t('风险提示')}</div>
          <ul className="list-disc pl-5">{(d.risks ?? []).map((r) => <li key={r}>{r}</li>)}</ul>
        </div>
      </div>
      {d.checklist?.length > 0 && (
        <div>
          <div className="mb-1 font-medium text-accent">{t('检查清单')}</div>
          <ul className="space-y-0.5">{d.checklist.map((c) => <li key={c.item}>{CHECK[c.status] ?? '•'} {c.item}：<span className="text-muted">{c.note}</span></li>)}</ul>
        </div>
      )}
      {d.analysis && <p className="leading-relaxed text-muted">{d.analysis}</p>}
      <p className="text-xs text-muted">{t('仅供学习研究，不构成投资建议')}</p>
      {compare && recordId != null && <ReassessModal id={recordId} onClose={() => setCompare(false)} />}
    </div>
  )
}
