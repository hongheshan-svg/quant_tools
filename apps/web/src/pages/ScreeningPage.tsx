// 策略选股：选股结果（含次日涨幅）、近 30 天策略次日表现、历史回测
import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '@/api/endpoints'
import type { BacktestReport, BacktestStrategy, ScreeningDate, ScreeningPick, ScreenResult, StrategyPerformance } from '@/api/types'
import { DataTable, type Column } from '@/components/DataTable'
import { Badge, Button, Card, ErrorBox, PageHeader, Pct, Select, Spinner } from '@/components/ui'
import { useApi } from '@/hooks/useApi'
import { useT } from '@/i18n'
import { progressText, useTask } from '@/hooks/useTask'
import { fmtNum } from '@/utils/format'

const rate = (v: number | null) => <Pct value={v} digits={1} signed={false} />

export function ScreeningPage() {
  const t = useT()
  const navigate = useNavigate()
  const { data, error, loading, reload } = useApi(api.screening)
  const datesApi = useApi(api.screeningDates)
  const [selDate, setSelDate] = useState('')
  const [strategy, setStrategy] = useState('')
  const [histPicks, setHistPicks] = useState<ScreeningPick[] | null>(null)
  const [histError, setHistError] = useState('')
  const screen = useTask<ScreenResult>()
  const backtest = useTask<BacktestReport>()

  // 默认（最近一天、全部策略）直接用 /screening 的结果，切换日期或策略后请求 /screening/picks
  useEffect(() => {
    if (!selDate && !strategy) {
      setHistPicks(null)
      setHistError('')
      return
    }
    let cancelled = false
    api.screeningPicks(selDate || undefined, strategy || undefined)
      .then((r) => { if (!cancelled) { setHistPicks(r.picks); setHistError('') } })
      .catch((e) => { if (!cancelled) setHistError(e instanceof Error ? e.message : String(e)) })
    return () => { cancelled = true }
  }, [selDate, strategy])

  const refreshAll = () => {
    setSelDate('')
    setStrategy('')
    void datesApi.reload()
    return reload()
  }
  const dates = datesApi.data?.dates ?? []
  const strategyOptions = datesApi.data?.strategies ?? []
  const dateLabel = (d: ScreeningDate) => {
    const next = d.avg_next_pct == null ? '' : `，${t('次日均')} ${d.avg_next_pct > 0 ? '+' : ''}${d.avg_next_pct.toFixed(2)}%`
    return `${d.trade_date}（${t('{n} 只', { n: d.picks })}${next}）`
  }
  const histColumns: Column<ScreeningDate>[] = [
    { key: 'date', title: t('日期'), render: (d) => <span className="num">{d.trade_date}</span> },
    { key: 'picks', title: t('入选数'), align: 'right', render: (d) => <span className="num">{d.picks}</span> },
    { key: 'eval', title: t('已验证'), align: 'right', render: (d) => <span className="num">{d.evaluated}</span> },
    { key: 'avg', title: t('次日均涨幅'), align: 'right', render: (d) => <Pct value={d.avg_next_pct} /> },
    { key: 'win', title: t('次日上涨比例'), align: 'right', render: (d) => rate(d.win_rate) },
  ]
  const shownPicks = histPicks ?? data?.picks ?? []

  const pickColumns: Column<ScreeningPick>[] = [
    { key: 'stock', title: t('股票'), render: (p) => <><div>{p.name}</div><div className="num text-xs text-muted">{p.code}</div></> },
    { key: 'labels', title: t('策略'), render: (p) => <div className="flex flex-wrap gap-1">{p.labels.map((l) => <Badge key={l} tone="accent">{t(l)}</Badge>)}</div> },
    { key: 'score', title: t('得分'), align: 'right', render: (p) => <span className="num">{fmtNum(p.score, 0)}</span> },
    { key: 'fit', title: t('大盘适配'), render: (p) => (p.fits_regime ? <span className="text-down">{t('适配')}</span> : <span className="text-muted">{t('不适配')}</span>) },
    { key: 'pct', title: t('当日涨幅'), align: 'right', render: (p) => <Pct value={p.change_pct} /> },
    { key: 'next', title: t('次日涨幅'), align: 'right', render: (p) => <Pct value={p.next_change_pct} /> },
    { key: 'reason', title: t('入选理由'), className: 'max-w-lg text-xs text-muted', render: (p) => p.reasons.join('；') },
  ]
  const perfColumns: Column<StrategyPerformance>[] = [
    { key: 'label', title: t('策略'), render: (r) => t(r.label) },
    { key: 'regimes', title: t('适配环境'), render: (r) => <span className="text-muted">{r.regimes}</span> },
    { key: 'picks', title: t('近30天入选'), align: 'right', render: (r) => <span className="num">{r.picks}</span> },
    { key: 'eval', title: t('已验证'), align: 'right', render: (r) => <span className="num">{r.evaluated}</span> },
    { key: 'avg', title: t('次日平均涨幅'), align: 'right', render: (r) => <Pct value={r.avg_next_pct} /> },
    { key: 'win', title: t('次日上涨比例'), align: 'right', render: (r) => rate(r.win_rate) },
    { key: 'lu', title: t('次日涨停比例'), align: 'right', render: (r) => rate(r.limit_up_rate) },
  ]
  const btColumns: Column<BacktestStrategy>[] = [
    { key: 'label', title: t('策略'), render: (r) => t(r.label) },
    { key: 'days', title: t('天数/入选'), align: 'right', render: (r) => <span className="num">{r.days}/{r.picks}</span> },
    { key: 'a1', title: t('次日均收益'), align: 'right', render: (r) => <Pct value={r.avg_1d} /> },
    { key: 'w1', title: t('次日胜率'), align: 'right', render: (r) => rate(r.win_1d) },
    { key: 'a3', title: t('3日均收益'), align: 'right', render: (r) => <Pct value={r.avg_3d} /> },
    { key: 'a5', title: t('5日均收益'), align: 'right', render: (r) => <Pct value={r.avg_5d} /> },
    { key: 'lu', title: t('次日涨停率'), align: 'right', render: (r) => rate(r.limit_up_rate) },
    { key: 'fit', title: t('适配时次日均收益'), align: 'right', render: (r) => <Pct value={r.avg_1d_fit} /> },
    { key: 'total', title: t('累计收益'), align: 'right', render: (r) => <Pct value={r.total_return} /> },
    { key: 'mdd', title: t('最大回撤'), align: 'right', render: (r) => <Pct value={r.max_drawdown} /> },
    { key: 'weight', title: t('排序权重'), align: 'right', render: (r) => <span className="num">{fmtNum(r.weight)}</span> },
  ]
  const bt = data?.backtest

  return (
    <div>
      <PageHeader
        title={t('策略选股')}
        description={t('放量突破、强势未板、龙回头、主线补涨、缩量回踩、超跌反弹 6 个策略扫描全市场；与大盘环境适配的排在前面')}
        actions={
          <>
            <Button loading={backtest.running} onClick={() => backtest.run(() => api.backtest(60), { success: (r) => (r.note ? r.note : t('回测完成：{n} 个交易日', { n: r.dates })) }).then(reload).catch(() => {})}>
              {backtest.running ? `${t('回测中')} ${progressText(backtest.progress)}` : t('历史回测（60 天）')}
            </Button>
            <Button variant="primary" loading={screen.running} onClick={() => screen.run(api.runScreening, { success: (r) => t('选出 {n} 只', { n: r.picks.length }) + (r.notes.length ? '（' + r.notes[0] + '）' : '') }).then(refreshAll).catch(() => {})}>
              {t('重新选股')}
            </Button>
          </>
        }
      />
      {error && <ErrorBox message={error} onRetry={reload} />}
      {loading && !data ? <Spinner /> : (
        <div className="space-y-4">
          <Card
            title={t('选股结果')}
            actions={
              <div className="flex gap-2">
                <Select aria-label={t('选股日期')} value={selDate} onChange={(e) => setSelDate(e.target.value)}>
                  <option value="">{t('最近一天')}</option>
                  {dates.map((d) => <option key={d.trade_date} value={d.trade_date}>{dateLabel(d)}</option>)}
                </Select>
                <Select aria-label={t('策略筛选')} value={strategy} onChange={(e) => setStrategy(e.target.value)}>
                  <option value="">{t('全部策略')}</option>
                  {strategyOptions.map((s) => <option key={s.name} value={s.name}>{t(s.label)}</option>)}
                </Select>
              </div>
            }
            bodyClassName="p-0"
          >
            {histError && <ErrorBox message={histError} />}
            <DataTable columns={pickColumns} rows={shownPicks} rowKey={(p) => p.code} onRowClick={(p) => navigate(`/stocks/${p.code}`)} empty={t('暂无选股结果，点「重新选股」扫描全市场')} />
          </Card>
          <Card title={t('历史概览（最近 20 个交易日）')} bodyClassName="p-0">
            <DataTable columns={histColumns} rows={dates.slice(0, 20)} rowKey={(d) => d.trade_date} onRowClick={(d) => setSelDate(d.trade_date)} empty={t('暂无历史选股')} />
          </Card>
          <Card title={t('策略次日表现（近 30 天实际选股）')} bodyClassName="p-0">
            <DataTable columns={perfColumns} rows={data?.performance ?? []} rowKey={(r) => r.strategy} />
          </Card>
          <Card
            title={bt ? `${t('历史回测')} ${bt.start} ~ ${bt.end}（${t('{n} 个交易日', { n: bt.dates })}${bt.skipped_dates ? `，${t('{n} 天行情不全已跳过', { n: bt.skipped_dates })}` : ''}）` : t('历史回测')}
            actions={bt && <span className="text-xs text-muted">{t('{time} 生成', { time: bt.created_at })} · {t('次日开盘入场')}</span>}
            bodyClassName="p-0"
          >
            <DataTable columns={btColumns} rows={bt?.strategies ?? []} rowKey={(r) => r.strategy} empty={bt?.note ?? t('尚未回测')} />
          </Card>
        </div>
      )}
    </div>
  )
}
