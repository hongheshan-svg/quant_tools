import { useCallback, useState } from 'react'
import { api } from '@/api/endpoints'
import type { Task } from '@/api/types'
import { t } from '@/i18n'
import { useTaskStore } from '@/stores/tasks'
import { toast } from '@/stores/toast'

export const POLL_MS = 800

export async function waitForTask<R>(task: Task<R>, onUpdate?: (t: Task<R>) => void, pollMs = POLL_MS): Promise<R> {
  let current = task
  onUpdate?.(current)
  while (current.status !== 'done' && current.status !== 'error') {
    await new Promise((r) => setTimeout(r, pollMs))
    current = (await api.task(current.id)) as Task<R>
    onUpdate?.(current)
  }
  if (current.status === 'error') throw new Error(current.error || t('任务失败'))
  return current.result as R
}

/** 任务正常结束、但结果里带非空 error 时（模型未配置、自选股为空等）返回该错误，否则返回空字符串 */
export function taskResultError(result: unknown): string {
  if (!result || typeof result !== 'object') return ''
  const error = (result as { error?: unknown }).error
  return typeof error === 'string' ? error.trim() : ''
}

/** 提交后台任务并等待结果；running 为 true 时按钮应禁用，progress 为最新进度。
 * 结果带 error 时提示错误而不是成功，结果照常返回（页面可以自己展示错误） */
export function useTask<R = unknown>() {
  const [running, setRunning] = useState(false)
  const [progress, setProgress] = useState<Task['progress']>(null)
  const upsert = useTaskStore((s) => s.upsert)

  const run = useCallback(
    // 接口层的任务结果类型是 unknown，这里按调用方声明的 R 使用
    async (submit: () => Promise<Task<any>>, options: { success?: string | ((r: R) => string); silent?: boolean } = {}) => {
      setRunning(true)
      setProgress(null)
      try {
        const task = await submit()
        const result = await waitForTask<R>(task as Task<R>, (t) => {
          setProgress(t.progress)
          upsert(t as Task)
        })
        const failure = taskResultError(result)
        if (failure) {
          if (!options.silent) toast.error(failure)
        } else if (options.success) {
          toast.success(typeof options.success === 'function' ? options.success(result) : options.success)
        }
        return result
      } catch (e) {
        if (!options.silent) toast.error(e instanceof Error ? e.message : String(e))
        throw e
      } finally {
        setRunning(false)
      }
    },
    [upsert],
  )

  return { run, running, progress }
}

export function progressText(progress: Task['progress']): string {
  if (!progress) return ''
  if (progress.text) return progress.text
  if (progress.total) return `${progress.done ?? 0}/${progress.total}`
  return ''
}
