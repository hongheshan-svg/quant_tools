import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AppRoutes } from '@/App'

const pick = (code: string, name: string, labels: string[], next: number | null = null) => ({
  trade_date: '2026-09-23', code, name, close: 10, change_pct: 4, fits_regime: true, score: 80, labels,
  reasons: ['理由' + name], next_change_pct: next,
})

const latest = { trade_date: '2026-09-23', picks: [pick('600004', '丁股', ['放量突破'])], performance: [], backtest: null }
const dates = {
  dates: [
    { trade_date: '2026-09-23', picks: 1, strategies: { volume_breakout: 1 }, evaluated: 0, avg_next_pct: null, win_rate: null },
    { trade_date: '2026-09-22', picks: 2, strategies: { volume_breakout: 1, oversold_rebound: 1 }, evaluated: 1, avg_next_pct: 2, win_rate: 100 },
    { trade_date: '2026-09-21', picks: 2, strategies: { volume_breakout: 1 }, evaluated: 2, avg_next_pct: 1.5, win_rate: 50 },
  ],
  strategies: [{ name: 'volume_breakout', label: '放量突破' }, { name: 'oversold_rebound', label: '超跌反弹' }],
}
const byDate: Record<string, ReturnType<typeof pick>[]> = {
  '2026-09-22': [pick('600001', '甲股', ['放量突破'], 2), pick('000003', '丙股', ['超跌反弹'])],
  '2026-09-21': [pick('600002', '乙股', ['缩量回踩'])],
}

function stub(urls: string[]) {
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input)
    urls.push(url)
    let body: unknown = {}
    if (url.includes('/screening/dates')) body = dates
    else if (url.includes('/screening/picks')) {
      const q = new URL(url, 'http://x').searchParams
      const d = q.get('trade_date') ?? '2026-09-23'
      let rows = byDate[d] ?? []
      const s = q.get('strategy')
      if (s === 'oversold_rebound') rows = rows.filter((r) => r.labels.includes('超跌反弹'))
      body = { trade_date: d, picks: rows }
    } else if (url.endsWith('/screening')) body = latest
    return new Response(JSON.stringify(body), { status: 200, headers: { 'content-type': 'application/json' } })
  }))
}

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

function renderPage() {
  return render(<MemoryRouter initialEntries={['/screening']}><AppRoutes authEnabled={false} /></MemoryRouter>)
}

describe('策略选股历史', () => {
  it('默认显示最近一次结果，日期下拉列出各日期与次日均涨幅', async () => {
    const urls: string[] = []
    stub(urls)
    renderPage()
    expect(await screen.findByText('丁股')).toBeInTheDocument()
    const select = await screen.findByLabelText('选股日期')
    await waitFor(() => expect(select.querySelectorAll('option').length).toBe(4))
    const texts = Array.from(select.querySelectorAll('option')).map((o) => o.textContent)
    expect(texts.some((x) => x?.includes('2026-09-22') && x.includes('2') && x.includes('+2.00%'))).toBe(true)
    expect(texts.some((x) => x?.includes('2026-09-23') && !x.includes('%'))).toBe(true)   // 无次日表现时不显示涨幅
    expect(urls.some((u) => u.includes('/screening/dates'))).toBe(true)
    expect(urls.some((u) => u.includes('/screening/picks'))).toBe(false)
  })

  it('切换日期后请求带 trade_date 并显示对应结果', async () => {
    const urls: string[] = []
    stub(urls)
    renderPage()
    await screen.findByText('丁股')
    const select = await screen.findByLabelText('选股日期')
    await waitFor(() => expect(select.querySelectorAll('option').length).toBe(4))
    fireEvent.change(select, { target: { value: '2026-09-22' } })
    expect(await screen.findByText('甲股')).toBeInTheDocument()
    expect(screen.getByText('丙股')).toBeInTheDocument()
    expect(screen.queryByText('丁股')).not.toBeInTheDocument()
    expect(urls.some((u) => u.includes('/screening/picks') && u.includes('trade_date=2026-09-22'))).toBe(true)
  })

  it('策略筛选请求带 strategy，选回全部后恢复', async () => {
    const urls: string[] = []
    stub(urls)
    renderPage()
    await screen.findByText('丁股')
    const dateSel = await screen.findByLabelText('选股日期')
    await waitFor(() => expect(dateSel.querySelectorAll('option').length).toBe(4))
    fireEvent.change(dateSel, { target: { value: '2026-09-22' } })
    await screen.findByText('甲股')
    const strat = screen.getByLabelText('策略筛选')
    fireEvent.change(strat, { target: { value: 'oversold_rebound' } })
    await waitFor(() => expect(screen.queryByText('甲股')).not.toBeInTheDocument())
    expect(screen.getByText('丙股')).toBeInTheDocument()
    expect(urls.some((u) => u.includes('strategy=oversold_rebound') && u.includes('trade_date=2026-09-22'))).toBe(true)
    fireEvent.change(strat, { target: { value: '' } })
    expect(await screen.findByText('甲股')).toBeInTheDocument()
  })

  it('历史概览点击某行切换到那天', async () => {
    const urls: string[] = []
    stub(urls)
    renderPage()
    await screen.findByText('丁股')
    expect(await screen.findByText('历史概览（最近 20 个交易日）')).toBeInTheDocument()
    fireEvent.click(await screen.findByRole('cell', { name: '2026-09-21' }))
    expect(await screen.findByText('乙股')).toBeInTheDocument()
    expect(screen.queryByText('丁股')).not.toBeInTheDocument()
    expect(urls.some((u) => u.includes('/screening/picks') && u.includes('trade_date=2026-09-21'))).toBe(true)
    expect((screen.getByLabelText('选股日期') as HTMLSelectElement).value).toBe('2026-09-21')
  })

  it('历史概览只显示最近 20 个交易日', async () => {
    const many = Array.from({ length: 25 }, (_, i) => ({
      trade_date: `2026-08-${String(25 - i).padStart(2, '0')}`, picks: i + 1, strategies: {}, evaluated: 0, avg_next_pct: null, win_rate: null,
    }))
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      const body = url.includes('/screening/dates') ? { dates: many, strategies: [] } : latest
      return new Response(JSON.stringify(body), { status: 200, headers: { 'content-type': 'application/json' } })
    }))
    renderPage()
    expect(await screen.findByRole('cell', { name: '2026-08-25' })).toBeInTheDocument()
    expect(screen.getByRole('cell', { name: '2026-08-06' })).toBeInTheDocument()
    expect(screen.queryByRole('cell', { name: '2026-08-05' })).not.toBeInTheDocument()
  })

  it('没有历史选股时显示空提示', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      const body = url.includes('/screening/dates') ? { dates: [], strategies: [] } : { trade_date: null, picks: [], performance: [], backtest: null }
      return new Response(JSON.stringify(body), { status: 200, headers: { 'content-type': 'application/json' } })
    }))
    renderPage()
    expect(await screen.findByText('暂无历史选股')).toBeInTheDocument()
  })
})
