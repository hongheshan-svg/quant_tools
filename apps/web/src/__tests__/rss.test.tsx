import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AppRoutes } from '@/App'

type Call = { url: string; method: string; body?: string }

const intelligence = {
  intelligence: {
    enabled: true,
    interval_minutes: 30,
    max_items_per_source: 50,
    keep_days: 7,
    sources: [
      { name: '路透中文', url: 'https://feeds.example.com/reuters.xml', enabled: true },
      { name: '雪球精选', url: 'https://feeds.example.com/xq.xml', enabled: false },
    ],
  },
}

function stub(calls: Call[], news: unknown[] = []) {
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const method = (init?.method ?? 'GET').toUpperCase()
    calls.push({ url, method, body: typeof init?.body === 'string' ? init.body : undefined })
    let body: unknown = []
    if (url.includes('/settings/intelligence/test')) body = { ok: true, title: '测试频道', count: 3, samples: ['样例一'], error: '' }
    else if (url.includes('/settings/intelligence')) body = method === 'PUT' ? { ok: true } : intelligence
    else if (url.includes('/settings/llm')) body = { llm: { primary: {}, fallback: {} }, platforms: {} }
    else if (url.includes('/news')) body = news
    return new Response(JSON.stringify(body), { status: 200, headers: { 'content-type': 'application/json' } })
  }))
}

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

async function openTab() {
  render(<MemoryRouter initialEntries={['/settings']}><AppRoutes authEnabled={false} /></MemoryRouter>)
  fireEvent.click(await screen.findByRole('tab', { name: '资讯源' }))
}

describe('SettingsPage intelligence tab', () => {
  it('has the 资讯源 tab and renders the source list', async () => {
    stub([])
    await openTab()
    expect(await screen.findByDisplayValue('路透中文')).toBeInTheDocument()
    expect(screen.getByDisplayValue('https://feeds.example.com/reuters.xml')).toBeInTheDocument()
    expect(screen.getByDisplayValue('雪球精选')).toBeInTheDocument()
  })

  it('adds a row and saves with PUT containing the new source', async () => {
    const calls: Call[] = []
    stub(calls)
    await openTab()
    await screen.findByDisplayValue('路透中文')
    fireEvent.change(screen.getByLabelText('名称', { selector: 'input:not([aria-label])' }), { target: { value: '新浪财经' } })
    fireEvent.change(screen.getByPlaceholderText('https://example.com/feed.xml'), { target: { value: 'https://feeds.example.com/sina.xml' } })
    fireEvent.click(screen.getByRole('button', { name: /添加/ }))
    expect(await screen.findByDisplayValue('新浪财经')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /保存/ }))
    await waitFor(() => expect(calls.some((c) => c.method === 'PUT' && c.url.includes('/settings/intelligence'))).toBe(true))
    const put = calls.find((c) => c.method === 'PUT' && c.url.includes('/settings/intelligence'))!
    const sent = JSON.parse(put.body ?? '{}').intelligence
    expect(sent.sources).toHaveLength(3)
    expect(sent.sources[2]).toMatchObject({ name: '新浪财经', url: 'https://feeds.example.com/sina.xml' })
    expect(sent.sources[0].name).toBe('路透中文')
  })

  it('tests a source with POST /settings/intelligence/test', async () => {
    const calls: Call[] = []
    stub(calls)
    await openTab()
    await screen.findByDisplayValue('路透中文')
    fireEvent.click(screen.getAllByRole('button', { name: /测试/ })[0])
    await waitFor(() => expect(calls.some((c) => c.method === 'POST' && c.url.includes('/settings/intelligence/test'))).toBe(true))
    const call = calls.find((c) => c.url.includes('/settings/intelligence/test'))!
    expect(JSON.parse(call.body ?? '{}').url).toBe('https://feeds.example.com/reuters.xml')
    expect(await screen.findByText(/样例一|测试频道|3/)).toBeInTheDocument()
  })
})

describe('NewsPage RSS items', () => {
  it('shows RSS·源名称 for RSS entries', async () => {
    stub([], [
      { time: '10:30', source: 'RSS·路透中文', level: '路透中文', title: '[RSS·路透中文] 央行降准', url: 'http://x/1', tags: [] },
      { time: '10:20', source: '财联社', level: '', title: '[财联社] 普通快讯', url: '', tags: [] },
    ])
    render(<MemoryRouter initialEntries={['/news']}><AppRoutes authEnabled={false} /></MemoryRouter>)
    expect((await screen.findAllByText(/RSS·路透中文/)).length).toBeGreaterThan(0)
    expect(screen.getByText(/央行降准/)).toBeInTheDocument()
  })

  it('renders tags and highlights important items', async () => {
    stub([], [
      { time: '18:30', source: '华尔街见闻', level: '头部财报', title: '[华尔街见闻] 台积电扩产', url: '', tags: ['美股', '头部企业'], important: true },
      { time: '18:20', source: '财联社', level: '普通快讯', title: '[财联社] 普通快讯', url: '', tags: [], important: false },
    ])
    render(<MemoryRouter initialEntries={['/news']}><AppRoutes authEnabled={false} /></MemoryRouter>)
    const title = await screen.findByText(/台积电扩产/)
    expect(title).toHaveClass('text-up')
    expect(screen.getByText('头部企业')).toBeInTheDocument()
    expect(screen.getByText(/普通快讯/)).not.toHaveClass('text-up')
  })
})

describe('page crash', () => {
  it('keeps the menu usable when a page throws', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => {})
    // 旧接口把 tags 返回成字符串，曾让整个界面白屏、菜单点不动
    stub([], [{ time: '18:30', source: '华尔街见闻', level: '', title: '坏数据', url: '', tags: '美股 | 头部企业' }])
    render(<MemoryRouter initialEntries={['/news']}><AppRoutes authEnabled={false} /></MemoryRouter>)
    expect(await screen.findByText('页面出错')).toBeInTheDocument()
    fireEvent.click(screen.getAllByRole('link', { name: /设置/ })[0])
    expect(await screen.findByRole('tab', { name: '资讯源' })).toBeInTheDocument()
    expect(screen.queryByText('页面出错')).not.toBeInTheDocument()
  })
})
