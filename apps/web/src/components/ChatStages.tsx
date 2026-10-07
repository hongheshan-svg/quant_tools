import { useEffect, useState } from 'react'
import type { ChatStage } from '@/api/types'
import { useT } from '@/i18n'

const STATUS: Record<string, string> = { started: '运行中', completed: '完成', failed: '失败', cancelled: '已取消', timeout: '超时', budget_skipped: '预算不足，已跳过' }

export function ChatStages({ events = [], live = false }: { events?: ChatStage[]; live?: boolean }) {
  const t = useT()
  const [now, setNow] = useState(Date.now())
  const stages = [...new Map(events.map((event) => [event.stage_id, event])).values()]
  const running = live && stages.some((event) => event.status === 'started')
  useEffect(() => {
    if (!running) return
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [running])
  if (!stages.length) return null
  return <details className="rounded border border-line p-2 text-xs" open={live}>
    <summary>{t('执行阶段与耗时')}</summary>
    <ol className="mt-2 space-y-1">{stages.map((event) => {
      const started = event.occurred_at ? Date.parse(event.occurred_at) : NaN
      const elapsed = live && event.status === 'started' && Number.isFinite(started) ? Math.max(event.elapsed_ms, now - started) : event.elapsed_ms
      return <li key={event.stage_id} className={['failed', 'timeout'].includes(event.status) ? 'text-danger' : 'text-muted'}>
        {t(event.name)} {event.scope?.code} · {t(STATUS[event.status] ?? event.status)} · {(Math.max(0, elapsed) / 1000).toFixed(1)}s
        {event.remaining_ms !== undefined && <span> · {t('剩余预算')} {(event.remaining_ms / 1000).toFixed(1)}s</span>}
        {event.reason && <span> · {event.reason}</span>}
      </li>
    })}</ol>
  </details>
}
