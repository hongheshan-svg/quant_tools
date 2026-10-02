// 诊断历史：按股票、操作建议、时间筛选历史 AI 诊断，查看详情、导出 Markdown / 分享图、删除
import { X } from 'lucide-react'
import { useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { api } from '@/api/endpoints'
import type { DiagnosisHistoryItem, DiagnosisRecord } from '@/api/types'
import { DataTable, type Column } from '@/components/DataTable'
import { DiagnosisView } from '@/components/DiagnosisView'
import { RunLogView } from '@/components/RunLogView'
import { ScoreTrendChart } from '@/components/ScoreTrendChart'
import { ShareImageButton } from '@/components/ShareImageButton'
import { StockSearch } from '@/components/StockSearch'
import { Badge, Button, Card, ErrorBox, Modal, PageHeader, Select, Spinner } from '@/components/ui'
import { useApi } from '@/hooks/useApi'
import { useT } from '@/i18n'
import { toast } from '@/stores/toast'
import { verdictClass } from '@/utils/format'
import { cn } from '@/utils/cn'

const PAGE_SIZE = 50
const ACTIONS: Record<string, string> = { buy: '买入', add: '加仓', hold: '持有', watch: '观望', reduce: '减仓', sell: '卖出', avoid: '回避' }
const DAYS = [{ v: 7, t: '近 7 天' }, { v: 30, t: '近 30 天' }, { v: 90, t: '近 90 天' }, { v: 0, t: '不限' }]

export function HistoryPage() {
  const t = useT()
  const [params, setParams] = useSearchParams()
  const code = params.get('code') ?? ''
  const [action, setAction] = useState('')
  const [days, setDays] = useState(30)
  const [limit, setLimit] = useState(PAGE_SIZE)
  const linkedId = Number(params.get('id'))
  const [openId, setOpenId] = useState<number | null>(Number.isSafeInteger(linkedId) && linkedId > 0 ? linkedId : null)
  const [selected, setSelected] = useState<number[]>([])
  const [deleting, setDeleting] = useState(false)
  const { data, error, loading, reload } = useApi(
    () => api.diagnosisHistoryList({ code: code || undefined, action: action || undefined, days, limit, offset: 0 }),
    [code, action, days, limit],
  )

  const setCode = (c: string) => {
    setLimit(PAGE_SIZE)
    setSelected([])
    setParams(c ? { code: c } : {})
  }

  const columns: Column<DiagnosisHistoryItem>[] = [
    { key: 'select', title: <input type="checkbox" aria-label={t('选择当前页')} checked={!!data?.items.length && data.items.every((r) => selected.includes(r.id))} onChange={(e) => setSelected(e.target.checked ? (data?.items.map((r) => r.id) ?? []) : [])} />,
      render: (r) => <input type="checkbox" aria-label={t('选择诊断 {id}', { id: r.id })} checked={selected.includes(r.id)} onClick={(e) => e.stopPropagation()} onChange={(e) => setSelected((ids) => e.target.checked ? [...ids, r.id] : ids.filter((id) => id !== r.id))} /> },
    { key: 'time', title: t('时间'), render: (r) => <span className="num text-xs">{r.created_at}</span> },
    { key: 'stock', title: t('股票'), render: (r) => <>{r.name} <span className="num text-xs text-muted">{r.code}</span></> },
    {
      key: 'action', title: t('建议'),
      render: (r) => <Badge className={cn(verdictClass(ACTIONS[r.action]))}>{ACTIONS[r.action] ? t(ACTIONS[r.action]) : (r.action || '--')}</Badge>,
    },
    { key: 'score', title: t('评分'), align: 'right', render: (r) => <span className="num">{r.score ?? '--'}</span> },
    { key: 'summary', title: t('结论'), render: (r) => <span className="line-clamp-2">{r.summary || '--'}</span> },
  ]

  return (
    <div>
      <PageHeader title={t('诊断历史')} description={t('历史 AI 诊断记录，可导出 Markdown 或分享图')} />
      <div className="mb-3 flex gap-2">
        <Button variant="danger" disabled={!selected.length} loading={deleting} onClick={async () => {
          if (!window.confirm(t('确定删除选中的诊断记录？'))) return
          setDeleting(true)
          try { const r = await api.deleteDiagnoses({ ids: selected }); toast.success(t('已删除 {n} 条', { n: r.deleted })); setSelected([]); await reload() }
          catch (e) { toast.error(e instanceof Error ? e.message : String(e)) } finally { setDeleting(false) }
        }}>{t('删除选中')}（{selected.length}）</Button>
        {code && <Button variant="danger" loading={deleting} onClick={async () => {
          if (!window.confirm(t('确定清理该股票的全部诊断历史？'))) return
          setDeleting(true)
          try { const r = await api.deleteDiagnoses({ code }); toast.success(t('已删除 {n} 条', { n: r.deleted })); setSelected([]); await reload() }
          catch (e) { toast.error(e instanceof Error ? e.message : String(e)) } finally { setDeleting(false) }
        }}>{t('清理本股历史')}</Button>}
      </div>
      <Card className="mb-4" bodyClassName="flex flex-wrap items-center gap-3">
        {code ? (
          <span className="inline-flex items-center gap-1 rounded border border-accent/40 bg-accent/10 px-2 py-1 text-sm text-accent">
            {t('股票 {code}', { code })}
            <button type="button" aria-label={t('清除股票筛选')} onClick={() => setCode('')}><X className="size-3.5" /></button>
          </span>
        ) : (
          <StockSearch className="w-64" placeholder={t('按股票筛选')} onSelect={(s) => setCode(s.code)} />
        )}
        <Select aria-label={t('操作建议')} value={action} onChange={(e) => { setAction(e.target.value); setLimit(PAGE_SIZE); setSelected([]) }}>
          <option value="">{t('全部建议')}</option>
          {Object.entries(ACTIONS).map(([k, v]) => <option key={k} value={k}>{t(v)}</option>)}
        </Select>
        <Select aria-label={t('时间范围')} value={days} onChange={(e) => { setDays(Number(e.target.value)); setLimit(PAGE_SIZE); setSelected([]) }}>
          {DAYS.map((d) => <option key={d.v} value={d.v}>{t(d.t)}</option>)}
        </Select>
        {data && <span className="text-xs text-muted">{t('共 {n} 条', { n: data.total })}</span>}
      </Card>
      {code && <Card className="mb-4" title={t('评分走势')}><ScoreTrendChart code={code} /></Card>}
      {error && <ErrorBox message={error} onRetry={reload} />}
      {loading && !data ? <Spinner /> : data && (
        <Card bodyClassName="p-0">
          <DataTable columns={columns} rows={data.items} rowKey={(r) => r.id} onRowClick={(r) => setOpenId(r.id)} empty={t('没有符合条件的诊断记录')} />
          {data.items.length < data.total && (
            <div className="border-t border-line p-3 text-center">
              <Button loading={loading} onClick={() => setLimit(limit + PAGE_SIZE)}>{t('加载更多')}</Button>
            </div>
          )}
        </Card>
      )}
      {openId !== null && <DetailModal id={openId} onClose={() => setOpenId(null)} onDeleted={() => { setOpenId(null); void reload() }} />}
    </div>
  )
}

function DetailModal({ id, onClose, onDeleted }: { id: number; onClose: () => void; onDeleted: () => void }) {
  const t = useT()
  const { data, error, loading } = useApi<DiagnosisRecord>(() => api.diagnosisRecord(id), [id])
  const [logOpen, setLogOpen] = useState(false)  // 展开后才渲染运行记录

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(await api.diagnosisMarkdownText(id))
      toast.success(t('已复制 Markdown'))
    } catch (e) {
      toast.error(e instanceof Error ? e.message : t('复制失败'))
    }
  }
  const remove = async () => {
    if (!window.confirm(t('确定删除这条诊断记录？'))) return
    try {
      await api.deleteDiagnosis(id)
      toast.success(t('已删除'))
      onDeleted()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : t('删除失败'))
    }
  }
  const linkClass = 'inline-flex items-center rounded-md border border-line px-3 py-1.5 text-sm font-medium hover:bg-panel-2'
  return (
    <Modal
      open
      wide
      title={data ? t('{name}({code}) 诊断 · {time}', { name: data.name, code: data.code, time: data.created_at }) : t('诊断详情')}
      onClose={onClose}
      footer={data && (
        <>
          <Button variant="danger" onClick={remove}>{t('删除')}</Button>
          <Button onClick={copy}>{t('复制 Markdown')}</Button>
          <a className={linkClass} href={api.diagnosisMarkdownUrl(id)} download>{t('下载 Markdown')}</a>
          <ShareImageButton url={api.diagnosisImageUrl(id)} filename={`diagnosis-${id}.png`} />
        </>
      )}
    >
      {error ? <ErrorBox message={error} /> : loading || !data ? <Spinner /> : (
        <>
          <DiagnosisView d={data.result} diagnosisId={data.id} />
          <details className="mt-4 rounded border border-line p-3" onToggle={(e) => setLogOpen(e.currentTarget.open)}>
            <summary className="cursor-pointer text-sm font-medium">{t('运行记录')}</summary>
            {logOpen && <div className="mt-3"><RunLogView log={data.run_log ?? data.result.run_log} /></div>}
          </details>
        </>
      )}
    </Modal>
  )
}
