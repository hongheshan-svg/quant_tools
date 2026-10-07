// 对齐上游首页：自选、今日/近期报告和任务与当前报告并排，支持窄屏堆叠。
import { ArrowUpRight, FileText, RefreshCw, Search, Star } from 'lucide-react'
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '@/api/endpoints'
import type { Diagnosis, DiagnosisHistoryItem } from '@/api/types'
import { DiagnosisView } from '@/components/DiagnosisView'
import { RunLogView } from '@/components/RunLogView'
import { ShareImageButton } from '@/components/ShareImageButton'
import { StockSearch } from '@/components/StockSearch'
import { Badge, Button, Card, ErrorBox, PageHeader, Pct, Spinner, Tabs } from '@/components/ui'
import { useApi } from '@/hooks/useApi'
import { progressText, useTask } from '@/hooks/useTask'
import { useT } from '@/i18n'
import { useTaskStore } from '@/stores/tasks'
import { toast } from '@/stores/toast'
import { cn } from '@/utils/cn'
import { fmtNum, verdictClass } from '@/utils/format'
import { ResearchOverview } from './StockPage'
import { SetupBanner } from './DashboardPage'

type Selection = { code: string; name: string; recordId?: number }
type ListTab = 'watchlist' | 'today' | 'history'

function ReportPanel({ selected, onFinished }: { selected: Selection; onFinished: () => void }) {
  const t = useT()
  const report = useApi<Diagnosis | null>(() => selected.recordId ? api.diagnosisRecord(selected.recordId).then((r) => ({ ...r.result, diagnosis_id: r.id, run_log: r.run_log })) : api.latestDiagnosis(selected.code))
  const run = useTask<Diagnosis>()
  const [tab, setTab] = useState<'report' | 'research'>('report')
  const d = report.data
  const id = d?.diagnosis_id ?? d?.id
  return <Card title={<span>{selected.name || selected.code} <span className="num ml-2 text-xs text-muted">{selected.code}</span></span>} actions={<>
    <Link className="inline-flex items-center gap-1 text-xs text-accent" to={`/stocks/${selected.code}`}>{t('行情与详情')}<ArrowUpRight className="size-3" /></Link>
    <Button variant="primary" loading={run.running} onClick={() => void run.run(() => api.diagnose(selected.code)).then((result) => { if (!result.error) { report.setData(result); onFinished() } }).catch(() => {})}>
      {run.running ? t('分析中 {p}', { p: progressText(run.progress) }) : t('重新分析')}
    </Button>
  </>}>
    <Tabs tabs={[{ key: 'report', label: t('诊断报告') }, { key: 'research', label: t('研究概览') }]} value={tab} onChange={setTab} />
    {tab === 'research' ? report.loading ? <Spinner /> : report.error ? <ErrorBox message={report.error} onRetry={report.reload} /> : <ResearchOverview code={selected.code} reportArtifact={d?.structured_report ?? null} reportId={id} /> : <>
      {report.error && <ErrorBox message={report.error} onRetry={report.reload} />}
      {report.loading ? <Spinner /> : d ? <>
        <div className="mb-4 flex flex-wrap gap-2">
          {id != null && <><a className="text-xs text-accent" href={api.diagnosisMarkdownUrl(id)} download>{t('导出 Markdown')}</a><ShareImageButton url={api.diagnosisImageUrl(id)} filename={`${selected.code}.png`} /></>}
          <Link className="text-xs text-accent" to={`/chat?code=${selected.code}`}>{t('继续问股')}</Link>
        </div>
        <DiagnosisView d={d} diagnosisId={id} />
        <details className="mt-4 rounded-lg border border-line p-3"><summary className="cursor-pointer text-xs text-muted">{t('分析过程与耗时')}</summary><div className="mt-3"><RunLogView log={d.run_log} /></div></details>
      </> : !report.error && <div className="py-16 text-center text-muted"><FileText className="mx-auto mb-3 size-8" /><p>{t('该标的还没有诊断报告')}</p><p className="mt-2 text-xs">{t('点击重新分析，生成第一份报告')}</p></div>}
    </>}
  </Card>
}

