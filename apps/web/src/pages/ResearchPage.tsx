// 深度研究：输入主题生成研究报告，左侧历史，右侧报告 + 可折叠证据
import { Download, Trash2 } from 'lucide-react'
import { useState } from 'react'
import { api } from '@/api/endpoints'
import type { ResearchReport } from '@/api/types'
import { Markdown } from '@/components/Markdown'
import { Button, Card, Empty, ErrorBox, Input, PageHeader, Spinner } from '@/components/ui'
import { useApi } from '@/hooks/useApi'
import { progressText, useTask } from '@/hooks/useTask'
import { toast } from '@/stores/toast'
import { cn } from '@/utils/cn'

export function ResearchPage() {
  const [topic, setTopic] = useState('')
  const [current, setCurrent] = useState<ResearchReport | null>(null)
  const [openIds, setOpenIds] = useState<Record<string, boolean>>({})
  const { run, running, progress } = useTask<ResearchReport>()
  const { data: list, error, loading, reload } = useApi(() => api.researchList(), [])

  const start = async () => {
    const text = topic.trim()
    if (!text) return
    try {
      const report = await run(() => api.startResearch(text), { success: '研究报告已生成' })
      setCurrent(report)
      reload()
    } catch {
      // 错误已由 useTask 提示
    }
  }

  const open = async (id: number) => {
    try {
      setOpenIds({})
      setCurrent(await api.researchReport(id))
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e))
    }
  }

  const remove = async (id: number) => {
    try {
      await api.deleteResearch(id)
      if (current?.id === id) setCurrent(null)
      reload()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e))
    }
  }

  return (
    <div>
      <PageHeader title="深度研究" description="拆解主题、联网检索、结合行情与主线，生成带证据引用的研究报告（约 1~2 分钟）" />
      <div className="mb-4 flex flex-wrap gap-2">
        <Input
          className="min-w-64 flex-1"
          value={topic}
          maxLength={100}
          placeholder="固态电池产业链近期催化"
          aria-label="研究主题"
          onChange={(e) => setTopic(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && !running && start()}
        />
        <Button variant="primary" loading={running} disabled={!topic.trim()} onClick={start}>开始研究</Button>
        {running && <span className="self-center text-xs text-muted">{progressText(progress) || '排队中…'}</span>}
      </div>

      <div className="grid gap-4 lg:grid-cols-[18rem_1fr]">
        <Card title="历史报告" bodyClassName="p-2">
          {loading && !list ? <Spinner /> : error ? <ErrorBox message={error} onRetry={reload} /> : !list?.length ? (
            <Empty>还没有研究报告</Empty>
          ) : (
            <ul className="space-y-1">
              {list.map((r) => (
                <li key={r.id} className={cn('group flex items-start gap-1 rounded-md p-2 hover:bg-surface-2', current?.id === r.id && 'bg-surface-2')}>
                  <button type="button" className="min-w-0 flex-1 text-left" onClick={() => open(r.id)}>
                    <div className="truncate text-sm font-medium">{r.topic}</div>
                    <div className="num text-xs text-muted">{r.created_at.replace('T', ' ')}</div>
                    <div className="line-clamp-2 text-xs text-muted">{r.summary}</div>
                  </button>
                  <button type="button" aria-label={`删除 ${r.topic}`} className="text-muted hover:text-down" onClick={() => remove(r.id)}>
                    <Trash2 className="size-3.5" />
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Card>

        <div>
          {!current ? (
            <Card><Empty>输入主题开始研究，或从左侧选择历史报告</Empty></Card>
          ) : (
            <Card
              title={current.topic}
              actions={
                <a href={api.researchMarkdownUrl(current.id)} download>
                  <Button><Download className="size-3.5" />下载 Markdown</Button>
                </a>
              }
            >
              <Markdown text={current.markdown} />
              {current.evidence.length > 0 && (
                <details className="mt-4 border-t border-border pt-3">
                  <summary className="cursor-pointer text-sm font-medium">证据（{current.evidence.length}）</summary>
                  <ul className="mt-2 space-y-1 text-xs">
                    {current.evidence.map((e) => (
                      <li key={e.id}>
                        <button type="button" className="text-left" aria-expanded={!!openIds[e.id]} onClick={() => setOpenIds((s) => ({ ...s, [e.id]: !s[e.id] }))}>
                          <span className="num font-medium">[{e.id}]</span> <span className="text-muted">{e.source}</span> <span>{e.title}</span>
                        </button>
                        {e.url && <> <a href={e.url} target="_blank" rel="noreferrer" className="text-accent underline">链接</a></>}
                        {openIds[e.id] && <div className="mt-0.5 text-muted">{e.content}</div>}
                      </li>
                    ))}
                  </ul>
                </details>
              )}
            </Card>
          )}
        </div>
      </div>
    </div>
  )
}
