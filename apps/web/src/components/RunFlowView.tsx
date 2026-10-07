import { useCallback, useState } from 'react'
import { api } from '@/api/endpoints'
import type { RunFlow } from '@/api/types'
import { useApi } from '@/hooks/useApi'
import { useT } from '@/i18n'
import { Button, ErrorBox } from '@/components/ui'

const STATUS: Record<string, string> = { success: '成功', failed: '失败', degraded: '部分失败', running: '进行中', pending: '排队中', unknown: '未知' }

export function RunFlowView({ flow, compact = false }: { flow: RunFlow; compact?: boolean }) {
  const t = useT()
  const [message, setMessage] = useState('')
  const copy = async () => {
    try { await navigator.clipboard.writeText(flow.copy_text); setMessage(t('已复制')) }
    catch { setMessage(t('复制失败，请手动选择摘要文本')) }
  }
  return <div className="space-y-2 text-xs">
    <p>{t('运行状态')}：{t(STATUS[flow.status] ?? flow.status)} · {t('运行编号')} {flow.trace_id}</p>
    <Button onClick={() => void copy()}>{t('复制排障摘要')}</Button> <span role="status">{message}</span>
    <details><summary>{t('排障摘要')}</summary><pre className="whitespace-pre-wrap break-all p-2">{flow.copy_text}</pre></details>
    {flow.truncated && <p className="text-warn">{t('记录已截断')}</p>}
    {flow.sources.map((source, i) => <p key={i}>{source.dataset} · {source.source} · {t('成功')} {source.success} / {t('失败')} {source.failure}</p>)}
    {!compact && <ol className="space-y-2 border-l border-line pl-2">{flow.nodes.map((node) => <li key={node.id}><b>{node.name}</b> · {t(STATUS[node.status] ?? node.status)}<p className="text-muted">{node.started_at ?? t('未知')} → {node.ended_at ?? t('未知')}</p>{node.detail && <p>{node.detail}</p>}</li>)}</ol>}
  </div>
}

function TaskFlow({ id, revision }: { id: string; revision?: number }) {
  const fetch = useCallback(() => api.taskFlow(id), [id])
  const { data, error } = useApi(fetch, [revision])
  return <div className="py-2">{error && <ErrorBox message={error} />}{data && <RunFlowView flow={data} />}</div>
}

export function TaskFlowDetails(props: { id: string; revision?: number }) {
  const t = useT()
  const [open, setOpen] = useState(false)
  return <details onToggle={(event) => setOpen(event.currentTarget.open)}><summary className="cursor-pointer text-accent">{t('运行详情')}</summary>{open && <TaskFlow {...props} />}</details>
}
