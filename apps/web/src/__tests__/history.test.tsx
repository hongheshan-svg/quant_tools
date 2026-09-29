import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AppRoutes } from '@/App'

const list = {
  total: 2,
  items: [
    { id: 11, code: '600519', name: '贵州茅台', trade_date: '2026-09-25', action: 'buy', score: 82, summary: '茅台看多摘要', created_at: '2026-09-28 15:40' },
    { id: 12, code: '601919', name: '中远海控', trade_date: '2026-09-25', action: 'hold', score: 55, summary: '海控观望摘要', created_at: '2026-09-27 10:00' },
  ],
}
const detail = { id: 11, code: '600519', name: '贵州茅台', trade_date: '2026-09-25', action: 'buy', score: 82, created_at: '2026-09-28 15:40',
  result: { action: 'buy', score: 82, one_sentence: '茅台详情结论' } }

function stub(urls: string[]) {
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input)
    urls.push(url)
    let body: unknown = []
    if (/\/stocks\/diagnoses\/\d+/.test(url)) body = detail
    else if (url.includes('/stocks/diagnoses')) body = list
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

describe('诊断历史页', () => {
  it('导航里有诊断历史，并渲染列表', async () => {
    stub([])
    renderAt('/history')
    expect(screen.getByRole('link', { name: /诊断历史/ })).toBeInTheDocument()
    expect(await screen.findByText('贵州茅台')).toBeInTheDocument()
    expect(screen.getByText('中远海控')).toBeInTheDocument()
  })

  it('点击一行打开详情并给出下载链接', async () => {
    const urls: string[] = []
    stub(urls)
    renderAt('/history')
    fireEvent.click(await screen.findByText('贵州茅台'))
    await waitFor(() => expect(urls.some((u) => /\/stocks\/diagnoses\/11(\?|$)/.test(u))).toBe(true))
    const md = await screen.findByRole('link', { name: /下载 Markdown/ })
    expect(md.getAttribute('href')).toContain('/stocks/diagnoses/11/markdown')
    const img = screen.getByRole('link', { name: /下载分享图/ })
    expect(img.getAttribute('href')).toContain('/stocks/diagnoses/11/image')
  })

  it('URL 带 code 时请求带上 code 参数', async () => {
    const urls: string[] = []
    stub(urls)
    renderAt('/history?code=600519')
    await screen.findByText('贵州茅台')
    expect(urls.some((u) => u.includes('/stocks/diagnoses') && u.includes('code=600519'))).toBe(true)
  })
})
