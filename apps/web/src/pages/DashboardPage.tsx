// 交易决策：市场概况、大盘环境、交易焦点、AI 涨停预测、综合评分、涨停池
import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api } from '@/api/endpoints'
import type { Dashboard, LimitUpStock, MarketOverview, Prediction, TopStock, TradeFocusRow } from '@/api/types'
import { DataTable, type Column } from '@/components/DataTable'
import { Badge, Button, Card, ErrorBox, PageHeader, Pct, Spinner, Stat, Tabs } from '@/components/ui'
import { useApi } from '@/hooks/useApi'
import { progressText, useTask } from '@/hooks/useTask'
import { useT } from '@/i18n'
import { fmtAmount, fmtNum, RECOMMENDATION_LABELS, verdictClass } from '@/utils/format'

type TabKey = 'focus' | 'predictions' | 'top' | 'limit_up'

const SETUP_DISMISS_KEY = 'setup_hint_dismissed'

function readDismissed(): boolean {
  try {
    return localStorage.getItem(SETUP_DISMISS_KEY) === '1'
  } catch {
    return false
  }
}

/** 配置未完成提示条：必需项未完成时始终显示，仅可选项未完成时可「暂不提示」 */
function SetupBanner() {
  const t = useT()
  const { data } = useApi(api.setupStatus)
  const [dismissed, setDismissed] = useState(readDismissed)
  if (!data || data.done >= data.total) return null
  if (dismissed && data.required_missing === 0) return null
  const missing = data.total - data.done
  return (
    <div className="mb-3 flex flex-wrap items-center gap-3 rounded-md border border-line bg-panel px-3 py-2 text-sm">
      <span>{t('还有 {n} 项配置未完成', { n: missing })}{data.required_missing > 0 ? t('（其中必需 {n} 项）', { n: data.required_missing }) : ''}</span>
      <Link to="/setup" className="text-accent hover:underline">{t('去配置')}</Link>
      {data.required_missing === 0 && (
        <button
          type="button"
          className="text-muted hover:text-text"
          onClick={() => {
            try {
              localStorage.setItem(SETUP_DISMISS_KEY, '1')
            } catch {
              /* 存储不可用时仅本次隐藏 */
            }
            setDismissed(true)
          }}
        >
          {t('暂不提示')}
        </button>
      )}
    </div>
  )
}

const REGIME_TONE: Record<string, 'up' | 'warn' | 'down' | 'default'> = { 进攻: 'up', 均衡: 'warn', 防守: 'down', 冰点: 'down' }

function OverviewStats({ ov }: { ov: MarketOverview }) {
  const t = useT()
  const indices: [string, unknown, unknown][] = [
    ['上证指数', ov.sh_index, ov.sh_change_pct],
    ['深证成指', ov.sz_index, ov.sz_change_pct],
    ['创业板指', ov.cy_index, ov.cy_change_pct],
  ]
  return (
    <div className="grid grid-cols-2 gap-2 sm:grid-cols-4 lg:grid-cols-8">
      {indices.map(([label, value, pct]) => (
        <Stat key={label} label={t(label)} value={String(value ?? '--')} sub={<Pct value={pct} />} />
      ))}
      <Stat label={t('涨 / 跌')} value={<><span className="text-up">{ov.up_count ?? '--'}</span> / <span className="text-down">{ov.down_count ?? '--'}</span></>} />
      <Stat label={t('涨停 / 跌停')} value={<><span className="text-up">{ov.limit_up_count ?? '--'}</span> / <span className="text-down">{ov.limit_down_count ?? '--'}</span></>} />
      <Stat label={t('两市成交额')} value={ov.total_amount_yi ? t('{v}万亿', { v: (ov.total_amount_yi / 10000).toFixed(2) }) : '--'} />
      <Stat label={t('北向资金')} value={ov.northbound_net_yi != null ? t('{v}亿', { v: ov.northbound_net_yi.toFixed(1) }) : '--'} />
      <Stat label={t('市场情绪')} value={ov.market_emotion || '--'} />
    </div>
  )
}

