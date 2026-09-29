import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { AppRoutes } from '@/App'
import { api } from '@/api/endpoints'
import { SEARCH_DEBOUNCE_MS, StockSearch } from '@/components/StockSearch'

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } })

const BARS = Array.from({ length: 5 }, (_, i) => ({
  code: 'sh000300', name: '沪深300', trade_date: `2026-09-2${i + 1}`, open: 4000 + i, high: 4010 + i, low: 3990 + i,
  close: 4002 + i, volume: 1e9, amount: 3e11, change_pct: 0.5, turnover: null,
}))

function stubFetch() {
  const fn = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input)
    if (url.includes('/stocks/sh000300/daily')) return json(BARS)
    if (url.includes('/stocks/sh000300/news')) return json({ news: [], notices: [] })
    if (url.includes('/stocks/sh000300/diagnosis')) return json(null)
    return json([])
  })
  vi.stubGlobal('fetch', fn)
  return fn
}

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('搜索下拉的品种标签', () => {
  beforeEach(() => vi.useFakeTimers())
  afterEach(() => vi.useRealTimers())

  it('指数和 ETF 显示标签，个股不显示', async () => {
    vi.spyOn(api, 'searchStocks').mockResolvedValue([
      { code: '600519', name: '贵州茅台', kind: 'stock' },
      { code: 'sh000300', name: '沪深300', kind: 'index' },
      { code: '510300', name: '沪深300ETF', kind: 'etf' },
    ] as never)
    render(<StockSearch onSelect={vi.fn()} />)
    fireEvent.change(screen.getByLabelText('搜索股票'), { target: { value: '沪深300' } })
    await act(async () => {
      await vi.advanceTimersByTimeAsync(SEARCH_DEBOUNCE_MS + 10)
    })
    const options = screen.getAllByRole('option')
    expect(options).toHaveLength(3)
    expect(options[0].textContent).not.toMatch(/指数|ETF/)
    expect(options[1].textContent).toContain('指数')
    expect(options[2].textContent).toContain('ETF')
  })
})

describe('StockPage 打开指数', () => {
  it('显示行情，但不显示加入自选', async () => {
    stubFetch()
    render(<MemoryRouter initialEntries={['/stocks/sh000300']}><AppRoutes authEnabled={false} /></MemoryRouter>)
    expect((await screen.findAllByText(/沪深300/)).length).toBeGreaterThan(0)
    await waitFor(() => expect(screen.getAllByText(/4,?006/).length).toBeGreaterThan(0))
    await act(async () => { await new Promise((r) => setTimeout(r, 50)) })
    expect(screen.queryByText('加入自选')).not.toBeInTheDocument()
    expect(screen.queryByText('移出自选')).not.toBeInTheDocument()
  })

  it('普通个股仍显示加入自选', async () => {
    const fn = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.includes('/stocks/600519/daily')) return json(BARS.map((b) => ({ ...b, code: '600519', name: '贵州茅台' })))
      if (url.includes('/stocks/600519/news')) return json({ news: [], notices: [] })
      return json([])
    })
    vi.stubGlobal('fetch', fn)
    render(<MemoryRouter initialEntries={['/stocks/600519']}><AppRoutes authEnabled={false} /></MemoryRouter>)
    expect(await screen.findByText('加入自选')).toBeInTheDocument()
  })
})
