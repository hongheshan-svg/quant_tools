// 个股详情：K 线（日/周/月）、日线数据、新闻公告、AI 诊断、加入自选；ETF/指数只有 K 线、日线和 AI 诊断
import { Star } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api } from '@/api/endpoints'
import type { DailyBar, Diagnosis, SignalReview, StockNews } from '@/api/types'
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

type TabKey = 'kline' | 'daily' | 'news' | 'diagnosis'
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
          </>
        )}
        {backfilling && <Badge tone="accent">{t('本地日线不足，正在联网补齐…')}</Badge>}
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
      </Card>
    </div>
  )
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
