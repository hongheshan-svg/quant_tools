import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AppRoutes } from '@/App'

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } })

const REAL = {
  snapshot: {
    account: { cash: 0, market_value: 15000, total_assets: 15000, unrealized_pnl: 0, realized_pnl: 0, cash_known: false },
    positions: [{
      account: 'real', code: '600519', name: '贵州茅台', quantity: 1500, available_quantity: 1500, avg_cost: 6.4667,
      market_price: 10, market_value: 15000, unrealized_pnl: 5300, stop_loss: 6.1, target_price: 7.4, first_date: '2026-09-01',
    }],
    warnings: [],
  },
  trades: [],
  risk: null,
}
const ACTIONS = [
  { id: 2, code: '600519', name: '贵州茅台', ex_date: '2026-09-10', action: 'bonus', action_label: '送转股', cash: 0, shares: 500, note: '', source: 'manual' },
  { id: 1, code: '600519', name: '贵州茅台', ex_date: '2026-09-10', action: 'dividend', action_label: '现金分红', cash: 300, shares: 0, note: '', source: 'manual' },
]
const PREVIEW = {
  format: 'csv', trades: [{ trade_date: '2026-09-01', code: '600519', name: '贵州茅台', side: 'buy', price: 10, quantity: 1000, fee: 0 }],
  actions: [{ ex_date: '2026-09-10', code: '600519', name: '贵州茅台', action: 'dividend', cash: 300, shares: 0 }],
  new_trades: 1, new_actions: 1, duplicates: 0, skipped: 2, warnings: [],
}

function stubFetch() {
  const fn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const method = init?.method ?? 'GET'
    if (url.includes('/real/trades/import')) {
      return url.includes('preview=true') ? json(PREVIEW) : json({ added: 1, duplicate: 0, skipped: 2, actions_added: 1, error: '' })
    }
    if (url.includes('/real/actions')) return method === 'GET' ? json(ACTIONS) : json({ ok: true, id: 3 })
    if (url.includes('/real/cash-flows')) {
      if (method === 'GET') return json(FLOWS)
      return json({ ok: true, id: 9 })
    }
    if (url.includes('/real')) return json(REAL)
    return json([])
  })
  vi.stubGlobal('fetch', fn)
  return fn
}

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

let FLOWS: unknown[] = []

const renderReal = () => render(<MemoryRouter initialEntries={['/real']}><AppRoutes authEnabled={false} /></MemoryRouter>)

describe('实盘记账：分红送转与导入预览', () => {
  it('分红送转区块按接口数据渲染', async () => {
    const fetchMock = stubFetch()
    renderReal()
    expect((await screen.findAllByText(/分红送转/)).length).toBeGreaterThan(0)
    expect(await screen.findByText('现金分红')).toBeInTheDocument()
    expect(screen.getByText('送转股')).toBeInTheDocument()
    expect(screen.getAllByText(/2026-09-10/).length).toBeGreaterThan(0)
    expect(fetchMock.mock.calls.some((c) => String(c[0]).includes('/real/actions'))).toBe(true)
  })

  it('记一笔分红送转按方案录入，请求体含 plan', async () => {
    const fetchMock = stubFetch()
    renderReal()
    await userEvent.click(await screen.findByRole('button', { name: /记一笔分红送转/ }))
    const dialog = await screen.findByRole('dialog')
    const textboxes = dialog.querySelectorAll('input')
    expect(textboxes.length).toBeGreaterThan(0)
    // 股票代码输入框（文本框）填代码
    const codeInput = Array.from(textboxes).find((i) => /代码|股票/.test(i.getAttribute('placeholder') ?? '')) as HTMLInputElement | undefined
    if (codeInput) await userEvent.type(codeInput, '600519')
    const numeric = Array.from(textboxes).filter((i) => i.type === 'number')
    if (numeric[0]) { await userEvent.clear(numeric[0]); await userEvent.type(numeric[0], '3') }
    const buttons = Array.from(dialog.querySelectorAll('button')).filter((b) => /保存|确定|确认/.test(b.textContent ?? ''))
    expect(buttons.length).toBeGreaterThan(0)
    await userEvent.click(buttons[buttons.length - 1])
    await waitFor(() => {
      const post = fetchMock.mock.calls.find((c) => String(c[0]).includes('/real/actions') && c[1]?.method === 'POST')
      expect(post).toBeTruthy()
      expect(String(post![1]?.body)).toContain('plan')
    })
  })

  it('导入交割单先预览，确认后才真正导入', async () => {
    const fetchMock = stubFetch()
    renderReal()
    await screen.findByRole("heading", { name: /实盘记账/ })
    const input = document.querySelector('input[type="file"]') as HTMLInputElement
    expect(input).toBeTruthy()
    await userEvent.upload(input, new File(['x'], 'jgd.csv', { type: 'text/csv' }))
    await waitFor(() => expect(fetchMock.mock.calls.some((c) => String(c[0]).includes('/real/trades/import') && String(c[0]).includes('preview=true'))).toBe(true))
    const importCalls = () => fetchMock.mock.calls.filter((c) => String(c[0]).includes('/real/trades/import'))
    expect(importCalls().every((c) => String(c[0]).includes('preview=true'))).toBe(true)   // 还没确认，不能真正导入
    expect(await screen.findByText(/新增/)).toBeInTheDocument()
    await userEvent.click(await screen.findByRole('button', { name: /确认导入/ }))
    await waitFor(() => expect(importCalls().some((c) => !String(c[0]).includes('preview=true'))).toBe(true))
  })
})

describe('RealPage cash flows', () => {
  it('records a deposit and shows net deposit and cumulative return in ledger mode', async () => {
    FLOWS = [{ id: 1, flow_date: '2026-09-01', direction: 'in', direction_label: '入金', amount: 100000, note: '首次入金', account: '默认' }]
    const original = REAL.snapshot.account
    REAL.snapshot.account = { ...original, net_deposit: 100000, ledger_mode: true, total_return: 2345.5 } as typeof original
    try {
      const fetchMock = stubFetch()
      renderReal()
      expect(await screen.findByText('首次入金')).toBeInTheDocument()
      expect(screen.getByText('累计收益')).toBeInTheDocument()
      fireEvent.click(screen.getByRole('button', { name: '记一笔出入金' }))
      fireEvent.change(await screen.findByLabelText('金额（元）'), { target: { value: '5000' } })
      fireEvent.change(screen.getByLabelText('方向'), { target: { value: 'out' } })
      fireEvent.click(screen.getAllByRole('button', { name: '保存' }).at(-1)!)
      await waitFor(() => {
        const post = fetchMock.mock.calls.find((c) => String(c[0]).includes('/real/cash-flows') && c[1]?.method === 'POST')
        expect(post).toBeTruthy()
        expect(JSON.parse(String(post![1]!.body))).toMatchObject({ direction: 'out', amount: 5000 })
      })
    } finally {
      REAL.snapshot.account = original
      FLOWS = []
    }
  })
})
