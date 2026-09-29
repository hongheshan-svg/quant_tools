import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AlertsPage } from '@/pages/AlertsPage'

type Call = { url: string; method: string; body?: string }

function stub(calls: Call[]) {
  const rules = [
    { code: '600519', type: 'price_cross', direction: 'above', price: 1800, enabled: true, note: '' },
    { code: '000001', type: 'macd_cross', direction: 'golden', enabled: true, note: '' },
  ]
  const types = {
    price_cross: { label: '价格突破', fields: [{ key: 'direction', label: '方向', type: 'select', options: ['above', 'below'] }, { key: 'price', label: '价格', type: 'number' }] },
    macd_cross: { label: 'MACD 金叉死叉', fields: [{ key: 'direction', label: '方向', type: 'select', options: ['golden', 'dead'] }] },
  }
  const settings = { enabled: true, cooldown_minutes: 30, big_drop_pct: -7, near_stop_pct: 2, market_regime: true, regime_score_drop: 15, watchlist: [] }
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const method = (init?.method ?? 'GET').toUpperCase()
    calls.push({ url, method, body: typeof init?.body === 'string' ? init.body : undefined })
    let body: unknown = []
    if (url.includes('/alerts/rules/test')) body = { triggered: false, message: '未触发', quote: null }
    else if (url.includes('/alerts/rules')) body = method === 'PUT' ? { rules } : { rules, types }
    else if (url.includes('/alerts/settings')) body = settings
    return new Response(JSON.stringify(body), { status: 200, headers: { 'content-type': 'application/json' } })
  }))
}

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

function renderPage() {
  return render(<MemoryRouter><AlertsPage /></MemoryRouter>)
}

describe('AlertsPage', () => {
  it('has three tabs', async () => {
    stub([])
    renderPage()
    for (const name of ['提醒记录', '提醒规则', '提醒设置']) {
      expect(await screen.findByRole('tab', { name })).toBeInTheDocument()
    }
  })

  it('renders rules and saves changes with PUT', async () => {
    const calls: Call[] = []
    stub(calls)
    renderPage()
    fireEvent.click(await screen.findByRole('tab', { name: '提醒规则' }))
    expect(await screen.findByText(/600519/)).toBeInTheDocument()
    expect(screen.getByText(/000001/)).toBeInTheDocument()
    const toggles = await screen.findAllByLabelText('启用')
    expect(toggles).toHaveLength(2)
    fireEvent.click(toggles[0])
    fireEvent.click(screen.getByRole('button', { name: /保存/ }))
    await waitFor(() => expect(calls.some((c) => c.method === 'PUT' && c.url.includes('/alerts/rules'))).toBe(true))
    const put = calls.find((c) => c.method === 'PUT' && c.url.includes('/alerts/rules'))!
    const sent = JSON.parse(put.body ?? '{}')
    expect(sent.rules).toHaveLength(2)
    expect(sent.rules[0].code).toBe('600519')
    expect(sent.rules[0].enabled).toBe(false)
  })

  it('saves settings with PUT', async () => {
    const calls: Call[] = []
    stub(calls)
    renderPage()
    fireEvent.click(await screen.findByRole('tab', { name: '提醒设置' }))
    await screen.findByDisplayValue('30')
    fireEvent.click(screen.getByRole('button', { name: /保存/ }))
    await waitFor(() => expect(calls.some((c) => c.method === 'PUT' && c.url.includes('/alerts/settings'))).toBe(true))
  })
})
