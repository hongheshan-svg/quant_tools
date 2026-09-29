import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { AppRoutes } from '@/App'
import { SETTINGS_HELP } from '@/utils/settingsHelp'

type Item = { key: string; label: string; done: boolean; required: boolean; hint: string; link: string }

function setup(items: Item[]) {
  const done = items.filter((i) => i.done).length
  return { items, done, total: items.length, required_missing: items.filter((i) => i.required && !i.done).length }
}

const llmItem = (done: boolean): Item => ({ key: 'llm', label: 'AI 模型', done, required: true, hint: '填写 API Key', link: '/setup' })
const watchItem = (done: boolean): Item => ({ key: 'watchlist', label: '自选股', done, required: false, hint: '添加自选股', link: '/watchlist' })

const calls: string[] = []

function stubFetch(status: unknown) {
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input)
    calls.push(url)
    let body: unknown = []
    if (url.includes('/system/setup')) body = status
    else if (url.includes('/dashboard')) body = { today: '2026-09-29', score_date: '2026-09-28', limit_up_date: '2026-09-28', top_stocks: [], limit_up_count: 0, limit_up_stocks: [], signals: [], trade_focus: [], premarket_predictions: [], market_overview: {} }
    else if (url.includes('/settings/llm')) body = { llm: { primary: {}, backup: {}, vision: {} }, platforms: {} }
    else if (url.includes('/settings/notifier')) body = { notifier: { routes: {} }, channels: {}, kinds: {}, fields: {} }
    return new Response(JSON.stringify(body), { status: 200, headers: { 'content-type': 'application/json' } })
  }))
}

function renderAt(path: string) {
  return render(<MemoryRouter initialEntries={[path]}><AppRoutes authEnabled={false} /></MemoryRouter>)
}

beforeEach(() => {
  calls.length = 0
  try { localStorage.clear() } catch { /* ignore */ }
})
afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('Dashboard setup banner', () => {
  it('shows banner with link when required items missing', async () => {
    stubFetch(setup([llmItem(false), watchItem(false)]))
    renderAt('/')
    expect(await screen.findByText(/还有 \d+ 项配置未完成/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /去配置/ })).toHaveAttribute('href', '/setup')
  })

  it('hidden when everything is done', async () => {
    stubFetch(setup([llmItem(true), watchItem(true)]))
    renderAt('/')
    await waitFor(() => expect(calls.some((u) => u.includes('/system/setup'))).toBe(true))
    await new Promise((r) => setTimeout(r, 50))
    expect(screen.queryByText(/项配置未完成/)).toBeNull()
  })

  it('dismiss hides optional-only banner', async () => {
    stubFetch(setup([llmItem(true), watchItem(false)]))
    renderAt('/')
    await screen.findByText(/还有 1 项配置未完成/)
    fireEvent.click(screen.getByRole('button', { name: /暂不提示/ }))
    await waitFor(() => expect(screen.queryByText(/项配置未完成/)).toBeNull())
  })

  it('dismiss does not hide when required items missing', async () => {
    stubFetch(setup([llmItem(false), watchItem(false)]))
    renderAt('/')
    await screen.findByText(/项配置未完成/)
    const btn = screen.queryByRole('button', { name: /暂不提示/ })
    if (btn) fireEvent.click(btn)
    await new Promise((r) => setTimeout(r, 50))
    expect(screen.getByText(/项配置未完成/)).toBeInTheDocument()
  })
})

describe('Setup wizard page', () => {
  it('renders steps and AI model form, re-check refetches', async () => {
    stubFetch(setup([llmItem(false), watchItem(true)]))
    renderAt('/setup')
    expect(await screen.findAllByText(/AI 模型/)).not.toHaveLength(0)
    expect(screen.getAllByText(/自选股/).length).toBeGreaterThan(0)
    expect((await screen.findAllByText(/主力模型/)).length).toBeGreaterThan(0)
    const before = calls.filter((u) => u.includes('/system/setup')).length
    fireEvent.click(screen.getByRole('button', { name: /重新检查/ }))
    await waitFor(() => expect(calls.filter((u) => u.includes('/system/setup')).length).toBeGreaterThan(before))
  })
})

describe('Settings help', () => {
  it('opens on ?tab=notifier with 推送 tab selected', async () => {
    stubFetch(setup([]))
    renderAt('/settings?tab=notifier')
    const tab = await screen.findByRole('tab', { name: '推送' })
    expect(tab).toHaveAttribute('aria-selected', 'true')
  })

  it('help button opens dialog with tab help title', async () => {
    stubFetch(setup([]))
    renderAt('/settings')
    await screen.findByRole('tab', { name: 'AI 模型' })
    fireEvent.click(screen.getAllByRole('button', { name: /帮助/ })[0])
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText(SETTINGS_HELP.llm.title)).toBeInTheDocument()
  })

  it('SETTINGS_HELP covers all tab keys', () => {
    const keys = ['llm', 'notifier', 'bot', 'search', 'intelligence', 'scheduler', 'backup', 'security']
    for (const k of keys) {
      const h = (SETTINGS_HELP as Record<string, { title: string; summary: string; items: unknown[] }>)[k]
      expect(h, k).toBeTruthy()
      expect(h.title).toBeTruthy()
      expect(h.summary).toBeTruthy()
      expect(h.items.length).toBeGreaterThan(0)
    }
  })
})
