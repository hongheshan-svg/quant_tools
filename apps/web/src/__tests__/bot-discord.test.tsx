import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AppRoutes } from '@/App'

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('SettingsPage bot tab: Discord', () => {
  it('saves discord token and comma separated allowed_channels', async () => {
    const calls: { url: string; method: string; body?: string }[] = []
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const method = (init?.method ?? 'GET').toUpperCase()
      calls.push({ url, method, body: typeof init?.body === 'string' ? init.body : undefined })
      let body: unknown = []
      if (url.includes('/settings/llm')) body = { llm: { primary: {}, fallback: {} }, platforms: {} }
      else if (url.includes('/settings/bot')) {
        body = method === 'PUT'
          ? { ok: true, started: [], restart_required: false, background: true }
          : { bot: { dingtalk: {}, feishu: {}, discord: { enabled: false, token: 'old-token', guild_mode: 'mention', allowed_channels: ['old1'] }, allowed_users: [] }, running: [] }
      }
      return new Response(JSON.stringify(body), { status: 200, headers: { 'content-type': 'application/json' } })
    }))
    render(<MemoryRouter initialEntries={['/settings']}><AppRoutes authEnabled={false} /></MemoryRouter>)
    fireEvent.click(await screen.findByRole('tab', { name: '聊天机器人' }))
    expect((await screen.findAllByText(/Discord/)).length).toBeGreaterThan(0)
    const token = screen.getByLabelText(/Bot Token/) as HTMLInputElement
    expect(token.type).toBe('password')
    // 等设置同步进表单后再填写，避免后到的同步覆盖输入
    await waitFor(() => expect(token.value).toBe('old-token'))
    await waitFor(() => expect((screen.getByLabelText(/频道/) as HTMLInputElement).value).toContain('old1'))
    fireEvent.change(token, { target: { value: 'tok-123' } })
    fireEvent.change(screen.getByLabelText(/频道/), { target: { value: 'c1, c2，c3' } })
    await waitFor(() => expect(token.value).toBe('tok-123'))
    await waitFor(() => expect((screen.getByLabelText(/频道/) as HTMLInputElement).value).toMatch(/c1.*c2.*c3/))
    fireEvent.click(screen.getByRole('button', { name: '保存' }))
    await waitFor(() => expect(calls.some((c) => c.method === 'PUT' && c.url.includes('/settings/bot'))).toBe(true))
    const put = calls.find((c) => c.method === 'PUT' && c.url.includes('/settings/bot'))!
    const sent = JSON.parse(put.body ?? '{}')
    expect(sent.bot.discord.token).toBe('tok-123')
    expect(sent.bot.discord.allowed_channels).toEqual(['c1', 'c2', 'c3'])
  })
})
