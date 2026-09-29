import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AppRoutes } from '@/App'

const item = {
  id: 7, code: '600519', name: '贵州茅台', action: 'buy', score: 82, confidence: '高', trade_date: '2026-09-21',
  status: 'active', status_reason: '', expires_on: '2026-09-28', horizon_days: 5, stop_loss: 9, target_price: 12,
  entry_low: 9.8, entry_high: 10.2, invalidation: '跌破9元', ret_1d: 1.2, ret_3d: null, ret_5d: null,
  max_adverse_pct: -1, max_favorable_pct: 2, feedback: '', feedback_note: '',
}
const list = { total: 1, items: [item] }
const stats = { total: 1, active: 1, hit_rate: 60, avg_ret: 1.5, by_status: {}, by_action: {} }

function stub(calls: { url: string; method: string; body?: string }[]) {
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const method = init?.method ?? 'GET'
    calls.push({ url, method, body: init?.body as string | undefined })
    let body: unknown = {}
    if (/\/signals\/stats/.test(url)) body = stats
    else if (/\/signals\/7\/feedback/.test(url)) body = { ok: true }
    else if (/\/signals\/7/.test(url)) body = item
    else if (/\/signals\/review\//.test(url)) body = { samples: 0, hits: 0, hit_rate: 0, avg_ret: 0, avg_adverse: 0, bias: '', text: '样本不足' }
    else if (/\/signals/.test(url)) body = list
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

describe('决策信号页', () => {
  it('导航里有决策信号并渲染表格', async () => {
    const calls: { url: string; method: string }[] = []
    stub(calls)
    renderAt('/signals')
    expect(screen.getByRole('link', { name: /决策信号/ })).toBeInTheDocument()
    expect(await screen.findByText('贵州茅台')).toBeInTheDocument()
    await waitFor(() => expect(calls.some((c) => c.url.includes('/signals/stats'))).toBe(true))
  })

  it('点行打开详情，点有用发送反馈', async () => {
    const calls: { url: string; method: string; body?: string }[] = []
    stub(calls)
    renderAt('/signals')
    fireEvent.click(await screen.findByText('贵州茅台'))
    const btn = await screen.findByRole('button', { name: /有用/ })
    fireEvent.click(btn)
    await waitFor(() => expect(calls.some((c) => c.method === 'PUT' && /\/signals\/7\/feedback/.test(c.url))).toBe(true))
    const put = calls.find((c) => c.method === 'PUT')!
    expect(put.body).toContain('useful')
  })
})
