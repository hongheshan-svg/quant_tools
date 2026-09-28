// 交易决策：市场概况、大盘环境、交易焦点、AI 涨停预测、综合评分、涨停池
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '@/api/endpoints'
import type { Dashboard, LimitUpStock, MarketOverview, Prediction, TopStock, TradeFocusRow } from '@/api/types'
import { DataTable, type Column } from '@/components/DataTable'
import { Badge, Button, Card, ErrorBox, PageHeader, Pct, Spinner, Stat, Tabs } from '@/components/ui'
import { useApi } from '@/hooks/useApi'
import { progressText, useTask } from '@/hooks/useTask'
import { fmtAmount, fmtNum, RECOMMENDATION_LABELS, verdictClass } from '@/utils/format'

type TabKey = 'focus' | 'predictions' | 'top' | 'limit_up'

const REGIME_TONE: Record<string, 'up' | 'warn' | 'down' | 'default'> = { 进攻: 'up', 均衡: 'warn', 防守: 'down', 冰点: 'down' }

function OverviewStats({ ov }: { ov: MarketOverview }) {
  const indices: [string, unknown, unknown][] = [
    ['上证指数', ov.sh_index, ov.sh_change_pct],
    ['深证成指', ov.sz_index, ov.sz_change_pct],
    ['创业板指', ov.cy_index, ov.cy_change_pct],
  ]
  return (
    <div className="grid grid-cols-2 gap-2 sm:grid-cols-4 lg:grid-cols-8">
      {indices.map(([label, value, pct]) => (
        <Stat key={label} label={label} value={String(value ?? '--')} sub={<Pct value={pct} />} />
      ))}
      <Stat label="涨 / 跌" value={<><span className="text-up">{ov.up_count ?? '--'}</span> / <span className="text-down">{ov.down_count ?? '--'}</span></>} />
      <Stat label="涨停 / 跌停" value={<><span className="text-up">{ov.limit_up_count ?? '--'}</span> / <span className="text-down">{ov.limit_down_count ?? '--'}</span></>} />
      <Stat label="两市成交额" value={ov.total_amount_yi ? `${(ov.total_amount_yi / 10000).toFixed(2)}万亿` : '--'} />
      <Stat label="北向资金" value={ov.northbound_net_yi != null ? `${ov.northbound_net_yi.toFixed(1)}亿` : '--'} />
      <Stat label="市场情绪" value={ov.market_emotion || '--'} />
    </div>
  )
}

