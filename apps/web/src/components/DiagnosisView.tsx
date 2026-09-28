// AI 诊断结果：结论、价格计划、分析员观点、资金/筹码/业绩、利好风险、检查清单
import type { Diagnosis } from '@/api/types'
import { cn } from '@/utils/cn'
import { fmtNum, verdictClass } from '@/utils/format'
import { Badge } from './ui'

const CHECK = { pass: '✅', warn: '⚠️', fail: '❌' } as Record<string, string>

export function DiagnosisView({ d }: { d: Diagnosis }) {
  if (d.error) return <p className="text-sm text-danger">诊断失败：{d.error}</p>
  const plan = d.battle_plan ?? {}
  const agents = (d.agents ?? []).filter((a) => !a.error)
  return (
    <div className="space-y-4 text-sm">
      <div className="flex flex-wrap items-center gap-3">
        <span className={cn('text-xl font-semibold', verdictClass(d.action_label))}>{d.action_label}</span>
        <span className="num text-lg">{d.score} 分</span>
        <Badge>信心 {d.confidence || '-'}</Badge>
        {d.trend_prediction && <Badge tone="accent">{d.trend_prediction}</Badge>}
        <span className="text-xs text-muted">诊断于 {d.created_at}（行情 {d.trade_date}）{d.cached ? '，30 分钟内的结果' : ''}</span>
      </div>
      {d.one_sentence && <p className="text-base font-medium">{d.one_sentence}</p>}
      <p className="text-xs text-muted">
        数据完整度 {d.data_quality?.score ?? '--'}%{d.data_quality?.missing?.length ? `（缺少：${d.data_quality.missing.join('、')}）` : ''}
      </p>
      {d.guardrails?.length > 0 && <div className="rounded-md border border-warn/40 bg-warn/10 px-3 py-2 text-warn">护栏：{d.guardrails.join('；')}</div>}
      <div className="grid gap-3 md:grid-cols-2">
        <div className="rounded-md border border-line p-3">
          <div className="mb-1 text-xs text-muted">价格计划</div>
          <div className="num">
            买入 {fmtNum(plan.buy_price)} ｜ 止损 <span className="text-down">{fmtNum(plan.stop_loss)}</span> ｜ 目标 <span className="text-up">{fmtNum(plan.target_price)}</span>
          </div>
          {plan.suggested_position && <div className="mt-1 text-xs">{plan.suggested_position}</div>}
        </div>
        <div className="rounded-md border border-line p-3">
          <div className="mb-1 text-xs text-muted">操作建议</div>
          <div>空仓：{d.position_advice?.no_position ?? '-'}</div>
          <div>持仓：{d.position_advice?.has_position ?? '-'}</div>
        </div>
      </div>
      {agents.length > 0 && (
        <div>
          <div className="mb-1 font-medium text-accent">分析员观点 {d.disagreement ? <span className="text-warn">（分歧：{d.disagreement}）</span> : <span className="text-xs text-muted">（观点基本一致）</span>}</div>
          <ul className="space-y-1">
            {agents.map((a) => (
              <li key={a.role}>
                <span className="text-muted">{a.label}：</span>
                <span className={verdictClass(a.view)}>{a.view}</span> <span className="num">{a.score}分</span>
                <span className="text-xs text-muted">（信心{a.confidence}）{a.key_points?.join('；')}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
      <div className="grid gap-1 text-xs">
        {d.theme_role?.theme && <div><span className="text-muted">主线地位：</span>{d.theme_role.theme}（{d.theme_role.phase}）{d.theme_role.role}</div>}
        <div><span className="text-muted">大盘：</span>{d.market_regime}</div>
        {d.fund_flow && <div><span className="text-muted">资金：</span>{d.fund_flow}</div>}
        {d.earnings && <div><span className="text-muted">业绩：</span>{d.earnings}</div>}
        {d.valuation && <div><span className="text-muted">估值：</span>{d.valuation}</div>}
        {d.calibration && <div><span className="text-muted">历史表现：</span>{d.calibration.replace('【历史表现】', '')}</div>}
      </div>
      <div className="grid gap-3 md:grid-cols-2">
        <div>
          <div className="mb-1 font-medium text-up">利好催化</div>
          <ul className="list-disc pl-5">{(d.catalysts ?? []).map((c) => <li key={c}>{c}</li>)}</ul>
        </div>
        <div>
          <div className="mb-1 font-medium text-down">风险提示</div>
          <ul className="list-disc pl-5">{(d.risks ?? []).map((r) => <li key={r}>{r}</li>)}</ul>
        </div>
      </div>
      {d.checklist?.length > 0 && (
        <div>
          <div className="mb-1 font-medium text-accent">检查清单</div>
          <ul className="space-y-0.5">{d.checklist.map((c) => <li key={c.item}>{CHECK[c.status] ?? '•'} {c.item}：<span className="text-muted">{c.note}</span></li>)}</ul>
        </div>
      )}
      {d.analysis && <p className="leading-relaxed text-muted">{d.analysis}</p>}
      <p className="text-xs text-muted">仅供学习研究，不构成投资建议</p>
    </div>
  )
}
