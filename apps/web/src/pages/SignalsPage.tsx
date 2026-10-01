// 决策信号：诊断给出的买入/减仓等建议的生命周期（观察期、失效条件、状态）与后验表现，可反馈有用/没用
import { X } from 'lucide-react'
import { useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { api } from '@/api/endpoints'
import type { DecisionSignal, SkillPerformanceRow } from '@/api/types'
import { DataTable, type Column } from '@/components/DataTable'
import { StockSearch } from '@/components/StockSearch'
import { Badge, Button, Card, ErrorBox, Input, Modal, PageHeader, Pct, Select, Spinner, Stat, Tabs, Textarea } from '@/components/ui'
import { useApi } from '@/hooks/useApi'
import { useT } from '@/i18n'
import { useTask } from '@/hooks/useTask'
import { toast } from '@/stores/toast'
import { cn } from '@/utils/cn'
import { fmtNum, fmtPct, isNum, verdictClass } from '@/utils/format'

const PROFILE_FILTERS: Record<string, string> = { conservative: '保守', balanced: '均衡', aggressive: '进取', unknown: '旧数据' }

const PAGE_SIZE = 50
const ACTIONS: Record<string, string> = { buy: '买入', add: '加仓', reduce: '减仓', sell: '卖出', avoid: '回避' }
const STATUSES: Record<string, string> = {
  active: '有效', invalidated: '已失效', replaced: '被替代', expired: '已过期', hit_target: '止盈', hit_stop: '止损', closed: '已关闭',
}
const STATUS_TONE: Record<string, 'default' | 'up' | 'down' | 'warn' | 'accent'> = {
  active: 'accent', invalidated: 'warn', replaced: 'default', expired: 'default', hit_target: 'up', hit_stop: 'down', closed: 'default',
}
const DAYS = [{ v: 7, t: '近 7 天' }, { v: 30, t: '近 30 天' }, { v: 90, t: '近 90 天' }, { v: 0, t: '不限' }]

export function SignalsPage() {
  const t = useT()
  const [tab, setTab] = useState<'signals' | 'skills'>('signals')
  const [params, setParams] = useSearchParams()
  const code = params.get('code') ?? ''
  const [status, setStatus] = useState('')
  const [action, setAction] = useState('')
  const [profile, setProfile] = useState('')
  const [days, setDays] = useState(90)
  const [limit, setLimit] = useState(PAGE_SIZE)
  const [open, setOpen] = useState<DecisionSignal | null>(null)
  const task = useTask<Record<string, number>>()
  const stats = useApi(() => api.signalStats(days || 3650, profile || undefined), [days, profile])
  const list = useApi(
    () => api.signals({ status: status || undefined, action: action || undefined, code: code || undefined, profile: profile || undefined, days, limit, offset: 0 }),
    [status, action, code, profile, days, limit],
  )

  const setCode = (c: string) => {
    setLimit(PAGE_SIZE)
    setParams(c ? { code: c } : {})
  }
  const evaluate = () =>
    task
      .run(() => api.evaluateSignals(), { success: (r) => t('评估完成：{n} 条', { n: r.evaluated ?? 0 }) })
      .then(() => { void list.reload(); void stats.reload() })
      .catch(() => {})

  const columns: Column<DecisionSignal>[] = [
    { key: 'time', title: t('时间'), render: (r) => <span className="num text-xs">{r.created_at ?? r.trade_date}</span> },
    { key: 'stock', title: t('股票'), render: (r) => <>{r.name} <span className="num text-xs text-muted">{r.code}</span></> },
    {
      key: 'action', title: t('建议'),
      render: (r) => <Badge className={cn(verdictClass(ACTIONS[r.action]))}>{t(ACTIONS[r.action] ?? r.action)}</Badge>,
    },
    { key: 'profile', title: t('风格'), render: (r) => <span className="text-xs">{r.profile ? t(PROFILE_FILTERS[r.profile] ?? r.profile_label ?? r.profile) : '--'}</span> },
    { key: 'score', title: t('评分'), align: 'right', render: (r) => <span className="num">{r.score ?? '--'}</span> },
    { key: 'horizon', title: t('观察期'), render: (r) => <span className="text-xs">{t('{n} 日', { n: r.horizon_days })}{r.expires_on ? ` · ${t('至 {date}', { date: r.expires_on })}` : ''}</span> },
    { key: 'status', title: t('状态'), render: (r) => <Badge tone={STATUS_TONE[r.status] ?? 'default'}>{t(STATUSES[r.status] ?? r.status)}</Badge> },
    { key: 'r1', title: t('1日'), align: 'right', render: (r) => <Pct value={r.ret_1d} /> },
    { key: 'r3', title: t('3日'), align: 'right', render: (r) => <Pct value={r.ret_3d} /> },
    { key: 'r5', title: t('5日'), align: 'right', render: (r) => <Pct value={r.ret_5d} /> },
    { key: 'adv', title: t('最大不利'), align: 'right', render: (r) => <span className="num">{fmtPct(r.max_adverse_pct, 2, false)}</span> },
  ]

  const s = stats.data
  return (
    <div>
      <PageHeader
        title={t('决策信号')}
        description={t('诊断给出的买入、加仓、减仓、卖出、回避建议会生成信号，带观察期和失效条件，之后按行情评估命中情况')}
        actions={<Button variant="primary" loading={task.running} onClick={evaluate}>{t('立即评估')}</Button>}
      />
      <Tabs
        value={tab}
        onChange={setTab}
        tabs={[{ key: 'signals', label: t('信号') }, { key: 'skills', label: t('策略表现') }]}
      />
      {tab === 'skills' && <SkillPerformance />}
      {tab === 'signals' && (
        <>
        <div className="mb-4 grid grid-cols-2 gap-3 md:grid-cols-4">
          <Stat label={t('信号总数')} value={s ? s.total : '--'} />
          <Stat label={t('命中率')} value={s && isNum(s.hit_rate) ? `${s.hit_rate.toFixed(1)}%` : '--'} />
          <Stat label={t('平均 5 日收益')} value={<Pct value={s?.avg_ret_5d} />} />
          <Stat label={t('平均最大不利波动')} value={<span className="num">{fmtPct(s?.avg_adverse, 2, false)}</span>} />
        </div>
        <Card className="mb-4" bodyClassName="flex flex-wrap items-center gap-3">
          {code ? (
            <span className="inline-flex items-center gap-1 rounded border border-accent/40 bg-accent/10 px-2 py-1 text-sm text-accent">
              {t('股票 {code}', { code })}
              <button type="button" aria-label={t('清除股票筛选')} onClick={() => setCode('')}><X className="size-3.5" /></button>
            </span>
          ) : (
            <StockSearch className="w-64" placeholder={t('按股票筛选')} onSelect={(x) => setCode(x.code)} />
          )}
          <Select aria-label={t('状态')} value={status} onChange={(e) => { setStatus(e.target.value); setLimit(PAGE_SIZE) }}>
            <option value="">{t('全部状态')}</option>
            {Object.entries(STATUSES).map(([k, v]) => <option key={k} value={k}>{t(v)}</option>)}
          </Select>
          <Select aria-label={t('建议')} value={action} onChange={(e) => { setAction(e.target.value); setLimit(PAGE_SIZE) }}>
            <option value="">{t('全部建议')}</option>
            {Object.entries(ACTIONS).map(([k, v]) => <option key={k} value={k}>{t(v)}</option>)}
          </Select>
          <Select aria-label={t('风格')} value={profile} onChange={(e) => { setProfile(e.target.value); setLimit(PAGE_SIZE) }}>
            <option value="">{t('全部风格')}</option>
            {Object.entries(PROFILE_FILTERS).map(([k, v]) => <option key={k} value={k}>{t(v)}</option>)}
          </Select>
          <Select aria-label={t('时间范围')} value={days} onChange={(e) => { setDays(Number(e.target.value)); setLimit(PAGE_SIZE) }}>
            {DAYS.map((d) => <option key={d.v} value={d.v}>{t(d.t)}</option>)}
          </Select>
          {list.data && <span className="text-xs text-muted">{t('共 {n} 条', { n: list.data.total })}</span>}
        </Card>
        {list.error && <ErrorBox message={list.error} onRetry={list.reload} />}
        {list.loading && !list.data ? <Spinner /> : list.data && (
          <Card bodyClassName="p-0">
            <DataTable columns={columns} rows={list.data.items} rowKey={(r) => r.id} onRowClick={setOpen} empty={t('还没有决策信号，做一次 AI 诊断后会自动生成')} />
            {list.data.items.length < list.data.total && (
              <div className="border-t border-line p-3 text-center">
                <Button loading={list.loading} onClick={() => setLimit(limit + PAGE_SIZE)}>{t('加载更多')}</Button>
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
  const t = useT()
  const perf = useApi(() => api.skillPerformance(90), [])
  const columns: Column<SkillPerformanceRow>[] = [
    { key: 'skill', title: t('策略'), render: (r) => r.display_name },
    { key: 'samples', title: t('样本'), align: 'right', render: (r) => <span className="num">{r.samples}</span> },
    { key: 'hits', title: t('命中'), align: 'right', render: (r) => <span className="num">{r.hits}</span> },
    { key: 'rate', title: t('命中率'), align: 'right', render: (r) => <span className="num">{r.hit_rate.toFixed(1)}%</span> },
    { key: 'ret', title: t('平均 5 日收益'), align: 'right', render: (r) => <Pct value={r.avg_ret} /> },
    { key: 'weight', title: t('当前权重'), align: 'right', render: (r) => <span className="num">{r.weight.toFixed(2)}</span> },
  ]
  if (perf.error) return <ErrorBox message={perf.error} onRetry={perf.reload} />
  if (perf.loading && !perf.data) return <Spinner />
  return (
    <Card bodyClassName="p-0">
      <DataTable columns={columns} rows={perf.data ?? []} rowKey={(r) => r.skill} empty={t('还没有策略会诊的后验样本，诊断 5 个交易日后开始统计')} />
      <p className="border-t border-line p-3 text-xs text-muted">{t('统计近 90 天各策略在个股诊断中的观点：看多后 5 日上涨、看空后 5 日下跌算命中，中性不统计；样本不足 20 时权重为 1.00。')}</p>
    </Card>
  )
}

function DetailModal({ signal, onClose, onSaved }: { signal: DecisionSignal; onClose: () => void; onSaved: () => void }) {
  const t = useT()
  const [feedback, setFeedback] = useState<string>(signal.feedback ?? '')
  const [note, setNote] = useState(signal.feedback_note ?? '')
  const [saving, setSaving] = useState(false)
  const [reason, setReason] = useState('')
  const [ending, setEnding] = useState('')

  const end = async (status: 'closed' | 'invalidated') => {
    setEnding(status)
    try {
      await api.setSignalStatus(signal.id, status, reason)
      toast.success(status === 'closed' ? t('已关闭信号') : t('已作废信号'))
      onSaved()
      onClose()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : t('保存失败'))
    } finally {
      setEnding('')
    }
  }
  const save = async (value: 'useful' | 'not_useful') => {
    setSaving(true)
    try {
      await api.signalFeedback(signal.id, value, note)
      setFeedback(value)
      toast.success(t('已记录反馈'))
      onSaved()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : t('保存失败'))
    } finally {
      setSaving(false)
    }
  }
  const entry = isNum(signal.entry_low) || isNum(signal.entry_high)
    ? (signal.entry_low === signal.entry_high ? fmtNum(signal.entry_low) : `${fmtNum(signal.entry_low)} ~ ${fmtNum(signal.entry_high)}`)
    : '--'
  const hitText = signal.hit === true ? t('命中') : signal.hit === false ? t('未命中') : '--'
  return (
    <Modal open wide title={`${signal.name}(${signal.code}) · ${t(ACTIONS[signal.action] ?? signal.action)}`} onClose={onClose}>
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Stat label={t('买点')} value={entry} />
        <Stat label={t('止损')} value={fmtNum(signal.stop_loss)} />
        <Stat label={t('目标')} value={fmtNum(signal.target_price)} />
        <Stat label={t('观察期')} value={t('{n} 日', { n: signal.horizon_days })} sub={signal.expires_on ? t('至 {date}', { date: signal.expires_on }) : undefined} />
      </div>
      <dl className="mt-4 space-y-2 text-sm">
        <div><dt className="inline text-muted">{t('状态：')}</dt><dd className="inline"><Badge tone={STATUS_TONE[signal.status] ?? 'default'}>{t(STATUSES[signal.status] ?? signal.status)}</Badge> {signal.status_reason}</dd></div>
        <div><dt className="inline text-muted">{t('失效条件：')}</dt><dd className="inline">{signal.invalidation || '--'}</dd></div>
        <div><dt className="inline text-muted">{t('诊断行情日：')}</dt><dd className="num inline">{signal.trade_date}</dd>
          <span className="ml-3 text-muted">{t('评分')}</span> <span className="num">{signal.score ?? '--'}</span>
          {signal.confidence && <span className="ml-3 text-muted">{t('信心')} {signal.confidence}</span>}</div>
      </dl>
      <div className="mt-4 grid grid-cols-2 gap-3 md:grid-cols-5">
        <Stat label={t('1 日')} value={<Pct value={signal.ret_1d} />} />
        <Stat label={t('3 日')} value={<Pct value={signal.ret_3d} />} />
        <Stat label={t('5 日')} value={<Pct value={signal.ret_5d} />} />
        <Stat label={t('最大不利')} value={<span className="num">{fmtPct(signal.max_adverse_pct, 2, false)}</span>} />
        <Stat label={t('最大有利')} value={<span className="num">{fmtPct(signal.max_favorable_pct, 2, false)}</span>} sub={t('结果：{r}', { r: hitText })} />
      </div>
      {signal.status === 'active' && (
        <div className="mt-4">
          <div className="mb-1 text-sm text-muted">{t('手动结束这条信号（之后不再评估）')}</div>
          <div className="flex flex-wrap gap-2">
            <Input className="min-w-0 flex-1" placeholder={t('原因（可选），如：已卖出、逻辑变了')} value={reason} onChange={(e) => setReason(e.target.value)} />
            <Button loading={ending === 'closed'} onClick={() => void end('closed')}>{t('关闭信号')}</Button>
            <Button variant="danger" loading={ending === 'invalidated'} onClick={() => void end('invalidated')}>{t('作废信号')}</Button>
          </div>
        </div>
      )}
      <div className="mt-4">
        <div className="mb-1 text-sm text-muted">{t('这条信号对你有帮助吗？')}</div>
        <Textarea rows={2} placeholder={t('备注（可选）')} value={note} onChange={(e) => setNote(e.target.value)} />
        <div className="mt-2 flex gap-2">
          <Button variant={feedback === 'useful' ? 'primary' : 'default'} loading={saving} onClick={() => void save('useful')}>{t('有用')}</Button>
          <Button variant={feedback === 'not_useful' ? 'primary' : 'default'} loading={saving} onClick={() => void save('not_useful')}>{t('没用')}</Button>
        </div>
      </div>
    </Modal>
  )
}
