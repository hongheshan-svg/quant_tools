import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AppRoutes } from '@/App'

type Call = { url: string; method: string; body?: string }

const list = [
  { id: 5, topic: '固态电池', created_at: '2026-09-29 10:00:00', summary: '固态电池摘要' },
  { id: 6, topic: '低空经济', created_at: '2026-09-28 10:00:00', summary: '低空摘要' },
]
const detail = {
  id: 5, topic: '固态电池', created_at: '2026-09-29 10:00:00',
  markdown: '# 固态电池深度报告\n\n结论正文[E1]',
  questions: ['问题一'], stocks: [],
  evidence: [{ id: 'E1', title: '证据标题甲', source: '财联社', url: 'https://x.com/a', content: '证据内容甲' }],
}

function stub(calls: Call[]) {
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const method = (init?.method ?? 'GET').toUpperCase()
    calls.push({ url, method, body: typeof init?.body === 'string' ? init.body : undefined })
    let body: unknown = []
    if (method === 'POST' && url.includes('/research')) body = { id: 't1', kind: 'research', status: 'running', progress: '拆解问题' }
    else if (/\/research\/\d+/.test(url)) body = detail
    else if (url.includes('/research')) body = list
    else if (url.includes('/tasks/')) body = { id: 't1', status: 'running' }
    return new Response(JSON.stringify(body), { status: 200, headers: { 'content-type': 'application/json' } })
  }))
}

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

function renderAt(path: string) {
  return render(<MemoryRouter initialEntries={[path]}><AppRoutes authEnabled={false} /></MemoryRouter>)
}

describe('深度研究页', () => {
  it('导航里有深度研究，页面有输入框和开始研究', async () => {
    stub([])
    renderAt('/research')
    expect(screen.getByRole('link', { name: /深度研究/ })).toBeInTheDocument()
    expect(screen.getByRole('textbox')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /开始研究/ })).toBeInTheDocument()
  })

  it('提交会 POST /research', async () => {
    const calls: Call[] = []
    stub(calls)
    renderAt('/research')
    fireEvent.change(screen.getByRole('textbox'), { target: { value: '固态电池' } })
    fireEvent.click(screen.getByRole('button', { name: /开始研究/ }))
    await waitFor(() => expect(calls.some((c) => c.method === 'POST' && c.url.includes('/research'))).toBe(true))
    const post = calls.find((c) => c.method === 'POST' && c.url.includes('/research'))!
    expect(post.body).toContain('固态电池')
  })

  it('历史列表渲染，点击后显示报告与证据', async () => {
    stub([])
    renderAt('/research')
    expect(await screen.findByText('低空经济')).toBeInTheDocument()
    fireEvent.click(await screen.findByText('固态电池'))
    expect(await screen.findByText(/固态电池深度报告/)).toBeInTheDocument()
    expect(await screen.findByText(/证据标题甲|证据内容甲/)).toBeInTheDocument()
  })
})
