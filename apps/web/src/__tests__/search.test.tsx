import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AppRoutes } from '@/App'

const SEARCH = {
  search: {
    enabled: true,
    providers: ['bocha', 'tavily'],
    bocha: { api_keys: ['******abcd'] },
    tavily: { api_keys: [] },
    searxng: { base_urls: [] },
    max_results: 8,
    days: 7,
    cache_minutes: 30,
  },
  providers: { bocha: '博查', tavily: 'Tavily', serpapi: 'SerpAPI', brave: 'Brave', anspire: 'Anspire', minimax: 'MiniMax', searxng: 'SearXNG' },
}

const TEST_RESULT = {
  results: [
    { provider: 'bocha', label: '博查', ok: true, count: 3, error: '', samples: ['比亚迪销量创新高'] },
    { provider: 'tavily', label: 'Tavily', ok: false, count: 0, error: 'HTTP 401 无效Key', samples: [] },
  ],
}

interface Call { method: string; url: string; body: unknown }

function stubApi() {
  const calls: Call[] = []
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const method = (init?.method ?? 'GET').toUpperCase()
    let body: unknown = undefined
    if (typeof init?.body === 'string') { try { body = JSON.parse(init.body) } catch { body = init.body } }
    calls.push({ method, url, body })
    let data: unknown = []
    if (url.includes('/settings/search/test')) data = TEST_RESULT
    else if (url.includes('/settings/search')) data = method === 'GET' ? SEARCH : { ok: true }
    else if (url.includes('/settings/llm')) data = { llm: { primary: {}, fallback: {} }, platforms: {} }
    return new Response(JSON.stringify(data), { status: 200, headers: { 'content-type': 'application/json' } })
  }))
  return calls
}

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

async function openSearchTab() {
  render(<MemoryRouter initialEntries={['/settings']}><AppRoutes authEnabled={false} /></MemoryRouter>)
  fireEvent.click(screen.getByRole('tab', { name: '联网搜索' }))
}

describe('SettingsPage search tab', () => {
  it('shows saved provider config', async () => {
    const calls = stubApi()
    await openSearchTab()
    expect((await screen.findAllByText(/博查/)).length).toBeGreaterThan(0)
    expect(calls.some((c) => c.method === 'GET' && c.url.includes('/settings/search'))).toBe(true)
    // 已保存的 Key 以掩码回显，不出现明文
    await waitFor(() => expect(screen.getAllByDisplayValue('******abcd').length).toBeGreaterThan(0))
  })

  it('saves via PUT with the masked key untouched', async () => {
    const calls = stubApi()
    await openSearchTab()
    await screen.findAllByText(/博查/)
    await waitFor(() => expect(screen.getAllByDisplayValue('******abcd').length).toBeGreaterThan(0))
    fireEvent.click(screen.getByRole('button', { name: '保存' }))
    await waitFor(() => expect(calls.some((c) => c.method === 'PUT' && c.url.includes('/settings/search'))).toBe(true))
    const put = calls.find((c) => c.method === 'PUT' && c.url.includes('/settings/search'))!
    const search = (put.body as { search: typeof SEARCH.search }).search
    expect(search.enabled).toBe(true)
    expect(JSON.stringify(search.bocha)).toContain('******abcd')
    expect(search.providers).toContain('bocha')
  })

  it('tests providers via POST and shows the results', async () => {
    const calls = stubApi()
    await openSearchTab()
    await screen.findAllByText(/博查/)
    fireEvent.click(screen.getByRole('button', { name: /测试/ }))
    await waitFor(() => expect(calls.some((c) => c.method === 'POST' && c.url.includes('/settings/search/test'))).toBe(true))
    const post = calls.find((c) => c.method === 'POST' && c.url.includes('/settings/search/test'))!
    expect((post.body as { search: unknown }).search).toBeTruthy()
    expect(typeof (post.body as { query: unknown }).query).toBe('string')
    expect(await screen.findByText(/比亚迪销量创新高/)).toBeInTheDocument()
    expect(screen.getByText(/HTTP 401 无效Key/)).toBeInTheDocument()
  })
})
