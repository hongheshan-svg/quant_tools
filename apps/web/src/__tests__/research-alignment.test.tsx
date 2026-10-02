import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ShareImageButton } from '@/components/ShareImageButton'
import { waitForTask } from '@/hooks/useTask'
import type { Task } from '@/api/types'

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('研究工作台交互', () => {
  it('图片准备完成后下一次点击立即调用原生分享', async () => {
    const share = vi.fn<(data: ShareData) => Promise<void>>().mockResolvedValue(undefined)
    vi.stubGlobal('navigator', { share, canShare: () => true })
    vi.stubGlobal('fetch', vi.fn(async () => new Response(new Blob(['png'], { type: 'image/png' }))))
    render(<ShareImageButton url="/api/v1/stocks/diagnoses/1/image" />)
    fireEvent.click(screen.getByRole('button', { name: '准备分享图' }))
    const button = await screen.findByRole('button', { name: '分享图片' })
    expect(share).not.toHaveBeenCalled()
    fireEvent.click(button)
    expect(share).toHaveBeenCalledTimes(1)
    expect(share.mock.calls[0][0].files![0]).toBeInstanceOf(File)
  })

  it('SSE 接收最终状态并关闭连接', async () => {
    const close = vi.fn()
    const source: { onmessage?: (event: { data: string }) => void } = {}
    vi.stubGlobal('EventSource', class {
      close = close
      onmessage?: (event: { data: string }) => void
      constructor() { Object.setPrototypeOf(source, this) }
    })
    const task = { id: 'one', status: 'running', progress: null, result: null, error: '' } as Task<{ value: number }>
    const waiting = waitForTask(task)
    source!.onmessage!({ data: JSON.stringify({ ...task, status: 'done', result: { value: 42 } }) })
    await expect(waiting).resolves.toEqual({ value: 42 })
    expect(close).toHaveBeenCalledTimes(1)
  })

  it('SSE 断开后回退到轮询', async () => {
    const source: { onerror?: () => void } = {}
    vi.stubGlobal('EventSource', class {
      close = vi.fn()
      onerror?: () => void
      constructor() { Object.setPrototypeOf(source, this) }
    })
    const task = { id: 'one', status: 'running', progress: null, result: null, error: '' } as Task<{ value: number }>
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ ...task, status: 'done', result: { value: 7 } }), { headers: { 'content-type': 'application/json' } })))
    const waiting = waitForTask(task)
    source!.onerror!()
    await expect(waiting).resolves.toEqual({ value: 7 })
    await waitFor(() => expect(fetch).toHaveBeenCalled())
  })
})
