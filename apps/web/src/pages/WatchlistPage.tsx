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
import { t, useT } from '@/i18n'
import { toast } from '@/stores/toast'
import { fmtNum, verdictClass } from '@/utils/format'
import { FUND_LABELS } from '@/utils/fund'

export function importSummary(r: ImportResult): string {
  const parts = [t('新增 {n} 只', { n: r.added.length })]
  if (r.existing.length) parts.push(t('已存在 {n} 只', { n: r.existing.length }))
  if (r.unknown.length) parts.push(t('无法识别：{list}', { list: r.unknown.slice(0, 10).join('、') }))
  if (r.over_limit.length) parts.push(t('超出上限 {n} 只', { n: r.over_limit.length }))
  return parts.join(t('，'))
}

export function WatchlistPage() {
  const t = useT()
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
      toast.success(t('已加入 {name}({code})', { name: r.name ?? '', code: r.code ?? '' }))
      void list.reload()
    } else toast.error(r.error ?? t('添加失败'))
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
    { key: 'name', title: t('股票'), render: (r) => (
      <>
        <div>
          {r.name}
          {r.kind && r.kind !== 'stock' && (
            <span className="ml-1.5 rounded bg-accent/10 px-1 py-0.5 text-[10px] font-normal text-accent">{t(FUND_LABELS[r.kind])}</span>
          )}
        </div>
        <div className="num text-xs text-muted">{r.code}</div>
      </>
    ) },
    { key: 'close', title: t('最新价'), align: 'right', render: (r) => <span className="num">{fmtNum(r.close)}</span> },
    { key: 'pct', title: t('涨跌幅'), align: 'right', render: (r) => <Pct value={r.change_pct} /> },
    { key: 'action', title: t('最近诊断'), render: (r) => <span className={verdictClass(r.diagnosis?.action_label)}>{t(r.diagnosis?.action_label ?? '未诊断')}</span> },
    { key: 'score', title: t('评分'), align: 'right', render: (r) => <span className="num">{r.diagnosis?.score ?? ''}</span> },
    { key: 'time', title: t('诊断时间'), render: (r) => <span className="num text-xs text-muted">{r.diagnosis?.created_at ?? ''}</span> },
    { key: 'one', title: t('一句话结论'), className: 'max-w-sm text-xs text-muted', render: (r) => r.diagnosis?.one_sentence },
    {
      key: 'remove', title: '', align: 'right', render: (r) => (
        <button type="button" aria-label={t('删除 {name}', { name: r.name })} className="text-muted hover:text-danger" onClick={(e) => {
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
        title={t('自选股')}
        description={t('收盘后（16:30）自动逐只 AI 诊断并推送决策仪表盘（支持 ETF 和指数）；盘中提醒也会关注个股自选')}
        actions={
          <Button
            variant="primary"
            loading={run.running}
            onClick={() =>
              run.run(() => api.runWatchlistReport(true), {
                success: (r) => (r.error ? r.error : t('完成 {done}/{total}，{state}', { done: r.done, total: r.total, state: r.pushed ? t('已推送') : t('未推送（没有启用推送渠道）') })),
              }).then(() => { void list.reload(); void report.reload() }).catch(() => {})
            }
          >
            {run.running ? t('逐只诊断中 {p}', { p: progressText(run.progress) }) : t('分析全部并推送')}
          </Button>
        }
      />
      <div className="grid gap-4 xl:grid-cols-[1.4fr_1fr]">
        <Card
          title={t('自选股（{n}）', { n: list.data?.length ?? 0 })}
          actions={
            <>
              <StockSearch className="w-56" placeholder={t('添加：股票 / ETF / 指数')} onSelect={(s) => void add(s.code)} />
              <Button onClick={() => setPasteOpen(true)}>{t('粘贴导入')}</Button>
              <Button onClick={() => fileInput.current?.click()}>{t('文件导入')}</Button>
              <Button loading={imageTask.running} onClick={() => imageInput.current?.click()} title={t('也可以直接在页面上粘贴截图')}>
                {imageTask.running ? t('识别中…') : t('识别截图')}
              </Button>
              <input
                ref={imageInput}
                type="file"
                accept="image/*"
                className="hidden"
                aria-label={t('选择截图')}
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
                aria-label={t('选择导入文件')}
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
          <DataTable columns={columns} rows={list.data ?? []} rowKey={(r) => r.code} onRowClick={(r) => navigate(`/stocks/${r.code}`)} empty={t('还没有自选股，用上方搜索框添加，或批量导入')} />
        </Card>
        <Card title={t('决策仪表盘')} actions={report.data && <span className="text-xs text-muted">{report.data.created_at}</span>}>
          {report.data ? <Markdown text={report.data.markdown} /> : <p className="text-sm text-muted">{t('还没有仪表盘，点「分析全部并推送」生成')}</p>}
        </Card>
      </div>
      <Modal
        open={imageResult !== null}
        title={t('截图识别结果')}
        onClose={() => setImageResult(null)}
        footer={
          <Button variant="primary" loading={adding} disabled={picked.size === 0} onClick={() => void addPicked()}>
            {t('加入自选（{n}）', { n: picked.size })}
          </Button>
        }
      >
        {imageResult && (
          <div className="space-y-3">
            {imageResult.candidates.length === 0 && <p className="text-sm text-muted">{t('没有识别到可用的股票')}</p>}
            <ul className="space-y-1">
              {imageResult.candidates.map((c) => (
                <li key={c.code}>
                  <label className="flex cursor-pointer items-center gap-2 text-sm">
                    <input
                      type="checkbox"
                      checked={picked.has(c.code)}
                      aria-label={t('选择 {name}', { name: c.name })}
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
              <p className="text-xs text-muted">{t('未识别：{list}', { list: imageResult.unresolved.join('、') })}</p>
            )}
          </div>
        )}
      </Modal>
      <Modal
        open={pasteOpen}
        title={t('粘贴导入')}
        onClose={() => setPasteOpen(false)}
        footer={
          <Button
            variant="primary"
            onClick={() => api.importWatch(pasteText).then((r) => { afterImport(r); setPasteOpen(false); setPasteText('') }).catch((e) => toast.error(e.message))}
            disabled={!pasteText.trim()}
          >
            {t('导入')}
          </Button>
        }
      >
        <p className="mb-2 text-xs text-muted">{t('粘贴一段文字（如券商自选股列表、聊天记录），自动识别其中的 6 位代码和股票名称（批量导入只识别个股，ETF 和指数请用上方搜索框添加）')}</p>
        <Textarea rows={8} value={pasteText} onChange={(e) => setPasteText(e.target.value)} aria-label={t('导入内容')} />
      </Modal>
    </div>
  )
}
