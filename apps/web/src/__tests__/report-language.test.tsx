import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AppRoutes } from '@/App'

type Call = { url: string; method: string; body?: string }

const llmSettings = {
  llm: { primary: { provider: 'deepseek', model: 'deepseek-chat', api_key: '******1111', base_url: 'https://api.deepseek.com' }, backup: {}, vision: {} },
  platforms: { deepseek: { name: 'DeepSeek', base_url: 'https://api.deepseek.com', default_model: 'deepseek-chat', models: ['deepseek-chat'] } },
}

function stub(calls: Call[], language = 'zh', putStatus = 200) {
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const method = (init?.method ?? 'GET').toUpperCase()
    calls.push({ url, method, body: typeof init?.body === 'string' ? init.body : undefined })
    let body: unknown = []
    let status = 200
    if (url.includes('/settings/report')) {
      if (method === 'PUT') { status = putStatus; body = putStatus === 200 ? JSON.parse(String(init?.body)) : { detail: 'bad' } }
      else body = { language: language }
    } else if (url.includes('/settings/llm')) body = method === 'PUT' ? { ok: true } : llmSettings
    return new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } })
  }))
}

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

async function open() {
  render(<MemoryRouter initialEntries={['/settings']}><AppRoutes authEnabled={false} /></MemoryRouter>)
  fireEvent.click(await screen.findByRole('tab', { name: 'AI 模型' }))
  return (await screen.findByLabelText(/AI 输出语言/)) as HTMLSelectElement
}

const puts = (calls: Call[]) => calls.filter((c) => c.method === 'PUT' && c.url.includes('/settings/report'))

/** 切换语言后保存：保存按钮在该设置块内；若组件在切换时自动保存，则不需要再点。 */
async function saveFromSection(select: HTMLElement, calls: Call[]) {
  await new Promise((r) => setTimeout(r, 50))
  if (puts(calls).length) return
  let node: HTMLElement | null = select
  while (node && !within(node).queryByRole('button', { name: /^保存$/ })) node = node.parentElement
  if (node) fireEvent.click(within(node).getByRole('button', { name: /^保存$/ }))
}

describe('Settings AI 输出语言', () => {
  it('shows the section under the AI 模型 tab with the explanation text', async () => {
    stub([])
    const select = await open()
    expect(select.tagName).toBe('SELECT')
    expect(screen.getByText(/影响诊断、复盘、问股、深度研究和推送报告/)).toBeTruthy()
    expect(screen.getByText(/界面语言在右上角切换/)).toBeTruthy()
  })

  it('loads the current language from GET /settings/report', async () => {
    const calls: Call[] = []
    stub(calls, 'en')
    const select = await open()
    await waitFor(() => expect(select.value).toBe('en'))
    expect(calls.some((c) => c.method === 'GET' && c.url.includes('/settings/report'))).toBe(true)
  })

  it('switches to English and saves via PUT with the language body', async () => {
    const calls: Call[] = []
    stub(calls, 'zh')
    const select = await open()
    await waitFor(() => expect(select.value).toBe('zh'))
    fireEvent.change(select, { target: { value: 'en' } })
    await saveFromSection(select, calls)
    await waitFor(() => expect(puts(calls).length).toBeGreaterThan(0))
    expect(JSON.parse(puts(calls)[0].body!)).toEqual({ language: 'en' })
  })

  it('can switch back to Chinese', async () => {
    const calls: Call[] = []
    stub(calls, 'en')
    const select = await open()
    await waitFor(() => expect(select.value).toBe('en'))
    fireEvent.change(select, { target: { value: 'zh' } })
    await saveFromSection(select, calls)
    await waitFor(() => expect(puts(calls).length).toBeGreaterThan(0))
    expect(JSON.parse(puts(calls)[0].body!)).toEqual({ language: 'zh' })
  })

  it('offers exactly the zh and en options', async () => {
    stub([])
    const select = await open()
    expect(Array.from(select.options).map((o) => o.value).sort()).toEqual(['en', 'zh'])
  })
})
