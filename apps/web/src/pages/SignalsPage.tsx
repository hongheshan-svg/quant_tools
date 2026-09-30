// 决策信号：诊断给出的买入/减仓等建议的生命周期（观察期、失效条件、状态）与后验表现，可反馈有用/没用
import { X } from 'lucide-react'
import { useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { api } from '@/api/endpoints'
import type { DecisionSignal, SkillPerformanceRow } from '@/api/types'
import { DataTable, type Column } from '@/components/DataTable'
import { StockSearch } from '@/components/StockSearch'
import { Badge, Button, Card, ErrorBox, Modal, PageHeader, Pct, Select, Spinner, Stat, Tabs, Textarea } from '@/components/ui'
import { useApi } from '@/hooks/useApi'
import { useTask } from '@/hooks/useTask'
import { toast } from '@/stores/toast'
import { cn } from '@/utils/cn'
import { fmtNum, fmtPct, isNum, verdictClass } from '@/utils/format'

const PAGE_SIZE = 50
const ACTIONS: Record<string, string> = { buy: '买入', add: '加仓', reduce: '减仓', sell: '卖出', avoid: '回避' }
const STATUSES: Record<string, string> = {
  active: '观察中', invalidated: '已失效', replaced: '已替代', expired: '已到期', hit_target: '触及目标', hit_stop: '触及止损',
}
const STATUS_TONE: Record<string, 'default' | 'up' | 'down' | 'warn' | 'accent'> = {
  active: 'accent', invalidated: 'warn', replaced: 'default', expired: 'default', hit_target: 'up', hit_stop: 'down',
}
const DAYS = [{ v: 7, t: '近 7 天' }, { v: 30, t: '近 30 天' }, { v: 90, t: '近 90 天' }, { v: 0, t: '不限' }]

export function SignalsPage() {
  const [tab, setTab] = useState<'signals' | 'skills'>('signals')
  const [params, setParams] = useSearchParams()
  const code = params.get('code') ?? ''
  const [status, setStatus] = useState('')
  const [action, setAction] = useState('')
  const [days, setDays] = useState(90)
  const [limit, setLimit] = useState(PAGE_SIZE)
  const [open, setOpen] = useState<DecisionSignal | null>(null)
  const task = useTask<Record<string, number>>()
  const stats = useApi(() => api.signalStats(days || 3650), [days])
  const list = useApi(
    () => api.signals({ status: status || undefined, action: action || undefined, code: code || undefined, days, limit, offset: 0 }),
    [status, action, code, days, limit],
  )

  const setCode = (c: string) => {
    setLimit(PAGE_SIZE)
    setParams(c ? { code: c } : {})
  }
  const evaluate = () =>
    task
      .run(() => api.evaluateSignals(), { success: (r) => `评估完成：${r.evaluated ?? 0} 条` })
      .then(() => { void list.reload(); void stats.reload() })
      .catch(() => {})

  const columns: Column<DecisionSignal>[] = [
    { key: 'time', title: '时间', render: (r) => <span className="num text-xs">{r.created_at ?? r.trade_date}</span> },
    { key: 'stock', title: '股票', render: (r) => <>{r.name} <span className="num text-xs text-muted">{r.code}</span></> },
    {
      key: 'action', title: '建议',
      render: (r) => <Badge className={cn(verdictClass(ACTIONS[r.action]))}>{ACTIONS[r.action] ?? r.action}</Badge>,
    },
    { key: 'score', title: '评分', align: 'right', render: (r) => <span className="num">{r.score ?? '--'}</span> },
    { key: 'horizon', title: '观察期', render: (r) => <span className="text-xs">{r.horizon_days} 日{r.expires_on ? ` · 至 ${r.expires_on}` : ''}</span> },
    { key: 'status', title: '状态', render: (r) => <Badge tone={STATUS_TONE[r.status] ?? 'default'}>{STATUSES[r.status] ?? r.status}</Badge> },
    { key: 'r1', title: '1日', align: 'right', render: (r) => <Pct value={r.ret_1d} /> },
    { key: 'r3', title: '3日', align: 'right', render: (r) => <Pct value={r.ret_3d} /> },
    { key: 'r5', title: '5日', align: 'right', render: (r) => <Pct value={r.ret_5d} /> },
    { key: 'adv', title: '最大不利', align: 'right', render: (r) => <span className="num">{fmtPct(r.max_adverse_pct, 2, false)}</span> },
  ]

  const s = stats.data
  return (
    <div>
      <PageHeader
        title="决策信号"
        description="诊断给出的买入、加仓、减仓、卖出、回避建议会生成信号，带观察期和失效条件，之后按行情评估命中情况"
        actions={<Button variant="primary" loading={task.running} onClick={evaluate}>立即评估</Button>}
      />
      <Tabs
        value={tab}
        onChange={setTab}
        tabs={[{ key: 'signals', label: '信号' }, { key: 'skills', label: '策略表现' }]}
      />
      {tab === 'skills' && <SkillPerformance />}
      {tab === 'signals' && (
        <>
        <div className="mb-4 grid grid-cols-2 gap-3 md:grid-cols-4">
          <Stat label="信号总数" value={s ? s.total : '--'} />
          <Stat label="命中率" value={s && isNum(s.hit_rate) ? `${s.hit_rate.toFixed(1)}%` : '--'} />
          <Stat label="平均 5 日收益" value={<Pct value={s?.avg_ret_5d} />} />
          <Stat label="平均最大不利波动" value={<span className="num">{fmtPct(s?.avg_adverse, 2, false)}</span>} />
        </div>
        <Card className="mb-4" bodyClassName="flex flex-wrap items-center gap-3">
          {code ? (
            <span className="inline-flex items-center gap-1 rounded border border-accent/40 bg-accent/10 px-2 py-1 text-sm text-accent">
              股票 {code}
              <button type="button" aria-label="清除股票筛选" onClick={() => setCode('')}><X className="size-3.5" /></button>
            </span>
          ) : (
            <StockSearch className="w-64" placeholder="按股票筛选" onSelect={(x) => setCode(x.code)} />
          )}
          <Select aria-label="状态" value={status} onChange={(e) => { setStatus(e.target.value); setLimit(PAGE_SIZE) }}>
            <option value="">全部状态</option>
            {Object.entries(STATUSES).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </Select>
          <Select aria-label="建议" value={action} onChange={(e) => { setAction(e.target.value); setLimit(PAGE_SIZE) }}>
            <option value="">全部建议</option>
            {Object.entries(ACTIONS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </Select>
          <Select aria-label="时间范围" value={days} onChange={(e) => { setDays(Number(e.target.value)); setLimit(PAGE_SIZE) }}>
            {DAYS.map((d) => <option key={d.v} value={d.v}>{d.t}</option>)}
          </Select>
          {list.data && <span className="text-xs text-muted">共 {list.data.total} 条</span>}
        </Card>
        {list.error && <ErrorBox message={list.error} onRetry={list.reload} />}
        {list.loading && !list.data ? <Spinner /> : list.data && (
          <Card bodyClassName="p-0">
            <DataTable columns={columns} rows={list.data.items} rowKey={(r) => r.id} onRowClick={setOpen} empty="还没有决策信号，做一次 AI 诊断后会自动生成" />
            {list.data.items.length < list.data.total && (
              <div className="border-t border-line p-3 text-center">
                <Button loading={list.loading} onClick={() => setLimit(limit + PAGE_SIZE)}>加载更多</Button>
              </div>
            )}
          </Card>
        )}
        </>
      )}
      {tab === 'signals' && open && <DetailModal signal={open} onClose={() => setOpen(null)} onSaved={() => void list.reload()} />}
    </div>
  )
}

function SkillPerformance() {
  const perf = useApi(() => api.skillPerformance(90), [])
  const columns: Column<SkillPerformanceRow>[] = [
    { key: 'skill', title: '策略', render: (r) => r.display_name },
    { key: 'samples', title: '样本', align: 'right', render: (r) => <span className="num">{r.samples}</span> },
    { key: 'hits', title: '命中', align: 'right', render: (r) => <span className="num">{r.hits}</span> },
    { key: 'rate', title: '命中率', align: 'right', render: (r) => <span className="num">{r.hit_rate.toFixed(1)}%</span> },
    { key: 'ret', title: '平均 5 日收益', align: 'right', render: (r) => <Pct value={r.avg_ret} /> },
    { key: 'weight', title: '当前权重', align: 'right', render: (r) => <span className="num">{r.weight.toFixed(2)}</span> },
  ]
  if (perf.error) return <ErrorBox message={perf.error} onRetry={perf.reload} />
  if (perf.loading && !perf.data) return <Spinner />
  return (
    <Card bodyClassName="p-0">
      <DataTable columns={columns} rows={perf.data ?? []} rowKey={(r) => r.skill} empty="还没有策略会诊的后验样本，诊断 5 个交易日后开始统计" />
      <p className="border-t border-line p-3 text-xs text-muted">统计近 90 天各策略在个股诊断中的观点：看多后 5 日上涨、看空后 5 日下跌算命中，中性不统计；样本不足 20 时权重为 1.00。</p>
    </Card>
  )
}

function DetailModal({ signal, onClose, onSaved }: { signal: DecisionSignal; onClose: () => void; onSaved: () => void }) {
  const [feedback, setFeedback] = useState<string>(signal.feedback ?? '')
  const [note, setNote] = useState(signal.feedback_note ?? '')
  const [saving, setSaving] = useState(false)

  const save = async (value: 'useful' | 'not_useful') => {
    setSaving(true)
    try {
      await api.signalFeedback(signal.id, value, note)
      setFeedback(value)
      toast.success('已记录反馈')
      onSaved()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '保存失败')
    } finally {
      setSaving(false)
    }
  }
  const entry = isNum(signal.entry_low) || isNum(signal.entry_high)
    ? (signal.entry_low === signal.entry_high ? fmtNum(signal.entry_low) : `${fmtNum(signal.entry_low)} ~ ${fmtNum(signal.entry_high)}`)
    : '--'
  const hitText = signal.hit === true ? '命中' : signal.hit === false ? '未命中' : '--'
  return (
    <Modal open wide title={`${signal.name}(${signal.code}) · ${ACTIONS[signal.action] ?? signal.action}`} onClose={onClose}>
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Stat label="买点" value={entry} />
        <Stat label="止损" value={fmtNum(signal.stop_loss)} />
        <Stat label="目标" value={fmtNum(signal.target_price)} />
        <Stat label="观察期" value={`${signal.horizon_days} 日`} sub={signal.expires_on ? `至 ${signal.expires_on}` : undefined} />
      </div>
      <dl className="mt-4 space-y-2 text-sm">
        <div><dt className="inline text-muted">状态：</dt><dd className="inline"><Badge tone={STATUS_TONE[signal.status] ?? 'default'}>{STATUSES[signal.status] ?? signal.status}</Badge> {signal.status_reason}</dd></div>
        <div><dt className="inline text-muted">失效条件：</dt><dd className="inline">{signal.invalidation || '--'}</dd></div>
        <div><dt className="inline text-muted">诊断行情日：</dt><dd className="num inline">{signal.trade_date}</dd>
          <span className="ml-3 text-muted">评分</span> <span className="num">{signal.score ?? '--'}</span>
          {signal.confidence && <span className="ml-3 text-muted">信心 {signal.confidence}</span>}</div>
      </dl>
      <div className="mt-4 grid grid-cols-2 gap-3 md:grid-cols-5">
        <Stat label="1 日" value={<Pct value={signal.ret_1d} />} />
        <Stat label="3 日" value={<Pct value={signal.ret_3d} />} />
        <Stat label="5 日" value={<Pct value={signal.ret_5d} />} />
        <Stat label="最大不利" value={<span className="num">{fmtPct(signal.max_adverse_pct, 2, false)}</span>} />
        <Stat label="最大有利" value={<span className="num">{fmtPct(signal.max_favorable_pct, 2, false)}</span>} sub={`结果：${hitText}`} />
      </div>
      <div className="mt-4">
        <div className="mb-1 text-sm text-muted">这条信号对你有帮助吗？</div>
        <Textarea rows={2} placeholder="备注（可选）" value={note} onChange={(e) => setNote(e.target.value)} />
        <div className="mt-2 flex gap-2">
          <Button variant={feedback === 'useful' ? 'primary' : 'default'} loading={saving} onClick={() => void save('useful')}>有用</Button>
          <Button variant={feedback === 'not_useful' ? 'primary' : 'default'} loading={saving} onClick={() => void save('not_useful')}>没用</Button>
        </div>
      </div>
    </Modal>
  )
}
