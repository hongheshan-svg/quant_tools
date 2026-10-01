import { act, renderHook } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/api/endpoints'
import { request } from '@/api/client'
import { progressText, taskResultError, useTask, waitForTask } from '@/hooks/useTask'
import { toast } from '@/stores/toast'
import type { Task } from '@/api/types'

const task = (status: Task['status'], extra: Partial<Task> = {}): Task => ({
  id: 't1', kind: 'demo', label: '演示', status, progress: null, result: null, error: '',
  created_at: '', started_at: null, finished_at: null, ...extra,
})

afterEach(() => vi.restoreAllMocks())

describe('waitForTask', () => {
  it('polls until done and reports progress', async () => {
    const poll = vi.spyOn(api, 'task')
      .mockResolvedValueOnce(task('running', { progress: { done: 1, total: 3 } }))
      .mockResolvedValueOnce(task('done', { result: { n: 3 } }))
    const updates: string[] = []
    const result = await waitForTask(task('pending'), (t) => updates.push(`${t.status}:${progressText(t.progress)}`), 0)
    expect(result).toEqual({ n: 3 })
    expect(poll).toHaveBeenCalledTimes(2)
    expect(updates).toEqual(['pending:', 'running:1/3', 'done:'])
  })

  it('throws task errors', async () => {
    vi.spyOn(api, 'task').mockResolvedValueOnce(task('error', { error: '失败了' }))
    await expect(waitForTask(task('running'), undefined, 0)).rejects.toThrow('失败了')
  })
})

describe('request', () => {
  it('raises ApiError with detail and broadcasts 401', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: '请先登录' }), { status: 401, headers: { 'content-type': 'application/json' } })))
    const listener = vi.fn()
    window.addEventListener('auth:required', listener)
    await expect(request('/dashboard')).rejects.toMatchObject({ message: '请先登录', status: 401 })
    expect(listener).toHaveBeenCalled()
    window.removeEventListener('auth:required', listener)
    vi.unstubAllGlobals()
  })

  it('formats validation errors', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: [{ msg: '价格必须大于 0' }] }), { status: 422, headers: { 'content-type': 'application/json' } })))
    await expect(request('/real/trades', { method: 'POST' })).rejects.toThrow('价格必须大于 0')
    vi.unstubAllGlobals()
  })
})

describe('useTask result errors', () => {
  it('detects non-empty error fields only', () => {
    expect(taskResultError({ error: '所有LLM模型均调用失败' })).toBe('所有LLM模型均调用失败')
    expect(taskResultError({ added: 1, error: '' })).toBe('')
    expect(taskResultError(null)).toBe('')
    expect(taskResultError([1, 2])).toBe('')
  })

  it('shows an error toast instead of the success message and still returns the result', async () => {
    const ok = vi.spyOn(toast, 'success')
    const bad = vi.spyOn(toast, 'error')
    vi.spyOn(api, 'task').mockResolvedValueOnce(task('done', { result: { error: '自选股为空，先在【自选股】页添加' } }))
    const { result } = renderHook(() => useTask<{ error: string }>())
    let value: unknown
    await act(async () => { value = await result.current.run(async () => task('running'), { success: '已完成' }) })
    expect(value).toEqual({ error: '自选股为空，先在【自选股】页添加' })
    expect(bad).toHaveBeenCalledWith('自选股为空，先在【自选股】页添加')
    expect(ok).not.toHaveBeenCalled()
  })
})
