// 信号绩效：交易信号 1/3/5 日表现与止损止盈模拟；AI 诊断事后验证
import { useState } from 'react'
import { api } from '@/api/endpoints'
import type { DiagnosisOutcomeDetail, DiagnosisOutcomeRow, SignalDetail, SignalSummaryRow } from '@/api/types'
import { DataTable, type Column } from '@/components/DataTable'
import { Button, Card, ErrorBox, PageHeader, Pct, Select, Spinner, Tabs } from '@/components/ui'
import { useApi } from '@/hooks/useApi'
import { fmtNum } from '@/utils/format'

const rate = (v: number | null) => <Pct value={v} digits={1} signed={false} />
const EXIT: Record<string, string> = { stop_loss: '止损', ambiguous_stop_loss: '止损(同日触及)', take_profit: '止盈', window_end: '到期' }
const TYPE: Record<string, string> = { premarket: 'AI预测', buy: '评分信号' }

export function PerformancePage() {
  const [tab, setTab] = useState<'signals' | 'diagnosis'>('signals')
  const [days, setDays] = useState(60)
  const signals = useApi(() => api.signalPerformance(days), [days])
  const outcomes = useApi(() => api.diagnosisOutcomes(days), [days])

  const summaryColumns: Column<SignalSummaryRow>[] = [
    { key: 'dim', title: '维度', render: (r) => <span className="text-muted">{r.dimension}</span> },
    { key: 'group', title: '分组', render: (r) => r.group },
    { key: 'total', title: '信号数', align: 'right', render: (r) => <span className="num">{r.total}</span> },
    { key: 'eval', title: '已评估', align: 'right', render: (r) => <span className="num">{r.evaluated}</span> },
    { key: 'lu', title: '涨停命中', align: 'right', render: (r) => rate(r.limit_up_rate) },
    { key: 'w1', title: '1日胜率', align: 'right', render: (r) => rate(r.win_rate_1d) },
    { key: 'r1', title: '1日均收益', align: 'right', render: (r) => <Pct value={r.avg_return_1d} /> },
    { key: 'w3', title: '3日胜率', align: 'right', render: (r) => rate(r.win_rate_3d) },
    { key: 'r3', title: '3日均收益', align: 'right', render: (r) => <Pct value={r.avg_return_3d} /> },
    { key: 'w5', title: '5日胜率', align: 'right', render: (r) => rate(r.win_rate_5d) },
    { key: 'r5', title: '5日均收益', align: 'right', render: (r) => <Pct value={r.avg_return_5d} /> },
    { key: 'sim', title: '模拟收益', align: 'right', render: (r) => <Pct value={r.simulated_avg} /> },
    { key: 'sl', title: '止损率', align: 'right', render: (r) => rate(r.stop_loss_rate) },
    { key: 'tp', title: '止盈率', align: 'right', render: (r) => rate(r.take_profit_rate) },
  ]
  const detailColumns: Column<SignalDetail>[] = [
    { key: 'date', title: '信号日/验证日', render: (d) => <span className="num text-xs">{d.signal_date} → {d.eval_date}</span> },
    { key: 'stock', title: '股票', render: (d) => <>{d.name} <span className="num text-xs text-muted">{d.code}</span></> },
    { key: 'type', title: '类型', render: (d) => TYPE[d.signal_type] ?? d.signal_type },
    { key: 'source', title: '来源', render: (d) => <span className="text-muted">{d.source}</span> },
    { key: 'verdict', title: '研判', render: (d) => d.verdict },
    { key: 'entry', title: '入场', align: 'right', render: (d) => (d.entry_price ? <span className="num">{fmtNum(d.entry_price)}（{d.entry_at === 'open' ? '开盘' : '收盘'}）</span> : <span className="text-muted">{d.status === 'pending' ? '待验证' : '无行情'}</span>) },
    ...[1, 3, 5].map((h) => ({ key: `r${h}`, title: `${h}日`, align: 'right' as const, render: (d: SignalDetail) => <Pct value={d.returns?.[h]} /> })),
    { key: 'lu', title: '涨停', align: 'center', render: (d) => (d.hit_limit_up == null ? '--' : d.hit_limit_up ? '是' : '否') },
    { key: 'exit', title: '离场', render: (d) => EXIT[d.exit_reason ?? ''] ?? '--' },
    { key: 'sim', title: '模拟收益', align: 'right', render: (d) => <Pct value={d.simulated_return} /> },
  ]
  const outcomeColumns: Column<DiagnosisOutcomeRow>[] = [
    { key: 'dim', title: '维度', render: (r) => <span className="text-muted">{r.dimension}</span> },
    { key: 'group', title: '分组', render: (r) => r.group },
    { key: 'total', title: '诊断数', align: 'right', render: (r) => <span className="num">{r.total}</span> },
    { key: 'eval', title: '已验证', align: 'right', render: (r) => <span className="num">{r.evaluated}</span> },
    ...[1, 3, 5].flatMap((h) => [
      { key: `a${h}`, title: `${h}日准确率`, align: 'right' as const, render: (r: DiagnosisOutcomeRow) => rate(r[`accuracy_${h}d` as 'accuracy_1d']) },
      { key: `v${h}`, title: `${h}日均涨跌`, align: 'right' as const, render: (r: DiagnosisOutcomeRow) => <Pct value={r[`avg_${h}d` as 'avg_1d']} /> },
    ]),
    { key: 'plan', title: '止盈先到', align: 'right', render: (r) => rate(r.target_first_rate) },
  ]
  const outcomeDetailColumns: Column<DiagnosisOutcomeDetail>[] = [
    { key: 'date', title: '行情日', render: (d) => <span className="num text-xs">{d.trade_date}</span> },
    { key: 'stock', title: '股票', render: (d) => <>{d.name} <span className="num text-xs text-muted">{d.code}</span></> },
    { key: 'action', title: '建议', render: (d) => d.action_label },
    { key: 'score', title: '评分', align: 'right', render: (d) => <span className="num">{d.score}</span> },
    { key: 'base', title: '基准价', align: 'right', render: (d) => <span className="num">{fmtNum(d.base_close)}</span> },
    ...([1, 3, 5] as const).map((h) => ({
      key: `r${h}`, title: `${h}日`, align: 'right' as const, render: (d: DiagnosisOutcomeDetail) => {
        const hit = d[`hit${h}` as 'hit1']
        return <><Pct value={d[`r${h}` as 'r1']} />{hit != null && <span className={hit ? 'text-down' : 'text-danger'}> {hit ? '✓' : '✗'}</span>}</>
      },
    })),
    { key: 'plan', title: '价格计划', render: (d) => d.plan || '--' },
  ]

  return (
    <div>
      <PageHeader
        title="信号绩效"
        description="开盘前信号按验证日开盘价入场，盘中信号按收盘价；止损止盈按 T+1 从入场次日判断"
        actions={
          <Select value={days} onChange={(e) => setDays(Number(e.target.value))} aria-label="统计天数">
            {[30, 60, 90, 180].map((d) => <option key={d} value={d}>近 {d} 天</option>)}
          </Select>
        }
      />
      <Card bodyClassName="p-3">
        <Tabs value={tab} onChange={setTab} tabs={[{ key: 'signals', label: '交易信号' }, { key: 'diagnosis', label: 'AI 诊断验证' }]} />
        {tab === 'signals' && (
          <>
            {signals.error && <ErrorBox message={signals.error} onRetry={signals.reload} />}
            {signals.loading && !signals.data ? <Spinner text="计算中…" /> : (
              <div className="space-y-4">
                <DataTable columns={summaryColumns} rows={signals.data?.summary ?? []} rowKey={(r) => `${r.dimension}-${r.group}`} />
                <h3 className="text-sm font-medium text-accent">信号明细</h3>
                <DataTable columns={detailColumns} rows={(signals.data?.details ?? []).slice(0, 300)} rowKey={(d, i) => `${d.code}-${d.signal_date}-${i}`} maxHeight="50vh" />
              </div>
            )}
          </>
        )}
        {tab === 'diagnosis' && (
          <>
            <p className="mb-2 text-xs text-muted">以诊断行情日收盘价为基准：买入/加仓看多、减仓/卖出/回避看空，持有/观望不判方向；同一行情日多次诊断只算最后一次</p>
            {outcomes.loading && !outcomes.data ? <Spinner /> : (
              <div className="space-y-4">
                <DataTable columns={outcomeColumns} rows={outcomes.data?.summary ?? []} rowKey={(r) => `${r.dimension}-${r.group}`} empty="还没有 AI 诊断记录" />
                <DataTable columns={outcomeDetailColumns} rows={(outcomes.data?.details ?? []).slice(0, 300)} rowKey={(d, i) => `${d.code}-${d.trade_date}-${i}`} maxHeight="50vh" empty="" />
              </div>
            )}
          </>
        )}
        <div className="mt-3 flex justify-end"><Button variant="ghost" onClick={() => { void signals.reload(); void outcomes.reload() }}>重新计算</Button></div>
      </Card>
    </div>
  )
}
