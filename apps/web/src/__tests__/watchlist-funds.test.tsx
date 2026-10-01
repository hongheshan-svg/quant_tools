import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AppRoutes } from '@/App'
import { WatchlistPage } from '@/pages/WatchlistPage'

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } })

const base = { note: '', added_at: '2026-09-25 10:00', trade_date: '2026-09-25', diagnosis: null }
const ROWS = [
  { ...base, code: '600519', name: '贵州茅台', kind: 'stock', close: 1500, change_pct: 1 },
  { ...base, code: '510300', name: '沪深300ETF', kind: 'etf', close: 4.05, change_pct: 0.8 },
  { ...base, code: 'sh000300', name: '沪深300', kind: 'index', close: 4006, change_pct: 0.6 },
]

function stubFetch() {
  const fn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const method = init?.method ?? 'GET'
    if (url.includes('/stocks/search')) return json([{ code: 'sh000300', name: '沪深300', kind: 'index' }])
    if (url.endsWith('/watchlist') && method === 'POST') return json({ ok: true, code: 'sh000300', name: '沪深300' })
    if (url.endsWith('/watchlist')) return json(ROWS)
    if (url.includes('/watchlist/report')) return json(null)
    return json([])
  })
  vi.stubGlobal('fetch', fn)
  return fn
}

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

function Where() {
  return <div data-testid="where">{useLocation().pathname}</div>
}

describe('自选股中的 ETF 与指数', () => {
  it('列表里 ETF 和指数显示类型徽标，个股不显示', async () => {
    stubFetch()
    render(<MemoryRouter initialEntries={['/watchlist']}><AppRoutes authEnabled={false} /></MemoryRouter>)
    const etfRow = (await screen.findByText('沪深300ETF')).closest('tr') as HTMLElement
    const indexRow = screen.getByText('沪深300').closest('tr') as HTMLElement
    const stockRow = screen.getByText('贵州茅台').closest('tr') as HTMLElement
    expect(within(etfRow).getByText('ETF')).toBeInTheDocument()
    expect(within(indexRow).getByText('指数')).toBeInTheDocument()
    expect(within(stockRow).queryByText('ETF')).not.toBeInTheDocument()
    expect(within(stockRow).queryByText('指数')).not.toBeInTheDocument()
    expect(within(indexRow).getByText('sh000300')).toBeInTheDocument()
  })

  it('没有 kind 字段的旧数据按个股显示（无徽标）', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith('/watchlist')) return json([{ ...base, code: '600519', name: '贵州茅台', close: 1500, change_pct: 1 }])
      return json([])
    }))
    render(<MemoryRouter initialEntries={['/watchlist']}><AppRoutes authEnabled={false} /></MemoryRouter>)
    const row = (await screen.findByText('贵州茅台')).closest('tr') as HTMLElement
    expect(within(row).queryByText('ETF')).not.toBeInTheDocument()
    expect(within(row).queryByText('指数')).not.toBeInTheDocument()
  })

  it('点击指数行进入个股页，代码带前缀', async () => {
    stubFetch()
    render(
      <MemoryRouter initialEntries={['/watchlist']}>
        <Where />
        <Routes><Route path="/watchlist" element={<WatchlistPage />} /><Route path="*" element={null} /></Routes>
      </MemoryRouter>,
    )
    await userEvent.click((await screen.findByText('沪深300')).closest('tr') as HTMLElement)
    await waitFor(() => expect(screen.getByTestId('where').textContent).toBe('/stocks/sh000300'))
  })

  it('搜索框选中指数后用规范代码调用添加接口', async () => {
    const fetchMock = stubFetch()
    render(<MemoryRouter initialEntries={['/watchlist']}><AppRoutes authEnabled={false} /></MemoryRouter>)
    await screen.findByText('沪深300ETF')
    await userEvent.type(screen.getByPlaceholderText(/添加/), '沪深300')
    const option = await screen.findByRole('option', { name: /沪深300.*指数/ })
    await userEvent.pointer({ keys: '[MouseLeft]', target: option })
    await waitFor(() => {
      const post = fetchMock.mock.calls.find((c) => String(c[0]).endsWith('/watchlist') && (c[1] as RequestInit | undefined)?.method === 'POST')
      expect(post).toBeTruthy()
      expect(JSON.parse(String((post![1] as RequestInit).body))).toEqual({ text: 'sh000300' })
    })
  })

  it('页面说明不再声称不能加 ETF 与指数', async () => {
    stubFetch()
    render(<MemoryRouter initialEntries={['/watchlist']}><AppRoutes authEnabled={false} /></MemoryRouter>)
    await screen.findByText('沪深300ETF')
    expect(document.body.textContent).not.toMatch(/不能加|不支持.*(ETF|指数)|不含\s*ETF/)
  })
})
