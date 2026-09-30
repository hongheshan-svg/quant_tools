// 任务中心：显示界面发起的后台任务和进度
import { ListChecks, Loader2 } from 'lucide-react'
import { useState } from 'react'
import { progressText } from '@/hooks/useTask'
import { useT } from '@/i18n'
import { useTaskStore } from '@/stores/tasks'
import { cn } from '@/utils/cn'

const STATUS = { pending: '排队中', running: '进行中', done: '完成', error: '失败' } as const

export function TaskCenter() {
  const t = useT()
  const tasks = Object.values(useTaskStore((s) => s.tasks)).sort((a, b) => b.created_at.localeCompare(a.created_at))
  const running = tasks.filter((x) => x.status === 'pending' || x.status === 'running')
  const [open, setOpen] = useState(false)
  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex items-center gap-1.5 rounded-md border border-line px-2.5 py-1.5 text-xs text-muted hover:text-text"
        aria-label={t('任务中心')}
      >
        {running.length ? <Loader2 className="size-3.5 animate-spin text-accent" /> : <ListChecks className="size-3.5" />}
        <span className="hidden sm:inline">{running.length ? t('{n} 个任务进行中', { n: running.length }) : t('任务')}</span>
      </button>
      {open && (
        <div className="absolute right-0 z-40 mt-1 w-80 rounded-md border border-line bg-panel p-2 shadow-lg">
          {tasks.length === 0 && <div className="p-2 text-xs text-muted">{t('还没有后台任务')}</div>}
          {tasks.slice(0, 12).map((task) => (
            <div key={task.id} className="flex items-center justify-between gap-2 rounded px-2 py-1.5 text-xs hover:bg-panel-2">
              <span className="truncate">{task.label}</span>
              <span className={cn('whitespace-nowrap', task.status === 'error' ? 'text-danger' : task.status === 'done' ? 'text-down' : 'text-accent')}>
                {t(STATUS[task.status])} {progressText(task.progress)}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