export function WorkspacePage() {
  const t = useT()
  const workspace = useApi(api.stockWorkspace)
  const [selection, setSelection] = useState<Selection | null>(null)
  const [listTab, setListTab] = useState<ListTab>('watchlist')
  const [checked, setChecked] = useState<string[]>([])
  const [query, setQuery] = useState('')
  const [revision, setRevision] = useState(0)
  const batch = useTask()
  const data = workspace.data
  const rows = data?.watchlist ?? []
  const firstReport = data?.recent_reports?.[0]
  const selected = selection ?? (rows.length ? { code: rows[0].code, name: rows[0].name } : firstReport ? { code: firstReport.code, name: firstReport.name, recordId: firstReport.id } : null)
  const tasks = Object.values(useTaskStore((s) => s.tasks)).sort((a, b) => b.created_at.localeCompare(a.created_at)).slice(0, 5)
  const openReport = (r: DiagnosisHistoryItem) => setSelection({ code: r.code, name: r.name, recordId: r.id })
  const filtered = rows.filter((r) => `${r.code} ${r.name}`.toLowerCase().includes(query.toLowerCase()))
  const reports = (listTab === 'today' ? data?.today_reports : data?.recent_reports) ?? []
  const analyze = () => {
    const codes = checked.filter((code) => rows.some((r) => r.code === code))
    void batch.run(() => codes.length ? api.runSelectedWatchlist(codes) : api.runWatchlistReport(false), { success: t('分析完成') }).then(() => { setChecked([]); setRevision((v) => v + 1); void workspace.reload() }).catch(() => {})
  }
  const add = async (stock: { code: string; name: string }) => {
    try {
      const result = await api.addWatch(stock.code)
      if (!result.ok) { toast.error(result.error || t('添加失败')); return }
      setSelection(stock)
      await workspace.reload()
    } catch (error) { toast.error(error instanceof Error ? error.message : String(error)) }
  }
  return <div>
    <SetupBanner />
    <PageHeader title={t('研究工作台')} description={t('选择标的、生成报告、核对证据，在同一处完成研究')} actions={<>
      <Link className="text-sm text-muted hover:text-accent" to="/market">{t('交易决策')}<ArrowUpRight className="ml-1 inline size-3" /></Link>
      <Button aria-label={t('刷新工作台')} loading={workspace.loading} onClick={() => { setRevision((v) => v + 1); void workspace.reload() }}><RefreshCw className="size-3.5" />{t('刷新')}</Button>
    </>} />
    <Card className="mb-4" bodyClassName="flex flex-wrap items-center gap-3">
      <StockSearch className="min-w-48 flex-1" placeholder={t('搜索股票 / ETF / 指数，加入研究列表')} onSelect={(s) => void add(s)} />
      <span className="text-xs text-muted">{t('今日已分析 {n}/{total}', { n: data?.analyzed_today ?? 0, total: rows.length })}</span>
      <Button variant="primary" disabled={!rows.length} loading={batch.running} onClick={analyze}>{batch.running ? progressText(batch.progress) || t('分析中…') : checked.length ? t('分析选中（{n}）', { n: checked.length }) : t('分析全部')}</Button>
    </Card>
    {workspace.error && <ErrorBox message={workspace.error} onRetry={workspace.reload} />}
    {workspace.loading && !data ? <Spinner /> : <div className="grid items-start gap-4 xl:grid-cols-[340px_minmax(0,1fr)]">
      <div className="space-y-4">
        <Card bodyClassName="p-3">
          <Tabs<ListTab> tabs={[{ key: 'watchlist', label: t('自选股') }, { key: 'today', label: t('今日报告') }, { key: 'history', label: t('近期报告') }]} value={listTab} onChange={setListTab} />
          {listTab === 'watchlist' ? <>
            <div className="mb-3 flex items-center gap-2"><Search className="size-3.5 text-muted" /><input className="min-w-0 flex-1 bg-transparent text-xs outline-none" aria-label={t('筛选自选股')} placeholder={t('筛选名称或代码')} value={query} onChange={(e) => setQuery(e.target.value)} /></div>
            <div className="max-h-[65vh] space-y-2 overflow-y-auto">
              {filtered.map((r) => <div key={r.code} className={cn('flex gap-2 rounded-lg border p-3', selected?.code === r.code ? 'border-accent/50 bg-accent/5' : 'border-line bg-panel-2')}>
                <input className="mt-1 shrink-0 self-start" type="checkbox" aria-label={t('选择股票 {code}', { code: r.code })} checked={checked.includes(r.code)} onChange={(e) => setChecked((codes) => e.target.checked ? [...codes, r.code] : codes.filter((code) => code !== r.code))} />
                <button type="button" className="min-w-0 flex-1 text-left" aria-pressed={selected?.code === r.code} onClick={() => setSelection({ code: r.code, name: r.name })}>
                  <div className="flex items-center justify-between gap-2"><span className="truncate text-sm font-semibold">{r.name || r.code}</span><span className={cn('text-xs', verdictClass(r.diagnosis?.action_label))}>{t(r.diagnosis?.action_label || '未诊断')} {r.diagnosis?.score ?? ''}</span></div>
                  <div className="mt-1 flex justify-between text-xs"><span className="num text-muted">{r.code}</span><span className="num">{fmtNum(r.close)} <Pct value={r.change_pct} /></span></div>
                  <p className="mt-2 line-clamp-2 text-xs text-muted">{r.diagnosis?.one_sentence || t('等待首次分析')}</p>
                  <p className="mt-2 text-[10px] text-muted">{r.trade_date || t('行情未就绪')} · {r.quote_source || t('来源未记录')}</p>
                  <p className="mt-1 text-xs text-accent" title={r.state?.reason}>{t(r.state?.label ?? '状态未知')} · {t(r.state?.next_action === 'wait' ? '等待完成' : r.state?.next_action === 'view_report' ? '查看报告' : r.state?.next_action === 'retry_query' ? '重试查询' : '更新分析')}</p>
                </button>
              </div>)}
              {!filtered.length && <div className="py-8 text-center text-xs text-muted"><Star className="mx-auto mb-3 size-6" />{t('暂无匹配标的，可用上方搜索添加')}</div>}
            </div>
            <Link className="mt-3 block text-center text-xs text-accent" to="/watchlist">{t('导入与管理自选股')}</Link>
          </> : <div className="max-h-[65vh] space-y-2 overflow-y-auto">{reports.map((r) => <button key={r.id} type="button" className={cn('w-full rounded-lg border p-3 text-left', selected?.recordId === r.id ? 'border-accent/50 bg-accent/5' : 'border-line hover:bg-panel-2')} onClick={() => openReport(r)}><div className="flex justify-between text-sm"><span>{r.name || r.code}</span><span className="num">{r.score ?? '--'}</span></div><p className="mt-1 text-[10px] text-muted">{r.code} · {r.created_at}</p><p className="mt-2 line-clamp-2 text-xs text-muted">{r.summary}</p></button>)}{!reports.length && <p className="py-8 text-center text-xs text-muted">{t('暂无报告')}</p>}</div>}
        </Card>
        <Card title={t('分析任务')} bodyClassName="p-3">{tasks.length ? tasks.map((task) => <div key={task.id} className="border-b border-line py-2 text-xs last:border-0"><div className="flex justify-between gap-2"><span className="truncate">{task.label}</span><Badge tone={task.status === 'error' ? 'warn' : task.status === 'done' ? 'down' : 'accent'}>{t({ pending: '排队中', running: '进行中', done: '完成', error: '失败' }[task.status])}</Badge></div><p className="mt-1 break-words text-muted">{task.error || progressText(task.progress)}</p></div>) : <p className="text-xs text-muted">{t('还没有后台任务')}</p>}</Card>
      </div>
      {selected ? <ReportPanel key={`${selected.code}:${selected.recordId ?? 'latest'}:${revision}`} selected={selected} onFinished={() => void workspace.reload()} /> : <Card bodyClassName="py-24 text-center"><FileText className="mx-auto mb-4 size-10 text-accent" /><h2 className="font-medium">{t('从一只股票开始研究')}</h2><p className="mt-2 text-sm text-muted">{t('搜索并添加自选股，或从近期报告继续研究')}</p></Card>}
    </div>}
  </div>
}
