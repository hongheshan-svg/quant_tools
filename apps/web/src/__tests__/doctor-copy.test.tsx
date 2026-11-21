import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, expect, it, vi } from 'vitest'
import { SetupPage } from '@/pages/SetupPage'

const REPORT = { status: 'error', summary: { errors: 1, warnings: 0 }, environment: {}, checks: {},
  text: '环境诊断：有错误（1 项检查有错误，0 项有警告）\n[大模型] 有错误（litellm）：primary deepseek/deepseek-v4-flash Key 0 个' }

function stubFetch() {
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input)
    let body: unknown = { items: [{ key: 'watchlist', label: '自选股', done: true, required: false, hint: '已完成', link: '/watchlist' }], done: 1, total: 1, required_missing: 0 }
    if (url.includes('/system/doctor')) body = REPORT
    return new Response(JSON.stringify(body), { status: 200, headers: { 'content-type': 'application/json' } })
  }))
}

const renderPage = () => render(<MemoryRouter><SetupPage /></MemoryRouter>)

afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks() })

it('copies the redacted doctor report text', async () => {
  stubFetch()
  const copy = vi.fn().mockResolvedValue(undefined)
  vi.stubGlobal('navigator', { clipboard: { writeText: copy } })
  renderPage()
  fireEvent.click(await screen.findByRole('button', { name: '复制诊断信息' }))
  await waitFor(() => expect(copy).toHaveBeenCalledWith(REPORT.text))
  expect(document.querySelector('pre')).toBeNull()
})

it('shows the report for manual copy when the clipboard is unavailable', async () => {
  stubFetch()
  vi.stubGlobal('navigator', { clipboard: { writeText: vi.fn().mockRejectedValue(new Error('denied')) } })
  renderPage()
  fireEvent.click(await screen.findByRole('button', { name: '复制诊断信息' }))
  expect(await screen.findByText('无法写入剪贴板，请手动复制下面的诊断信息（已脱敏）')).toBeInTheDocument()
  expect(document.querySelector('pre')?.textContent).toBe(REPORT.text)
})
