import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AppRoutes } from '@/App'
import { api } from '@/api/endpoints'
import { LoginPage } from '@/pages/LoginPage'

function stubFetch(routes: Record<string, unknown>) {
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input)
    const key = Object.keys(routes).find((k) => url.includes(k))
    const body = key ? routes[key] : []
    return new Response(JSON.stringify(body), { status: 200, headers: { 'content-type': 'application/json' } })
  }))
}

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('app routes', () => {
  it('renders layout navigation and the dashboard', async () => {
    stubFetch({
      '/dashboard': { today: '2026-09-29', score_date: '2026-09-28', limit_up_date: '2026-09-28', top_stocks: [], limit_up_count: 0, limit_up_stocks: [],
        signals: [], trade_focus: [{ rank: 1, code: '600519', name: '贵州茅台', composite_score: 88, recommendation: 'buy', continuous_days: 0,
          sector: '白酒', change_pct: 1.2, limit_reason: '', reason: '', signal_type: 'buy', signal_strength: 0.8, signal_reason: '放量', ai_verdict: '买入', ai_advice: '' }],
        premarket_predictions: [], market_overview: { sh_index: '3250.12', sh_change_pct: 0.85, up_count: 3000, down_count: 2000 } },
      '/market/regime': { regime: '均衡', summary: '市场环境：均衡（55分）' },
    })
    render(<MemoryRouter initialEntries={['/']}><AppRoutes authEnabled={false} /></MemoryRouter>)
    expect(screen.getByRole('link', { name: /AI 问股/ })).toBeInTheDocument()
    expect(await screen.findByText('贵州茅台')).toBeInTheDocument()
    expect(screen.getByText('市场环境：均衡（55分）')).toBeInTheDocument()
    expect(screen.getByText('3250.12')).toBeInTheDocument()
  })

  it('shows not found page', () => {
    stubFetch({})
    render(<MemoryRouter initialEntries={['/nope']}><AppRoutes authEnabled={false} /></MemoryRouter>)
    expect(screen.getByText('页面不存在')).toBeInTheDocument()
  })
})

describe('LoginPage', () => {
  it('logs in and reports errors', async () => {
    const login = vi.spyOn(api, 'login').mockRejectedValueOnce(new Error('密码错误')).mockResolvedValueOnce({ ok: true })
    const onLoggedIn = vi.fn()
    render(<LoginPage passwordSet onLoggedIn={onLoggedIn} />)
    fireEvent.change(screen.getByLabelText('密码'), { target: { value: 'secret1' } })
    fireEvent.click(screen.getByRole('button', { name: '登录' }))
    expect(await screen.findByText('密码错误')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '登录' }))
    await waitFor(() => expect(onLoggedIn).toHaveBeenCalled())
    expect(login).toHaveBeenCalledWith('secret1')
  })
})
