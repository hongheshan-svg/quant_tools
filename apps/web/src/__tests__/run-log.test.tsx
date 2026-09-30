import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AppRoutes } from '@/App'

const list = {
  total: 1,
  items: [{ id: 11, code: '600519', name: '贵州茅台', trade_date: '2026-09-25', action: 'buy', score: 82, summary: '茅台摘要', created_at: '2026-09-28 15:40' }],
}
const runLog = {
  steps: [
    { name: '资金流查询', kind: 'data', ok: true, ms: 123, detail: '' },
    { name: '决策', kind: 'llm', ok: true, ms: 4567, detail: 'gpt-x' },
  ],
  total_ms: 4690,
  model: 'gpt-x',
}
const detailOf = (run_log: unknown) => ({
  id: 11, code: '600519', name: '贵州茅台', trade_date: '2026-09-25', action: 'buy', score: 82, created_at: '2026-09-28 15:40',
  result: { action: 'buy', score: 82, one_sentence: '茅台详情结论' }, run_log,
})
const point = (id: number, score: number) => ({ id, created_at: `2026-09-2${id} 15:00`, trade_date: `2026-09-2${id}`, score, action: 'buy', close: 100 + id })

function stub(urls: string[], run_log: unknown, trend: unknown[]) {
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input)
    urls.push(url)
    let body: unknown = []
    if (url.includes('diagnosis-trend')) body = trend
    else if (/\/stocks\/diagnoses\/\d+/.test(url)) body = detailOf(run_log)
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

describe('诊断运行记录与评分趋势', () => {
  it('详情弹窗展开运行记录后显示步骤与耗时', async () => {
    stub([], runLog, [])
    renderAt('/history')
    fireEvent.click(await screen.findByText('贵州茅台'))
    const toggle = await screen.findByText(/运行记录/)
    fireEvent.click(toggle)
    const dialog = screen.getByRole('dialog')
    expect(await within(dialog).findByText('资金流查询')).toBeInTheDocument()
    expect(within(dialog).getByText('决策')).toBeInTheDocument()
    expect(within(dialog).getByText('123 ms')).toBeInTheDocument()
    expect(within(dialog).getByText('4.6 s')).toBeInTheDocument()
  })

  it('run_log 为 null 时提示没有运行记录', async () => {
    stub([], null, [])
    renderAt('/history')
    fireEvent.click(await screen.findByText('贵州茅台'))
    fireEvent.click(await screen.findByText(/运行记录/))
    expect(await screen.findByText(/没有运行记录/)).toBeInTheDocument()
  })

  it('按股票筛选时请求评分趋势接口', async () => {
    const urls: string[] = []
    stub(urls, null, [point(1, 60), point(2, 70), point(3, 80)])
    renderAt('/history?code=600519')
    await screen.findByText('贵州茅台')
    await waitFor(() => expect(urls.some((u) => u.includes('/stocks/600519/diagnosis-trend'))).toBe(true))
  })

  it('趋势数据少于 2 个点时显示提示', async () => {
    stub([], null, [point(1, 60)])
    renderAt('/history?code=600519')
    await screen.findByText('贵州茅台')
    expect(await screen.findByText(/(不足|至少|暂无|没有|再诊断).*(趋势|诊断|2)|(趋势|评分).*(不足|至少|暂无|没有)/)).toBeInTheDocument()
  })
})
