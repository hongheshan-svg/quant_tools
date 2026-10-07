import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AppRoutes } from '@/App'
import { useToastStore } from '@/stores/toast'

type Call = { url: string; method: string; body?: string }

const LIST = [
  { name: 'diagnosis', label: '个股诊断', custom: false, path: 'config/templates/diagnosis.md.j2' },
  { name: 'watchlist', label: '自选股决策仪表盘', custom: true, path: 'config/templates/watchlist.md.j2' },
  { name: 'market_review', label: '大盘复盘', custom: false, path: '' },
  { name: 'daily_report', label: '日报', custom: false, path: '' },
]
const TEXTS: Record<string, string> = {
  diagnosis: '{# 内置诊断示例 #}\n# {{ name }}',
  watchlist: '自定义仪表盘 {{ trade_date }}',
}

function stub(calls: Call[], opts: { previewError?: string; putStatus?: number } = {}) {
  let customWatch = true
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const method = (init?.method ?? 'GET').toUpperCase()
    calls.push({ url, method, body: typeof init?.body === 'string' ? init.body : undefined })
    let body: unknown = []
    let status = 200
    const m = url.match(/\/settings\/templates\/([a-z_]+)(\/preview)?/)
    if (m && m[2]) {
      body = opts.previewError ? { ok: false, error: opts.previewError, markdown: '' } : { ok: true, markdown: '## 预览结果XYZ' }
    } else if (m && method === 'GET') {
      const custom = m[1] === 'watchlist' ? customWatch : false
      body = { name: m[1], label: '', custom, text: custom ? TEXTS.watchlist : (m[1] === 'diagnosis' ? TEXTS.diagnosis : '内置示例') }
    } else if (m && method === 'PUT') {
      status = opts.putStatus ?? 200
      body = status === 200 ? { ok: true, custom: true } : { detail: '第 2 行语法错误' }
    } else if (m && method === 'DELETE') {
      customWatch = false
      body = { ok: true, custom: false }
    } else if (url.includes('/settings/templates')) body = LIST
    else if (url.includes('/settings/llm')) body = { llm: { primary: {}, fallback: {} }, platforms: {} }
    return new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } })
  }))
}

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

async function openTab() {
  render(<MemoryRouter initialEntries={['/settings']}><AppRoutes authEnabled={false} /></MemoryRouter>)
  fireEvent.click(await screen.findByRole('tab', { name: '报告模板' }))
}

const editor = () => screen.findByLabelText('模板内容') as Promise<HTMLTextAreaElement>

