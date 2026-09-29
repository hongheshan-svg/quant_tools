import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AppRoutes } from '@/App'

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } })

const TASK_DONE = {
  id: 't1', kind: 'import-image', label: '识别截图', status: 'done', progress: null, error: '',
  created_at: '', started_at: null, finished_at: null,
  result: {
    candidates: [
      { code: '600519', name: '贵州茅台', raw: '茅台' },
      { code: '601919', name: '中远海控', raw: '中远' },
    ],
    unresolved: ['某某未知'],
  },
}

function stubFetch() {
  const fn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const method = init?.method ?? 'GET'
    if (url.includes('/watchlist/import-image')) return json(TASK_DONE)
    if (url.includes('/tasks/')) return json(TASK_DONE)
    if (url.includes('/watchlist/import') && method === 'POST') return json({ added: ['贵州茅台(600519)', '中远海控(601919)'], existing: [], unknown: [], over_limit: [] })
    if (url.endsWith('/watchlist') && method === 'POST') return json({ ok: true, code: '600519', name: '贵州茅台' })
    if (url.includes('/settings/llm')) {
      return json({
        llm: { primary: { provider: 'deepseek', api_key: '******1234', model: 'deepseek-chat' }, backup: {}, vision: {} },
        platforms: { deepseek: { name: 'DeepSeek', base_url: 'https://api.deepseek.com', default_model: 'deepseek-chat', models: [] }, custom: { name: '自定义', base_url: '', default_model: '', models: [] } },
      })
    }
    return json([])
  })
  vi.stubGlobal('fetch', fn)
  return fn
}

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('自选股图片识别导入', () => {
  it('上传截图后弹出候选，默认勾选，加入自选调用添加接口', async () => {
    const fetchMock = stubFetch()
    render(<MemoryRouter initialEntries={['/watchlist']}><AppRoutes authEnabled={false} /></MemoryRouter>)
    const button = await screen.findByRole('button', { name: /识别截图/ })
    expect(button).toBeInTheDocument()

    const input = document.querySelector('input[type="file"][accept*="image"]') as HTMLInputElement
    expect(input).toBeTruthy()
    await userEvent.upload(input, new File([new Uint8Array([1, 2, 3])], 'shot.png', { type: 'image/png' }))

    await waitFor(() => expect(fetchMock.mock.calls.some((c) => String(c[0]).includes('/watchlist/import-image'))).toBe(true))
    expect(await screen.findByText(/贵州茅台/)).toBeInTheDocument()
    expect(screen.getByText(/中远海控/)).toBeInTheDocument()
    expect(screen.getByText(/某某未知/)).toBeInTheDocument()
    const boxes = screen.getAllByRole('checkbox') as HTMLInputElement[]
    expect(boxes.length).toBeGreaterThanOrEqual(2)
    expect(boxes.every((b) => b.checked)).toBe(true)

    const dialog = (screen.queryByRole('dialog') ?? document.body) as HTMLElement
    await userEvent.click(within(dialog).getByRole('button', { name: /加入自选/ }))
    await waitFor(() => {
      const posts = fetchMock.mock.calls.filter((c) => {
        const url = String(c[0])
        return (c[1]?.method ?? 'GET') === 'POST' && !url.includes('import-image') && !url.includes('import-file') && url.includes('/watchlist')
      })
      expect(posts.length).toBeGreaterThan(0)
    })
    const bodies = fetchMock.mock.calls
      .filter((c) => (c[1]?.method ?? 'GET') === 'POST' && !String(c[0]).includes('import-image') && String(c[0]).includes('/watchlist'))
      .map((c) => String(c[1]?.body))
      .join(' ')
    expect(bodies).toContain('600519')
    expect(bodies).toContain('601919')
  })

  it('设置页 AI 模型标签里有图片识别模型区域', async () => {
    stubFetch()
    render(<MemoryRouter initialEntries={['/settings']}><AppRoutes authEnabled={false} /></MemoryRouter>)
    expect((await screen.findAllByText(/图片识别模型/)).length).toBeGreaterThan(0)
  })
})
