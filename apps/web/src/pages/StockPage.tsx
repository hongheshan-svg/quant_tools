// 个股详情：K 线（日/周/月）、日线数据、新闻公告、AI 诊断、加入自选
import { Star } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api } from '@/api/endpoints'
import type { DailyBar, Diagnosis, StockNews } from '@/api/types'
import { CandlestickChart, toCandles, type Period } from '@/components/CandlestickChart'
import { DataTable, type Column } from '@/components/DataTable'
import { DiagnosisView } from '@/components/DiagnosisView'
import { Badge, Button, Card, ErrorBox, Pct, Spinner, Tabs } from '@/components/ui'
import { useApi } from '@/hooks/useApi'
import { progressText, useTask } from '@/hooks/useTask'
import { toast } from '@/stores/toast'
import { fmtAmount, fmtNum } from '@/utils/format'

type TabKey = 'kline' | 'daily' | 'news' | 'diagnosis'
const MIN_BARS = 60

export function StockPage() {
  const { code = '' } = useParams()
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
      toast.info('已移出自选股')
    } else {
      const r = await api.addWatch(code)
      if (r.ok) {
        setWatched(true)
        toast.success('已加入自选股')
      } else toast.error(r.error ?? '加入失败')
    }
  }

  const dailyColumns: Column<DailyBar>[] = [
    { key: 'date', title: '日期', render: (b) => <span className="num">{b.trade_date}</span> },
    ...(['open', 'high', 'low', 'close'] as const).map((k, i) => ({
      key: k, title: ['开盘', '最高', '最低', '收盘'][i], align: 'right' as const, render: (b: DailyBar) => <span className="num">{fmtNum(b[k])}</span>,
    })),
    { key: 'pct', title: '涨跌幅', align: 'right', render: (b) => <Pct value={b.change_pct} /> },
    { key: 'amount', title: '成交额', align: 'right', render: (b) => <span className="num">{fmtAmount(b.amount)}</span> },
    { key: 'turnover', title: '换手率', align: 'right', render: (b) => <span className="num">{b.turnover ? `${fmtNum(b.turnover)}%` : '--'}</span> },
  ]

  return (
    <div>
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <h1 className="text-lg font-semibold">{name || code}</h1>
        <span className="num text-muted">{code}</span>
        {latest && (
          <>
            <span className="num text-lg">{fmtNum(latest.close)}</span>
            <Pct value={latest.change_pct} />
            <span className="text-xs text-muted">{latest.trade_date}</span>
          </>
        )}
        {backfilling && <Badge tone="accent">本地日线不足，正在联网补齐…</Badge>}
        {watched != null && (
          <Button className="ml-auto" onClick={toggleWatch}>
            <Star className={watched ? 'size-4 fill-warn text-warn' : 'size-4'} /> {watched ? '移出自选' : '加入自选'}
          </Button>
        )}
      </div>
      {daily.error && <ErrorBox message={daily.error} onRetry={daily.reload} />}
      <Card bodyClassName="p-3">
        <Tabs<TabKey>
          value={tab}
          onChange={setTab}
          tabs={[{ key: 'kline', label: 'K 线' }, { key: 'daily', label: '日线数据' }, { key: 'news', label: '新闻公告' }, { key: 'diagnosis', label: 'AI 诊断' }]}
        />
        {tab === 'kline' && (
          <>
            <div className="mb-2 flex gap-1">
              {(['day', 'week', 'month'] as Period[]).map((p) => (
                <Button key={p} variant={period === p ? 'primary' : 'default'} onClick={() => setPeriod(p)}>{{ day: '日K', week: '周K', month: '月K' }[p]}</Button>
              ))}
            </div>
            {daily.loading && !daily.data ? <Spinner /> : <CandlestickChart candles={toCandles(bars, period)} />}
          </>
        )}
        {tab === 'daily' && <DataTable columns={dailyColumns} rows={[...bars].reverse()} rowKey={(b) => b.trade_date} maxHeight="60vh" />}
        {tab === 'news' && <NewsTab code={code} />}
        {tab === 'diagnosis' && <DiagnosisTab code={code} />}
      </Card>
    </div>
  )
}

function NewsTab({ code }: { code: string }) {
  const [refresh, setRefresh] = useState(0)
  const { data, error, loading } = useApi<StockNews>(() => api.stockNews(code, refresh > 0), [code, refresh])
  if (loading && !data) return <Spinner text="获取新闻与公告…" />
  if (error) return <ErrorBox message={error} />
  return (
    <div className="space-y-4">
      <div className="flex justify-end"><Button onClick={() => setRefresh((n) => n + 1)}>刷新</Button></div>
      <section>
        <h3 className="mb-2 font-medium text-accent">公告（近 30 天）</h3>
        <ul className="space-y-1.5 text-sm">
          {(data?.notices ?? []).map((n) => (
            <li key={n.url || n.title}>
              <span className="num mr-2 text-xs text-muted">{n.date}</span>
              {n.source && <Badge className="mr-1">{n.source}</Badge>}
              <a href={n.url} target="_blank" rel="noreferrer" className="hover:underline">{n.title}</a>
              {n.risk && <Badge tone={n.severe ? 'up' : 'warn'} className="ml-2">{n.severe ? '严重风险' : '风险'}：{n.risk}</Badge>}
            </li>
          ))}
          {!data?.notices.length && <li className="text-muted">暂无</li>}
        </ul>
      </section>
      <section>
        <h3 className="mb-2 font-medium text-accent">个股新闻（近 7 天）</h3>
        <ul className="space-y-1.5 text-sm">
          {(data?.news ?? []).map((n) => (
            <li key={n.url || n.title}>
              <span className="num mr-2 text-xs text-muted">{n.date}</span>
              <span className="mr-1 text-xs text-muted">[{n.source || '东方财富'}]</span>
              <a href={n.url} target="_blank" rel="noreferrer" className="hover:underline">{n.title}</a>
            </li>
          ))}
          {!data?.news.length && <li className="text-muted">暂无</li>}
        </ul>
      </section>
    </div>
  )
}

function DiagnosisTab({ code }: { code: string }) {
  const { data, loading, setData } = useApi<Diagnosis | null>(() => api.latestDiagnosis(code), [code])
  const task = useTask<Diagnosis>()
  const run = () => task.run(() => api.diagnose(code)).then(setData).catch(() => {})
  return (
    <div>
      <div className="mb-3 flex items-center justify-between gap-2">
        <p className="text-xs text-muted">结合技术面、资金、筹码、业绩、新闻公告、主线和大盘，由技术面/情报分析员与决策员给出结论（约 20~60 秒）</p>
        <Link to={`/history?code=${code}`} className="ml-auto text-xs text-accent hover:underline">历史诊断</Link>
        <Button variant="primary" loading={task.running} onClick={run}>{task.running ? `诊断中 ${progressText(task.progress)}` : data ? '重新诊断' : '开始诊断'}</Button>
      </div>
      {loading && !data ? <Spinner /> : data ? <DiagnosisView d={data} /> : <p className="text-sm text-muted">还没有诊断记录</p>}
    </div>
  )
}
