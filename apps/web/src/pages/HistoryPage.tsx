// 诊断历史：按股票、操作建议、时间筛选历史 AI 诊断，查看详情、导出 Markdown / 分享图、删除
import { X } from 'lucide-react'
import { useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { api } from '@/api/endpoints'
import type { DiagnosisHistoryItem, DiagnosisRecord } from '@/api/types'
import { DataTable, type Column } from '@/components/DataTable'
import { DiagnosisView } from '@/components/DiagnosisView'
import { StockSearch } from '@/components/StockSearch'
import { Badge, Button, Card, ErrorBox, Modal, PageHeader, Select, Spinner } from '@/components/ui'
import { useApi } from '@/hooks/useApi'
import { toast } from '@/stores/toast'
import { verdictClass } from '@/utils/format'
import { cn } from '@/utils/cn'

const PAGE_SIZE = 50
const ACTIONS: Record<string, string> = { buy: '买入', add: '加仓', hold: '持有', watch: '观望', reduce: '减仓', sell: '卖出', avoid: '回避' }
const DAYS = [{ v: 7, t: '近 7 天' }, { v: 30, t: '近 30 天' }, { v: 90, t: '近 90 天' }, { v: 0, t: '不限' }]

export function HistoryPage() {
  const [params, setParams] = useSearchParams()
  const code = params.get('code') ?? ''
  const [action, setAction] = useState('')
  const [days, setDays] = useState(30)
  const [limit, setLimit] = useState(PAGE_SIZE)
  const [openId, setOpenId] = useState<number | null>(null)
  const { data, error, loading, reload } = useApi(
    () => api.diagnosisHistoryList({ code: code || undefined, action: action || undefined, days, limit, offset: 0 }),
    [code, action, days, limit],
  )

  const setCode = (c: string) => {
    setLimit(PAGE_SIZE)
    setParams(c ? { code: c } : {})
  }

  const columns: Column<DiagnosisHistoryItem>[] = [
    { key: 'time', title: '时间', render: (r) => <span className="num text-xs">{r.created_at}</span> },
    { key: 'stock', title: '股票', render: (r) => <>{r.name} <span className="num text-xs text-muted">{r.code}</span></> },
    {
      key: 'action', title: '建议',
      render: (r) => <Badge className={cn(verdictClass(ACTIONS[r.action]))}>{ACTIONS[r.action] ?? (r.action || '--')}</Badge>,
    },
    { key: 'score', title: '评分', align: 'right', render: (r) => <span className="num">{r.score ?? '--'}</span> },
    { key: 'summary', title: '结论', render: (r) => <span className="line-clamp-2">{r.summary || '--'}</span> },
  ]

  return (
    <div>
      <PageHeader title="诊断历史" description="历史 AI 诊断记录，可导出 Markdown 或分享图" />
      <Card className="mb-4" bodyClassName="flex flex-wrap items-center gap-3">
        {code ? (
          <span className="inline-flex items-center gap-1 rounded border border-accent/40 bg-accent/10 px-2 py-1 text-sm text-accent">
            股票 {code}
            <button type="button" aria-label="清除股票筛选" onClick={() => setCode('')}><X className="size-3.5" /></button>
          </span>
        ) : (
          <StockSearch className="w-64" placeholder="按股票筛选" onSelect={(s) => setCode(s.code)} />
        )}
        <Select aria-label="操作建议" value={action} onChange={(e) => { setAction(e.target.value); setLimit(PAGE_SIZE) }}>
          <option value="">全部建议</option>
          {Object.entries(ACTIONS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </Select>
        <Select aria-label="时间范围" value={days} onChange={(e) => { setDays(Number(e.target.value)); setLimit(PAGE_SIZE) }}>
          {DAYS.map((d) => <option key={d.v} value={d.v}>{d.t}</option>)}
        </Select>
        {data && <span className="text-xs text-muted">共 {data.total} 条</span>}
      </Card>
      {error && <ErrorBox message={error} onRetry={reload} />}
      {loading && !data ? <Spinner /> : data && (
        <Card bodyClassName="p-0">
          <DataTable columns={columns} rows={data.items} rowKey={(r) => r.id} onRowClick={(r) => setOpenId(r.id)} empty="没有符合条件的诊断记录" />
          {data.items.length < data.total && (
            <div className="border-t border-line p-3 text-center">
              <Button loading={loading} onClick={() => setLimit(limit + PAGE_SIZE)}>加载更多</Button>
            </div>
          )}
        </Card>
      )}
      {openId !== null && <DetailModal id={openId} onClose={() => setOpenId(null)} onDeleted={() => { setOpenId(null); void reload() }} />}
    </div>
  )
}

function DetailModal({ id, onClose, onDeleted }: { id: number; onClose: () => void; onDeleted: () => void }) {
  const { data, error, loading } = useApi<DiagnosisRecord>(() => api.diagnosisRecord(id), [id])

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(await api.diagnosisMarkdownText(id))
      toast.success('已复制 Markdown')
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '复制失败')
    }
  }
  const remove = async () => {
    if (!window.confirm('确定删除这条诊断记录？')) return
    try {
      await api.deleteDiagnosis(id)
      toast.success('已删除')
      onDeleted()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '删除失败')
    }
  }
  const linkClass = 'inline-flex items-center rounded-md border border-line px-3 py-1.5 text-sm font-medium hover:bg-panel-2'
  return (
    <Modal
      open
      wide
      title={data ? `${data.name}(${data.code}) 诊断 · ${data.created_at}` : '诊断详情'}
      onClose={onClose}
      footer={data && (
        <>
          <Button variant="danger" onClick={remove}>删除</Button>
          <Button onClick={copy}>复制 Markdown</Button>
          <a className={linkClass} href={api.diagnosisMarkdownUrl(id)} download>下载 Markdown</a>
          <a className={linkClass} href={api.diagnosisImageUrl(id)} download>下载分享图</a>
        </>
      )}
    >
      {error ? <ErrorBox message={error} /> : loading || !data ? <Spinner /> : <DiagnosisView d={data.result} />}
    </Modal>
  )
}
