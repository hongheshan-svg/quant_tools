// 自选股：搜索添加、粘贴/文件批量导入、逐只 AI 诊断并推送决策仪表盘
import { Trash2 } from 'lucide-react'
import { useRef, useState, type ClipboardEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '@/api/endpoints'
import type { ImageImportResult, ImportResult, WatchlistReport, WatchlistRow } from '@/api/types'
import { DataTable, type Column } from '@/components/DataTable'
import { Markdown } from '@/components/Markdown'
import { StockSearch } from '@/components/StockSearch'
import { Button, Card, ErrorBox, Modal, PageHeader, Pct, Textarea } from '@/components/ui'
import { useApi } from '@/hooks/useApi'
import { progressText, useTask } from '@/hooks/useTask'
import { toast } from '@/stores/toast'
import { fmtNum, verdictClass } from '@/utils/format'

export function importSummary(r: ImportResult): string {
  const parts = [`新增 ${r.added.length} 只`]
  if (r.existing.length) parts.push(`已存在 ${r.existing.length} 只`)
  if (r.unknown.length) parts.push(`无法识别：${r.unknown.slice(0, 10).join('、')}`)
  if (r.over_limit.length) parts.push(`超出上限 ${r.over_limit.length} 只`)
  return parts.join('，')
}

export function WatchlistPage() {
  const navigate = useNavigate()
  const list = useApi(api.watchlist)
  const report = useApi<WatchlistReport | null>(api.watchlistReport)
  const [pasteOpen, setPasteOpen] = useState(false)
  const [pasteText, setPasteText] = useState('')
  const fileInput = useRef<HTMLInputElement>(null)
  const imageTask = useTask<ImageImportResult>()
  const [imageResult, setImageResult] = useState<ImageImportResult | null>(null)
  const [picked, setPicked] = useState<Set<string>>(new Set())
  const [adding, setAdding] = useState(false)
  const imageInput = useRef<HTMLInputElement>(null)
  const run = useTask<{ done: number; total: number; pushed: boolean; error?: string }>()

  const add = async (code: string) => {
    const r = await api.addWatch(code)
    if (r.ok) {
      toast.success(`已加入 ${r.name}(${r.code})`)
      void list.reload()
    } else toast.error(r.error ?? '添加失败')
  }

  const afterImport = (r: ImportResult) => {
    toast.info(importSummary(r))
    void list.reload()
  }

  const recognize = (file: File) => {
    imageTask
      .run(() => api.importImage(file))
      .then((r) => {
        setImageResult(r)
        setPicked(new Set(r.candidates.map((c) => c.code)))
      })
      .catch(() => {})
  }

  const onPaste = (e: ClipboardEvent<HTMLDivElement>) => {
    const file = Array.from(e.clipboardData?.files ?? []).find((f) => f.type.startsWith('image/'))
    if (file) {
      e.preventDefault()
      recognize(file)
    }
  }

  const addPicked = async () => {
    setAdding(true)
    try {
      const r = await api.importWatch([...picked].join('\n'))
      afterImport(r)
      setImageResult(null)
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e))
    } finally {
      setAdding(false)
    }
  }

  const columns: Column<WatchlistRow>[] = [
    { key: 'name', title: '股票', render: (r) => <><div>{r.name}</div><div className="num text-xs text-muted">{r.code}</div></> },
    { key: 'close', title: '最新价', align: 'right', render: (r) => <span className="num">{fmtNum(r.close)}</span> },
    { key: 'pct', title: '涨跌幅', align: 'right', render: (r) => <Pct value={r.change_pct} /> },
    { key: 'action', title: '最近诊断', render: (r) => <span className={verdictClass(r.diagnosis?.action_label)}>{r.diagnosis?.action_label ?? '未诊断'}</span> },
    { key: 'score', title: '评分', align: 'right', render: (r) => <span className="num">{r.diagnosis?.score ?? ''}</span> },
    { key: 'time', title: '诊断时间', render: (r) => <span className="num text-xs text-muted">{r.diagnosis?.created_at ?? ''}</span> },
    { key: 'one', title: '一句话结论', className: 'max-w-sm text-xs text-muted', render: (r) => r.diagnosis?.one_sentence },
    {
      key: 'remove', title: '', align: 'right', render: (r) => (
        <button type="button" aria-label={`删除 ${r.name}`} className="text-muted hover:text-danger" onClick={(e) => {
          e.stopPropagation()
          void api.removeWatch(r.code).then(() => list.reload())
        }}>
          <Trash2 className="size-4" />
        </button>
      ),
    },
  ]

  return (
    <div onPaste={onPaste}>
      <PageHeader
        title="自选股"
        description="收盘后（16:30）自动逐只 AI 诊断并推送决策仪表盘；盘中提醒也会关注自选股"
        actions={
          <Button
            variant="primary"
            loading={run.running}
            onClick={() =>
              run.run(() => api.runWatchlistReport(true), {
                success: (r) => (r.error ? r.error : `完成 ${r.done}/${r.total}，${r.pushed ? '已推送' : '未推送（没有启用推送渠道）'}`),
              }).then(() => { void list.reload(); void report.reload() }).catch(() => {})
            }
          >
            {run.running ? `逐只诊断中 ${progressText(run.progress)}` : '分析全部并推送'}
          </Button>
        }
      />
      <div className="grid gap-4 xl:grid-cols-[1.4fr_1fr]">
        <Card
          title={`自选股（${list.data?.length ?? 0}）`}
          actions={
            <>
              <StockSearch className="w-56" placeholder="添加：代码 / 名称 / 拼音" onSelect={(s) => void add(s.code)} />
              <Button onClick={() => setPasteOpen(true)}>粘贴导入</Button>
              <Button onClick={() => fileInput.current?.click()}>文件导入</Button>
              <Button loading={imageTask.running} onClick={() => imageInput.current?.click()} title="也可以直接在页面上粘贴截图">
                {imageTask.running ? '识别中…' : '识别截图'}
              </Button>
              <input
                ref={imageInput}
                type="file"
                accept="image/*"
                className="hidden"
                aria-label="选择截图"
                onChange={(e) => {
                  const file = e.target.files?.[0]
                  if (file) recognize(file)
                  e.target.value = ''
                }}
              />
              <input
                ref={fileInput}
                type="file"
                accept=".csv,.xlsx,.xls,.txt"
                className="hidden"
                aria-label="选择导入文件"
                onChange={(e) => {
                  const file = e.target.files?.[0]
                  if (file) api.importWatchFile(file).then(afterImport).catch((err) => toast.error(err.message))
                  e.target.value = ''
                }}
              />
            </>
          }
          bodyClassName="p-0"
        >
          {list.error && <ErrorBox message={list.error} onRetry={list.reload} />}
          <DataTable columns={columns} rows={list.data ?? []} rowKey={(r) => r.code} onRowClick={(r) => navigate(`/stocks/${r.code}`)} empty="还没有自选股，用上方搜索框添加，或批量导入" />
        </Card>
        <Card title="决策仪表盘" actions={report.data && <span className="text-xs text-muted">{report.data.created_at}</span>}>
          {report.data ? <Markdown text={report.data.markdown} /> : <p className="text-sm text-muted">还没有仪表盘，点「分析全部并推送」生成</p>}
        </Card>
      </div>
      <Modal
        open={imageResult !== null}
        title="截图识别结果"
        onClose={() => setImageResult(null)}
        footer={
          <Button variant="primary" loading={adding} disabled={picked.size === 0} onClick={() => void addPicked()}>
            加入自选（{picked.size}）
          </Button>
        }
      >
        {imageResult && (
          <div className="space-y-3">
            {imageResult.candidates.length === 0 && <p className="text-sm text-muted">没有识别到可用的股票</p>}
            <ul className="space-y-1">
              {imageResult.candidates.map((c) => (
                <li key={c.code}>
                  <label className="flex cursor-pointer items-center gap-2 text-sm">
                    <input
                      type="checkbox"
                      checked={picked.has(c.code)}
                      aria-label={`选择 ${c.name}`}
                      onChange={(e) => {
                        const next = new Set(picked)
                        if (e.target.checked) next.add(c.code)
                        else next.delete(c.code)
                        setPicked(next)
                      }}
                    />
                    <span>{c.name}</span>
                    <span className="num text-xs text-muted">{c.code}</span>
                  </label>
                </li>
              ))}
            </ul>
            {imageResult.unresolved.length > 0 && (
              <p className="text-xs text-muted">未识别：{imageResult.unresolved.join('、')}</p>
            )}
          </div>
        )}
      </Modal>
      <Modal
        open={pasteOpen}
        title="粘贴导入"
        onClose={() => setPasteOpen(false)}
        footer={
          <Button
            variant="primary"
            onClick={() => api.importWatch(pasteText).then((r) => { afterImport(r); setPasteOpen(false); setPasteText('') }).catch((e) => toast.error(e.message))}
            disabled={!pasteText.trim()}
          >
            导入
          </Button>
        }
      >
        <p className="mb-2 text-xs text-muted">粘贴一段文字（如券商自选股列表、聊天记录），自动识别其中的 6 位代码和股票名称</p>
        <Textarea rows={8} value={pasteText} onChange={(e) => setPasteText(e.target.value)} aria-label="导入内容" />
      </Modal>
    </div>
  )
}