export function DashboardPage() {
  const t = useT()
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
    { key: 'name', title: t('股票'), render: (r) => <><div>{r.name}</div><div className="num text-xs text-muted">{r.code}</div></> },
    { key: 'score', title: t('综合分'), align: 'right', render: (r) => <span className="num">{fmtNum(r.composite_score, 1)}</span> },
    { key: 'change', title: t('涨幅'), align: 'right', render: (r) => <Pct value={r.change_pct} /> },
    { key: 'days', title: t('连板'), align: 'center', render: (r) => (r.continuous_days ? t('{n}板', { n: r.continuous_days }) : '') },
    { key: 'sector', title: t('板块'), render: (r) => <span className="text-muted">{r.sector}</span> },
    { key: 'verdict', title: t('AI 研判'), render: (r) => <span className={verdictClass(r.ai_verdict || RECOMMENDATION_LABELS[r.recommendation])}>{t(r.ai_verdict || RECOMMENDATION_LABELS[r.recommendation] || '--')}</span> },
    { key: 'advice', title: t('理由 / 建议'), className: 'max-w-md text-xs text-muted', render: (r) => r.ai_advice || r.signal_reason || r.limit_reason || r.reason },
  ]
  const predictionColumns: Column<Prediction>[] = [
    { key: 'rank', title: '#', align: 'right', render: (r) => <span className="num text-muted">{r.rank}</span> },
    { key: 'name', title: t('股票'), render: (r) => <><div>{r.name}</div><div className="num text-xs text-muted">{r.code}</div></> },
    { key: 'confidence', title: t('信心'), align: 'right', render: (r) => <span className="num">{fmtNum(r.confidence, 0)}</span> },
    { key: 'verdict', title: t('研判'), render: (r) => <span className={verdictClass(r.ai_verdict)}>{t(r.ai_verdict)}</span> },
    { key: 'type', title: t('类型'), render: (r) => <Badge tone="accent">{t(r.predict_type || '--')}</Badge> },
    { key: 'source', title: t('来源'), render: (r) => <span className="text-muted">{r.source}</span> },
    { key: 'time', title: t('操作时机'), render: (r) => <span className="text-xs">{r.target_time}</span> },
    { key: 'reason', title: t('逻辑'), className: 'max-w-md text-xs text-muted', render: (r) => r.reason },
  ]
  const topColumns: Column<TopStock>[] = [
    { key: 'rank', title: '#', align: 'right', render: (r) => <span className="num text-muted">{r.rank}</span> },
    { key: 'name', title: t('股票'), render: (r) => <><div>{r.name}</div><div className="num text-xs text-muted">{r.code}</div></> },
    { key: 'score', title: t('综合分'), align: 'right', render: (r) => <span className="num font-semibold">{fmtNum(r.composite_score, 1)}</span> },
    { key: 'rec', title: t('建议'), render: (r) => <span className={verdictClass(RECOMMENDATION_LABELS[r.recommendation])}>{t(RECOMMENDATION_LABELS[r.recommendation] ?? r.recommendation)}</span> },
    ...(['sentiment_score', 'limit_up_score', 'capital_score', 'tech_score', 'global_score'] as const).map((k, i) => ({
      key: k,
      title: t(['舆情', '涨停', '资金', '技术', '国际'][i]),
      align: 'right' as const,
      render: (r: TopStock) => <span className="num text-muted">{fmtNum(r[k], 0)}</span>,
    })),
  ]
  const limitColumns: Column<LimitUpStock>[] = [
    { key: 'name', title: t('股票'), render: (r) => <><div>{r.name}</div><div className="num text-xs text-muted">{r.code}</div></> },
    { key: 'days', title: t('连板'), align: 'center', render: (r) => <Badge tone={r.continuous_days >= 2 ? 'up' : 'default'}>{t('{n}板', { n: r.continuous_days })}</Badge> },
    { key: 'first', title: t('首封'), render: (r) => <span className="num">{r.first_limit_time}</span> },
    { key: 'open', title: t('炸板'), align: 'right', render: (r) => <span className="num">{r.open_count}</span> },
    { key: 'seal', title: t('封单'), align: 'right', render: (r) => <span className="num">{fmtAmount(r.seal_amount)}</span> },
    { key: 'mv', title: t('流通市值'), align: 'right', render: (r) => <span className="num">{fmtAmount(r.circ_mv)}</span> },
    { key: 'sector', title: t('行业'), render: (r) => <span className="text-muted">{r.sector}</span> },
    { key: 'reason', title: t('涨停原因'), className: 'max-w-sm text-xs text-muted', render: (r) => r.reason },
  ]

  return (
    <div>
      <SetupBanner />
      <PageHeader
        title={t('交易决策')}
        description={data ? t('评分日期 {a} ｜ 涨停池 {b}', { a: data.score_date, b: data.limit_up_date })
          + (data.market_overview?.trade_date ? t(' ｜ 涨跌统计 {c}', { c: data.market_overview.trade_date }) : '') : undefined}
        actions={
          <>
            <Button loading={collect.running} onClick={() => collect.run(api.collect, { success: t('数据采集完成') }).then(reload).catch(() => {})}>
              {collect.running ? t('采集中 {p}', { p: progressText(collect.progress) }) : t('采集数据')}
            </Button>
            <Button variant="primary" loading={predict.running} onClick={() => predict.run(api.predict, { success: t('AI 涨停预测完成') }).then(() => { setTab('predictions'); void reload() }).catch(() => {})}>
              {t('AI 涨停预测')}
            </Button>
            <Button loading={report.running} onClick={() => report.run(api.pushDailyReport, { success: (r) => ((r as { pushed?: boolean })?.pushed ? t('日报已推送') : t('日报未推送（没有启用推送渠道）')) }).catch(() => {})}>
              {t('推送日报')}
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
              <Badge tone={REGIME_TONE[regime.data.regime] ?? 'default'}>{t(regime.data.regime)}</Badge>
              <span>{regime.data.summary}</span>
            </div>
          )}
          {data.market_overview?.ai_market_comment && <div className="text-sm text-muted">{t('AI 点评：')}{data.market_overview.ai_market_comment}</div>}
          <Card bodyClassName="p-3">
            <Tabs<TabKey>
              value={tab}
              onChange={setTab}
              tabs={[
                { key: 'focus', label: `${t('交易焦点')} (${(data.trade_focus ?? []).length})` },
                { key: 'predictions', label: `${t('AI 涨停预测')} (${(data.premarket_predictions ?? []).length})` },
                { key: 'top', label: `${t('综合评分')} (${(data.top_stocks ?? []).length})` },
                { key: 'limit_up', label: `${t('涨停池')} (${data.limit_up_count})` },
              ]}
            />
            {tab === 'focus' && <DataTable columns={focusColumns} rows={data.trade_focus ?? []} rowKey={(r) => r.code} onRowClick={(r) => open(r.code)} empty={t('今日还没有评分数据，先采集数据')} />}
            {tab === 'predictions' && <DataTable columns={predictionColumns} rows={data.premarket_predictions ?? []} rowKey={(r) => r.code} onRowClick={(r) => open(r.code)} empty={t('还没有 AI 涨停预测，点右上角「AI 涨停预测」')} />}
            {tab === 'top' && <DataTable columns={topColumns} rows={data.top_stocks ?? []} rowKey={(r) => r.code} onRowClick={(r) => open(r.code)} />}
            {tab === 'limit_up' && <DataTable columns={limitColumns} rows={data.limit_up_stocks ?? []} rowKey={(r) => r.code} onRowClick={(r) => open(r.code)} />}
          </Card>
        </div>
      )}
    </div>
  )
}