describe('SettingsPage report templates tab', () => {
  it('shows the tab, hint text and the first template builtin sample', async () => {
    stub([])
    await openTab()
    expect(await screen.findByText(/模板使用 Jinja2 语法；没有自定义模板时使用内置格式；模板出错时自动回退内置格式/)).toBeInTheDocument()
    await waitFor(async () => expect((await editor()).value).toContain('内置诊断示例'))
    expect(screen.getByRole('button', { name: '恢复内置' })).toBeDisabled()    // 没有自定义模板时无需恢复
  })

  it('selecting another template loads its text', async () => {
    const calls: Call[] = []
    stub(calls)
    await openTab()
    await waitFor(async () => expect((await editor()).value).toContain('内置诊断示例'))
    fireEvent.change(screen.getByLabelText('选择模板'), { target: { value: 'watchlist' } })
    await waitFor(async () => expect((await editor()).value).toBe('自定义仪表盘 {{ trade_date }}'))
    expect(calls.some((c) => c.method === 'GET' && c.url.endsWith('/settings/templates/watchlist'))).toBe(true)
    expect(screen.getByRole('button', { name: '恢复内置' })).toBeEnabled()
  })

  it('preview posts the edited text and renders the markdown result', async () => {
    const calls: Call[] = []
    stub(calls)
    await openTab()
    await waitFor(async () => expect((await editor()).value).toContain('内置诊断示例'))
    fireEvent.change(await editor(), { target: { value: '# 新内容 {{ name }}' } })
    fireEvent.click(screen.getByRole('button', { name: '预览' }))
    expect(await screen.findByText('预览结果XYZ')).toBeInTheDocument()
    const call = calls.find((c) => c.method === 'POST' && c.url.endsWith('/settings/templates/diagnosis/preview'))!
    expect(JSON.parse(call.body ?? '{}')).toEqual({ text: '# 新内容 {{ name }}' })
  })

  it('shows the error when preview fails', async () => {
    stub([], { previewError: '第 1 行语法错误：xyz' })
    await openTab()
    await waitFor(async () => expect((await editor()).value).toContain('内置诊断示例'))
    fireEvent.click(screen.getByRole('button', { name: '预览' }))
    expect(await screen.findByText(/第 1 行语法错误：xyz/)).toBeInTheDocument()
  })

  it('save sends PUT with the edited text', async () => {
    const calls: Call[] = []
    stub(calls)
    await openTab()
    await waitFor(async () => expect((await editor()).value).toContain('内置诊断示例'))
    fireEvent.change(await editor(), { target: { value: '保存的模板 {{ code }}' } })
    fireEvent.click(screen.getByRole('button', { name: '保存' }))
    await waitFor(() => expect(calls.some((c) => c.method === 'PUT')).toBe(true))
    const put = calls.find((c) => c.method === 'PUT')!
    expect(put.url.endsWith('/settings/templates/diagnosis')).toBe(true)
    expect(JSON.parse(put.body ?? '{}')).toEqual({ text: '保存的模板 {{ code }}' })
    await waitFor(() => expect(screen.getByRole('button', { name: '恢复内置' })).toBeEnabled())   // 保存后变为自定义
  })

  it('save failure keeps the text and does not mark as custom', async () => {
    stub([], { putStatus: 400 })
    await openTab()
    await waitFor(async () => expect((await editor()).value).toContain('内置诊断示例'))
    fireEvent.change(await editor(), { target: { value: '{% if %}' } })
    fireEvent.click(screen.getByRole('button', { name: '保存' }))
    await waitFor(() => expect(useToastStore.getState().toasts.some((x) => x.kind === 'error' && x.text.includes('第 2 行语法错误'))).toBe(true))
    expect((await editor()).value).toBe('{% if %}')
    expect(screen.getByRole('button', { name: '恢复内置' })).toBeDisabled()
  })

  it('restore builtin sends DELETE and reloads the builtin sample', async () => {
    const calls: Call[] = []
    stub(calls)
    await openTab()
    await waitFor(async () => expect((await editor()).value).toContain('内置诊断示例'))
    fireEvent.change(screen.getByLabelText('选择模板'), { target: { value: 'watchlist' } })
    await waitFor(async () => expect((await editor()).value).toBe('自定义仪表盘 {{ trade_date }}'))
    fireEvent.click(screen.getByRole('button', { name: '恢复内置' }))
    await waitFor(() => expect(calls.some((c) => c.method === 'DELETE' && c.url.endsWith('/settings/templates/watchlist'))).toBe(true))
    await waitFor(async () => expect((await editor()).value).toBe('内置示例'))
    expect(screen.getByRole('button', { name: '恢复内置' })).toBeDisabled()
  })

  it('switching a clean template does not prompt; declining a dirty switch retains text', async () => {
    stub([])
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false)
    await openTab()
    await waitFor(async () => expect((await editor()).value).toContain('内置诊断示例'))
    fireEvent.change(screen.getByLabelText('选择模板'), { target: { value: 'watchlist' } })
    await waitFor(async () => expect((await editor()).value).toBe('自定义仪表盘 {{ trade_date }}'))
    expect(confirm).not.toHaveBeenCalled()
    fireEvent.change(await editor(), { target: { value: '尚未保存的草稿' } })
    fireEvent.change(screen.getByLabelText('选择模板'), { target: { value: 'diagnosis' } })
    expect(confirm).toHaveBeenCalled()
    expect((await editor()).value).toBe('尚未保存的草稿')
    expect(screen.getByLabelText('选择模板')).toHaveValue('watchlist')
  })
})
