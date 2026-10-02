// 个股详情：K 线（日/周/月）、日线数据、新闻公告、AI 诊断、加入自选；ETF/指数只有 K 线、日线和 AI 诊断
import { Star } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api } from '@/api/endpoints'
import type { DailyBar, Diagnosis, SignalReview, StockNews, StockProfile } from '@/api/types'
import { CandlestickChart, toCandles, type Period } from '@/components/CandlestickChart'
import { DataTable, type Column } from '@/components/DataTable'
import { DiagnosisView } from '@/components/DiagnosisView'
import { ScoreTrendChart } from '@/components/ScoreTrendChart'
import { Badge, Button, Card, ErrorBox, Pct, Spinner, Tabs } from '@/components/ui'
import { useApi } from '@/hooks/useApi'
import { progressText, useTask } from '@/hooks/useTask'
import { useT } from '@/i18n'
import { toast } from '@/stores/toast'
import { fmtAmount, fmtNum } from '@/utils/format'
import { FUND_LABELS, fundKind } from '@/utils/fund'

type TabKey = 'kline' | 'daily' | 'news' | 'diagnosis' | 'research'
const MIN_BARS = 60

export function StockPage() {
  const t = useT()
  const { code = '' } = useParams()
  const fund = fundKind(code)
  const [tab, setTab] = useState<TabKey>('kline')
  const [period, setPeriod] = useState<Period>('day')
  const daily = useApi<DailyBar[]>(() => api.daily(code), [code])
  const bars = useMemo(() => daily.data ?? [], [daily.data])
  const latest = bars[bars.length - 1]
  const name = [...bars].reverse().find((b) => b.name)?.name ?? ''
  const [backfilling, setBackfilling] = useState(false)
  const [watched, setWatched] = useState<boolean | null>(null)

  // 本地日线不足时联网补齐
  useEffect(() => {
    if (daily.loading || !daily.data || daily.data.length >= MIN_BARS) return
    let cancelled = false
    setBackfilling(true)
    api
      .ensureHistory(code, name)
      .then((r) => {
        if (!cancelled && r.added > 0) void daily.reload()
      })
      .catch(() => {})
      .finally(() => !cancelled && setBackfilling(false))
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [code, daily.loading])

  useEffect(() => {
    api.watchlist().then((rows) => setWatched(rows.some((r) => r.code === code))).catch(() => setWatched(null))
  }, [code])

  const toggleWatch = async () => {
    if (watched) {
      await api.removeWatch(code)
      setWatched(false)
      toast.info(t('已移出自选股'))
    } else {
      const r = await api.addWatch(code)
      if (r.ok) {
        setWatched(true)
        toast.success(t('已加入自选股'))
      } else toast.error(r.error ?? t('加入失败'))
    }
  }

  const dailyColumns: Column<DailyBar>[] = [
    { key: 'date', title: t('日期'), render: (b) => <span className="num">{b.trade_date}</span> },
    ...(['open', 'high', 'low', 'close'] as const).map((k, i) => ({
      key: k, title: t(['开盘', '最高', '最低', '收盘'][i]), align: 'right' as const, render: (b: DailyBar) => <span className="num">{fmtNum(b[k])}</span>,
    })),
    { key: 'pct', title: t('涨跌幅'), align: 'right', render: (b) => <Pct value={b.change_pct} /> },
    { key: 'amount', title: t('成交额'), align: 'right', render: (b) => <span className="num">{fmtAmount(b.amount)}</span> },
    { key: 'turnover', title: t('换手率'), align: 'right', render: (b) => <span className="num">{b.turnover ? `${fmtNum(b.turnover)}%` : '--'}</span> },
  ]

  return (
    <div>
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <h1 className="text-lg font-semibold">{name || code}</h1>
        <span className="num text-muted">{code}</span>
        {fund && <Badge tone="accent">{t(FUND_LABELS[fund])}</Badge>}
        {latest && (
          <>
            <span className="num text-lg">{fmtNum(latest.close)}</span>
            <Pct value={latest.change_pct} />
            <span className="text-xs text-muted">{latest.trade_date}</span>
            <span className="text-xs text-muted">{t('来源')}：{latest.source || t('来源未记录')} · {t(({ none: '不复权', forward: '前复权', backward: '后复权', forward_additive: '前复权（加法）', backward_additive: '后复权（加法）' } as Record<string, string>)[latest.price_adjustment || ''] || '复权口径未知')}</span>
          </>
        )}
        {backfilling && <Badge tone="accent">{t('本地日线不足，正在联网补齐…')}</Badge>}
        {!fund && <Button loading={backfilling} onClick={async () => {
          setBackfilling(true)
          try { const r = await api.ensureHistory(code, name, true); toast.success(t('已更新 {n} 根日线', { n: r.added })); await daily.reload() }
          catch (e) { toast.error(e instanceof Error ? e.message : String(e)) }
          finally { setBackfilling(false) }
        }}>{t('刷新复权历史')}</Button>}
        {watched != null && (
          <Button className="ml-auto" onClick={toggleWatch}>
            <Star className={watched ? 'size-4 fill-warn text-warn' : 'size-4'} /> {watched ? t('移出自选') : t('加入自选')}
          </Button>
        )}
      </div>
      {daily.error && <ErrorBox message={daily.error} onRetry={daily.reload} />}
      <Card bodyClassName="p-3">
        <Tabs<TabKey>
          value={tab}
          onChange={setTab}
          tabs={[
            { key: 'kline', label: t('K 线') },
            { key: 'daily', label: t('日线数据') },
            ...(fund ? [] : [{ key: 'news' as TabKey, label: t('新闻公告') }]),
            { key: 'diagnosis', label: t('AI 诊断') },
            { key: 'research', label: t('研究概览') },
          ]}
        />
        {tab === 'kline' && (
          <>
            <div className="mb-2 flex gap-1">
              {(['day', 'week', 'month'] as Period[]).map((p) => (
                <Button key={p} variant={period === p ? 'primary' : 'default'} onClick={() => setPeriod(p)}>{t({ day: '日K', week: '周K', month: '月K' }[p])}</Button>
              ))}
            </div>
            {daily.loading && !daily.data ? <Spinner /> : <CandlestickChart candles={toCandles(bars, period)} />}
          </>
        )}
        {tab === 'daily' && <DataTable columns={dailyColumns} rows={[...bars].reverse()} rowKey={(b) => b.trade_date} maxHeight="60vh" />}
        {tab === 'news' && <NewsTab code={code} />}
        {tab === 'diagnosis' && <DiagnosisTab code={code} isFund={!!fund} />}
        {tab === 'research' && <ResearchOverview code={code} />}
      </Card>
    </div>
  )
}

export function ResearchOverview({ code, reportArtifact, reportId }: { code: string; reportArtifact?: import('@/api/types').ResearchArtifact | null; reportId?: number }) {
  const t = useT()
  const { data, loading, error, reload } = useApi<StockProfile>(() => api.stockProfile(code), [code])
  if (error) return <ErrorBox message={error} onRetry={reload} />
  if (loading || !data) return <Spinner />
  const artifact = reportArtifact !== undefined ? reportArtifact : data.research.data?.structured_report
  return <div className="space-y-4">
    <div className="flex items-center gap-2"><h3 className="font-medium">{t('研究概览')}</h3><Button onClick={reload}>{t('刷新')}</Button></div>
    {artifact ? <>
      <p className="text-xs text-muted">{artifact.created_at} {reportId != null && `· #${reportId}`}</p>
      <p className="font-medium">{artifact.thesis.summary}</p>
      <div className="grid gap-3 md:grid-cols-2">
        <Card title={t('失效条件')}><ul className="space-y-1">{artifact.invalidation_conditions.map((c) => <li key={c.id}>{c.description}</li>)}</ul></Card>
        <Card title={t('下一步')}><ul className="space-y-1">{artifact.next_actions.map((a) => <li key={a.action}>{a.label}：{a.reason} {a.due_at}</li>)}</ul></Card>
      </div>
      <details><summary className="cursor-pointer text-accent">{t('证据与来源')}（{artifact.evidence.length}）</summary>
        <ul className="mt-2 space-y-3">{artifact.evidence.map((e) => <li key={e.id} className="rounded-lg border border-border p-3"><p className="mb-1 font-medium">{e.title}</p><Badge>{e.source || e.source_type}</Badge> <span className="text-xs text-muted">{e.as_of || t('时间未知')} · {t(e.freshness === 'stale' ? '已陈旧' : e.freshness === 'fresh' ? '可用' : '时间未确认')}</span><p className="mt-1 whitespace-pre-wrap text-sm">{e.summary}</p></li>)}</ul>
      </details>
    </> : <p className="text-muted">{t('暂无诊断，先生成 AI 诊断')}</p>}
    <Card title={t('持仓与盯盘')}>
      {data.portfolio.status === 'unavailable' ? <p className="text-muted">{t('持仓数据不可用')}</p> : <p>{data.portfolio.data?.held ? data.portfolio.data.holdings.map((h) => `${h.label} ${h.quantity} 股，成本 ${h.avg_cost}`).join('；') : t('未持仓')}</p>}
      {data.monitors.data?.alert_rules.map((r, i) => <p key={i}>{r.text} {r.note}</p>)}
      {data.signals.data?.active.map((s) => <p key={s.id}><Link className="text-accent" to="/signals">{s.action_label}</Link> · {t('止损')} {s.stop_loss ?? '--'} · {t('目标')} {s.target_price ?? '--'}</p>)}
    </Card>
    <Card title={t('近期报告')}><ul className="space-y-1">{data.history.data?.recent_reports.map((r) => <li key={r.id}><Link className="text-accent" to={`/history?code=${encodeURIComponent(code)}&id=${r.id}`}>{r.created_at} · {r.summary}</Link></li>)}</ul></Card>
    <Card title={t('相关资讯')}><ul className="space-y-1">{data.intelligence.data?.items.map((i) => <li key={i.id}><a href={i.url || undefined} target="_blank" rel="noreferrer">{i.title}</a><span className="ml-2 text-xs text-muted">{i.source}</span></li>)}</ul></Card>
  </div>
}

function NewsTab({ code }: { code: string }) {
  const t = useT()
  const [refresh, setRefresh] = useState(0)
  const { data, error, loading } = useApi<StockNews>(() => api.stockNews(code, refresh > 0), [code, refresh])
  if (loading && !data) return <Spinner text={t('获取新闻与公告…')} />
  if (error) return <ErrorBox message={error} />
  return (
    <div className="space-y-4">
      <div className="flex justify-end"><Button onClick={() => setRefresh((n) => n + 1)}>{t('刷新')}</Button></div>
      <section>
        <h3 className="mb-2 font-medium text-accent">{t('公告（近 30 天）')}</h3>
        <ul className="space-y-1.5 text-sm">
          {(data?.notices ?? []).map((n) => (
            <li key={n.url || n.title}>
              <span className="num mr-2 text-xs text-muted">{n.date}</span>
              {n.source && <Badge className="mr-1">{n.source}</Badge>}
              <a href={n.url} target="_blank" rel="noreferrer" className="hover:underline">{n.title}</a>
              {n.risk && <Badge tone={n.severe ? 'up' : 'warn'} className="ml-2">{n.severe ? t('严重风险') : t('风险')}{t('：')}{n.risk}</Badge>}
            </li>
          ))}
          {!data?.notices.length && <li className="text-muted">{t('暂无')}</li>}
        </ul>
      </section>
      <section>
        <h3 className="mb-2 font-medium text-accent">{t('个股新闻（近 7 天）')}</h3>
        <ul className="space-y-1.5 text-sm">
          {(data?.news ?? []).map((n) => (
            <li key={n.url || n.title}>
              <span className="num mr-2 text-xs text-muted">{n.date}</span>
              <span className="mr-1 text-xs text-muted">[{t(n.source || '东方财富')}]</span>
              <a href={n.url} target="_blank" rel="noreferrer" className="hover:underline">{n.title}</a>
            </li>
          ))}
          {!data?.news.length && <li className="text-muted">{t('暂无')}</li>}
        </ul>
      </section>
    </div>
  )
}

function DiagnosisTab({ code, isFund }: { code: string; isFund: boolean }) {
  const t = useT()
  const { data, loading, setData } = useApi<Diagnosis | null>(() => api.latestDiagnosis(code), [code])
  const task = useTask<Diagnosis>()
  const run = () => task.run(() => api.diagnose(code)).then(setData).catch(() => {})
  const { data: review } = useApi<SignalReview>(() => api.signalReview(code), [code, data?.created_at])
  return (
    <div>
      <div className="mb-3 flex items-center justify-between gap-2">
        <p className="text-xs text-muted">{isFund ? t('结合技术面、大盘环境、对应主线和相关资讯给出结论（ETF / 指数没有资金流、筹码、业绩和公告数据，约 20~60 秒）') : t('结合技术面、资金、筹码、业绩、新闻公告、主线和大盘，由技术面/情报分析员与决策员给出结论（约 20~60 秒）')}</p>
        <Link to={`/history?code=${code}`} className="ml-auto text-xs text-accent hover:underline">{t('历史诊断')}</Link>
        <Button variant="primary" loading={task.running} onClick={run}>{task.running ? t('诊断中 {p}', { p: progressText(task.progress) }) : data ? t('重新诊断') : t('开始诊断')}</Button>
      </div>
      {review && (
        <p className={review.samples >= 3 ? 'mb-3 text-sm' : 'mb-3 text-xs text-muted'}>
          <span className="text-muted">{t('历史信号复盘：')}</span>{review.text}
          {review.samples >= 3 && review.bias && review.bias !== '正常' && <Badge tone="warn" className="ml-1">{t(review.bias)}</Badge>}
          <Link to={`/signals?code=${code}`} className="ml-2 text-xs text-accent hover:underline">{t('决策信号')}</Link>
        </p>
      )}
      <div className="mb-3"><ScoreTrendChart code={code} key={data?.created_at} /></div>
      {loading && !data ? <Spinner /> : data ? <DiagnosisView d={data} /> : <p className="text-sm text-muted">{t('还没有诊断记录')}</p>}
    </div>
  )
}
