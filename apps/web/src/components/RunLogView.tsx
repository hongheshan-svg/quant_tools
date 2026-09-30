// 诊断运行记录：每步的类型、成败、耗时和说明，顶部显示总耗时与模型
import type { RunLog } from '@/api/types'
import { Badge } from '@/components/ui'
import { useT } from '@/i18n'
import { cn } from '@/utils/cn'

const KIND_LABELS = { data: '取数', llm: '模型', note: '护栏' } as const

const fmtMs = (ms: number) => (ms >= 1000 ? `${(ms / 1000).toFixed(1)} s` : `${ms} ms`)

export function RunLogView({ log }: { log: RunLog | null | undefined }) {
  const t = useT()
  if (!log || !log.steps?.length) {
    return <p className="text-sm text-muted">{t('这条诊断没有运行记录（旧版本生成）')}</p>
  }
  const failed = log.steps.filter((s) => !s.ok).length
  return (
    <div className="text-sm">
      <p className="mb-2 text-xs text-muted">
        {t('总耗时')} <span className="num text-text">{fmtMs(log.total_ms)}</span>
        {log.model && <> · {t('模型')} <span className="num text-text">{log.model}</span></>}
        {failed > 0 && <span className="ml-2 text-warn">{t('{n} 步失败', { n: failed })}</span>}
      </p>
      <ul className="divide-y divide-line rounded border border-line">
        {log.steps.map((s, i) => (
          <li key={i} className={cn('flex flex-wrap items-baseline gap-2 px-3 py-1.5', !s.ok && 'bg-warn/10')}>
            <Badge tone={s.kind === 'llm' ? 'accent' : s.kind === 'note' ? 'warn' : 'default'}>{t(KIND_LABELS[s.kind] ?? s.kind)}</Badge>
            {s.kind === 'note' ? (
              <span className="min-w-0 flex-1 break-words">{s.detail || s.name}</span>
            ) : (
              <>
                <span className="font-medium">{s.name}</span>
                <span className={cn('text-xs', s.ok ? 'text-down' : 'text-warn')}>{s.ok ? t('成功') : t('失败')}</span>
                <span className="num text-xs text-muted">{fmtMs(s.ms)}</span>
                {s.detail && <span className={cn('min-w-0 flex-1 break-words text-xs', s.ok ? 'text-muted' : 'text-warn')}>{s.detail}</span>}
              </>
            )}
          </li>
        ))}
      </ul>
    </div>
  )
}