export function DashboardPage() {
  const navigate = useNavigate()
  const { data, error, loading, reload } = useApi<Dashboard>(api.dashboard)
  const regime = useApi(api.regime)
  const [tab, setTab] = useState<TabKey>('focus')
  const collect = useTask()
  const predict = useTask()
  const report = useTask()
  const open = (code: string) => navigate(`/stocks/${code}`)

  const focusColumns: Column<TradeFocusRow>[] = [
    { key: 'rank', title: '#', align: 'right', render: (r) => <span className="num text-muted">{r.rank}</span> },
    { key: 'name', title: '股票', render: (r) => <><div>{r.name}</div><div className="num text-xs text-muted">{r.code}</div></> },
    { key: 'score', title: '综合分', align: 'right', render: (r) => <span className="num">{fmtNum(r.composite_score, 1)}</span> },
    { key: 'change', title: '涨幅', align: 'right', render: (r) => <Pct value={r.change_pct} /> },
    { key: 'days', title: '连板', align: 'center', render: (r) => (r.continuous_days ? `${r.continuous_days}板` : '') },
    { key: 'sector', title: '板块', render: (r) => <span className="text-muted">{r.sector}</span> },
    { key: 'verdict', title: 'AI 研判', render: (r) => <span className={verdictClass(r.ai_verdict || RECOMMENDATION_LABELS[r.recommendation])}>{r.ai_verdict || RECOMMENDATION_LABELS[r.recommendation] || '--'}</span> },
    { key: 'advice', title: '理由 / 建议', className: 'max-w-md text-xs text-muted', render: (r) => r.ai_advice || r.signal_reason || r.limit_reason || r.reason },
  ]
  const predictionColumns: Column<Prediction>[] = [
    { key: 'rank', title: '#', align: 'right', render: (r) => <span className="num text-muted">{r.rank}</span> },
    { key: 'name', title: '股票', render: (r) => <><div>{r.name}</div><div className="num text-xs text-muted">{r.code}</div></> },
    { key: 'confidence', title: '信心', align: 'right', render: (r) => <span className="num">{fmtNum(r.confidence, 0)}</span> },
    { key: 'verdict', title: '研判', render: (r) => <span className={verdictClass(r.ai_verdict)}>{r.ai_verdict}</span> },
    { key: 'type', title: '类型', render: (r) => <Badge tone="accent">{r.predict_type || '--'}</Badge> },
    { key: 'source', title: '来源', render: (r) => <span className="text-muted">{r.source}</span> },
    { key: 'time', title: '操作时机', render: (r) => <span className="text-xs">{r.target_time}</span> },
    { key: 'reason', title: '逻辑', className: 'max-w-md text-xs text-muted', render: (r) => r.reason },
  ]
  const topColumns: Column<TopStock>[] = [
    { key: 'rank', title: '#', align: 'right', render: (r) => <span className="num text-muted">{r.rank}</span> },
    { key: 'name', title: '股票', render: (r) => <><div>{r.name}</div><div className="num text-xs text-muted">{r.code}</div></> },
    { key: 'score', title: '综合分', align: 'right', render: (r) => <span className="num font-semibold">{fmtNum(r.composite_score, 1)}</span> },
    { key: 'rec', title: '建议', render: (r) => <span className={verdictClass(RECOMMENDATION_LABELS[r.recommendation])}>{RECOMMENDATION_LABELS[r.recommendation] ?? r.recommendation}</span> },
    ...(['sentiment_score', 'limit_up_score', 'capital_score', 'tech_score', 'global_score'] as const).map((k, i) => ({
      key: k,
      title: ['舆情', '涨停', '资金', '技术', '国际'][i],
      align: 'right' as const,
      render: (r: TopStock) => <span className="num text-muted">{fmtNum(r[k], 0)}</span>,
    })),
  ]
  const limitColumns: Column<LimitUpStock>[] = [
    { key: 'name', title: '股票', render: (r) => <><div>{r.name}</div><div className="num text-xs text-muted">{r.code}</div></> },
    { key: 'days', title: '连板', align: 'center', render: (r) => <Badge tone={r.continuous_days >= 2 ? 'up' : 'default'}>{r.continuous_days}板</Badge> },
    { key: 'first', title: '首封', render: (r) => <span className="num">{r.first_limit_time}</span> },
    { key: 'open', title: '炸板', align: 'right', render: (r) => <span className="num">{r.open_count}</span> },
    { key: 'seal', title: '封单', align: 'right', render: (r) => <span className="num">{fmtAmount(r.seal_amount)}</span> },
    { key: 'mv', title: '流通市值', align: 'right', render: (r) => <span className="num">{fmtAmount(r.circ_mv)}</span> },
    { key: 'sector', title: '行业', render: (r) => <span className="text-muted">{r.sector}</span> },
    { key: 'reason', title: '涨停原因', className: 'max-w-sm text-xs text-muted', render: (r) => r.reason },
  ]

  return (
    <div>
      <PageHeader
        title="交易决策"
        description={data ? `评分日期 ${data.score_date} ｜ 涨停池 ${data.limit_up_date}` : undefined}
        actions={
          <>
            <Button loading={collect.running} onClick={() => collect.run(api.collect, { success: '数据采集完成' }).then(reload).catch(() => {})}>
              {collect.running ? `采集中 ${progressText(collect.progress)}` : '采集数据'}
            </Button>
            <Button variant="primary" loading={predict.running} onClick={() => predict.run(api.predict, { success: 'AI 涨停预测完成' }).then(() => { setTab('predictions'); void reload() }).catch(() => {})}>
              AI 涨停预测
            </Button>
            <Button loading={report.running} onClick={() => report.run(api.pushDailyReport, { success: (r) => ((r as { pushed?: boolean })?.pushed ? '日报已推送' : '日报未推送（没有启用推送渠道）') }).catch(() => {})}>
              推送日报
            </Button>
          </>
        }
      />
      {error && <ErrorBox message={error} onRetry={reload} />}
      {loading && !data && <Spinner />}
      {data && (
        <div className="space-y-4">
          <OverviewStats ov={data.market_overview ?? {}} />
          {regime.data && regime.data.regime !== '未知' && (
            <div className="flex flex-wrap items-center gap-2 rounded-md border border-line bg-panel px-3 py-2 text-sm">
              <Badge tone={REGIME_TONE[regime.data.regime] ?? 'default'}>{regime.data.regime}</Badge>
              <span>{regime.data.summary}</span>
            </div>
          )}
          {data.market_overview?.ai_market_comment && <div className="text-sm text-muted">AI 点评：{data.market_overview.ai_market_comment}</div>}
          <Card bodyClassName="p-3">
            <Tabs<TabKey>
              value={tab}
              onChange={setTab}
              tabs={[
                { key: 'focus', label: `交易焦点 (${data.trade_focus.length})` },
                { key: 'predictions', label: `AI 涨停预测 (${data.premarket_predictions.length})` },
                { key: 'top', label: `综合评分 (${data.top_stocks.length})` },
                { key: 'limit_up', label: `涨停池 (${data.limit_up_count})` },
              ]}
            />
            {tab === 'focus' && <DataTable columns={focusColumns} rows={data.trade_focus} rowKey={(r) => r.code} onRowClick={(r) => open(r.code)} empty="今日还没有评分数据，先采集数据" />}
            {tab === 'predictions' && <DataTable columns={predictionColumns} rows={data.premarket_predictions} rowKey={(r) => r.code} onRowClick={(r) => open(r.code)} empty="还没有 AI 涨停预测，点右上角「AI 涨停预测」" />}
            {tab === 'top' && <DataTable columns={topColumns} rows={data.top_stocks} rowKey={(r) => r.code} onRowClick={(r) => open(r.code)} />}
            {tab === 'limit_up' && <DataTable columns={limitColumns} rows={data.limit_up_stocks} rowKey={(r) => r.code} onRowClick={(r) => open(r.code)} />}
          </Card>
        </div>
      )}
    </div>
  )
}
