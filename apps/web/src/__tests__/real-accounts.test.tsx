import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { AppRoutes } from '@/App'

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } })

const acct = (name: string, trades = 0) => ({ name, broker: '', note: '', trades, positions: trades ? 1 : 0, market_value: 0, cash: null })

const position = (accounts: string[]) => ({
  account: 'real', accounts, code: '600519', name: '贵州茅台', quantity: 1500, available_quantity: 1500, avg_cost: 10,
  market_price: 10, market_value: 15000, unrealized_pnl: 0, stop_loss: 9.5, target_price: 11.5, first_date: '2026-09-01',
})
const real = (accounts: string[], account = '') => ({
  snapshot: {
    account: { cash: 0, market_value: 15000, total_assets: 15000, unrealized_pnl: 0, realized_pnl: 0, cash_known: false },
    positions: [position(accounts)], warnings: [],
  },
  trades: [],
  risk: null,
  _account: account,
})

let accounts: ReturnType<typeof acct>[] = []

function stubFetch() {
  const fn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const method = init?.method ?? 'GET'
    if (url.includes('/real/accounts')) {
      if (method === 'POST') {
        const body = JSON.parse(String(init?.body))
        accounts = [...accounts, acct(body.name)]
        return json({ ok: true })
      }
      return json(accounts)
    }
    if (url.includes('/real/actions')) return json([])
    if (url.includes('/real/cash-flows')) return json([])
    if (/\/real(\?|$)/.test(url)) {
      const m = /account=([^&]*)/.exec(url)
      const account = m ? decodeURIComponent(m[1]) : ''
      return json(real(account ? [account] : ['默认', '招商'], account))
    }
    return json([])
  })
  vi.stubGlobal('fetch', fn)
  return fn
}

const renderReal = () => render(<MemoryRouter initialEntries={['/real']}><AppRoutes authEnabled={false} /></MemoryRouter>)
const realCalls = (fn: ReturnType<typeof stubFetch>) =>
  fn.mock.calls.map((c) => String(c[0])).filter((u) => /\/real(\?|$)/.test(u))

beforeEach(() => {
  try { localStorage.clear() } catch { /* 忽略 */ }
  accounts = [acct('默认')]
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  try { localStorage.clear() } catch { /* 忽略 */ }
})

describe('实盘记账：多账户', () => {
  it('只有默认账户时不显示账户列，但有管理账户入口', async () => {
    stubFetch()
    renderReal()
    expect(await screen.findByText('贵州茅台')).toBeInTheDocument()
    expect(screen.queryByRole('columnheader', { name: '账户' })).toBeNull()
    expect(screen.getByRole('button', { name: /管理账户/ })).toBeInTheDocument()
  })

  it('有多个账户时，全部账户视图显示账户列', async () => {
    accounts = [acct('默认'), acct('招商', 2)]
    stubFetch()
    renderReal()
    expect(await screen.findByRole('columnheader', { name: '账户' })).toBeInTheDocument()
    expect(screen.getAllByText(/招商/).length).toBeGreaterThan(0)
  })

  it('切换账户后请求带 account，并保存到 localStorage', async () => {
    accounts = [acct('默认'), acct('招商', 2)]
    const fn = stubFetch()
    renderReal()
    await screen.findByText('贵州茅台')
    const select = (await screen.findAllByRole('combobox')).find((s) => within(s).queryByRole('option', { name: '全部账户' }))
    expect(select).toBeTruthy()
    await userEvent.selectOptions(select!, '招商')
    await waitFor(() => expect(realCalls(fn).some((u) => u.includes('account=' + encodeURIComponent('招商')))).toBe(true))
    expect(localStorage.getItem('quant-real-account')).toBe('招商')
    // 选了单个账户后不再显示账户列
    await waitFor(() => expect(screen.queryByRole('columnheader', { name: '账户' })).toBeNull())
  })

  it('启动时读取 localStorage 里保存的账户', async () => {
    accounts = [acct('默认'), acct('招商', 2)]
    localStorage.setItem('quant-real-account', '招商')
    const fn = stubFetch()
    renderReal()
    await waitFor(() => expect(realCalls(fn).some((u) => u.includes('account=' + encodeURIComponent('招商')))).toBe(true))
  })

  it('管理账户弹窗新增账户后列表刷新', async () => {
    const fn = stubFetch()
    renderReal()
    await screen.findByText('贵州茅台')
    await userEvent.click(screen.getByRole('button', { name: /管理账户/ }))
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText('默认')).toBeInTheDocument()
    const input = within(dialog).getAllByRole('textbox')[0]
    await userEvent.type(input, '华泰')
    await userEvent.click(within(dialog).getByRole('button', { name: /新增|添加/ }))
    await waitFor(() => {
      const post = fn.mock.calls.find((c) => String(c[0]).includes('/real/accounts') && c[1]?.method === 'POST')
      expect(post).toBeTruthy()
      expect(String(post![1]?.body)).toContain('华泰')
    })
    expect(await within(await screen.findByRole('dialog')).findByText('华泰')).toBeInTheDocument()
  })
})
