import { fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AppRoutes } from '@/App'

function stubFetch(routes: Record<string, unknown>) {
  const fn = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input)
    const key = Object.keys(routes).find((k) => url.includes(k))
    const body = key ? routes[key] : []
    return new Response(JSON.stringify(body), { status: 200, headers: { 'content-type': 'application/json' } })
  })
  vi.stubGlobal('fetch', fn)
  return fn
}

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

const notifierSettings = {
  notifier: {
    routes: {},
    telegram: { enabled: true, bot_token: '******', chat_id: '-100' },
    email: { enabled: false },
  },
  channels: { wechat: '企业微信', dingtalk: '钉钉', feishu: '飞书', email: '邮件', telegram: 'Telegram', bark: 'Bark' },
  kinds: { alert: '盘中提醒' },
  fields: {
    telegram: [
      { key: 'bot_token', label: 'Bot Token', required: true, secret: true, placeholder: '123456:ABC', type: 'password' },
      { key: 'chat_id', label: 'Chat ID', required: true, secret: false, placeholder: '', type: 'text' },
    ],
    bark: [{ key: 'device_key', label: 'Device Key', required: true, secret: true, placeholder: '', type: 'password' }],
  },
}

async function openNotifierTab() {
  render(<MemoryRouter initialEntries={['/settings']}><AppRoutes authEnabled={false} /></MemoryRouter>)
  fireEvent.click(screen.getByRole('tab', { name: '推送' }))
}

describe('SettingsPage notifier tab (new channels)', () => {
  it('renders new channels from fields with password inputs', async () => {
    stubFetch({ '/settings/llm': { llm: { primary: {}, fallback: {} }, platforms: {} }, '/settings/notifier': notifierSettings })
    await openNotifierTab()
    expect(await screen.findByRole('group', { name: 'Telegram' })).toBeInTheDocument()   // legend
    const token = screen.getByLabelText(/Bot Token/)
    expect(token).toHaveAttribute('type', 'password')
    expect(screen.getByLabelText(/Chat ID/)).toHaveAttribute('type', 'text')
    expect(screen.getByLabelText(/Device Key/)).toHaveAttribute('type', 'password')
  })

  it('shows new channels as columns in the routing table', async () => {
    stubFetch({ '/settings/llm': { llm: { primary: {}, fallback: {} }, platforms: {} }, '/settings/notifier': notifierSettings })
    await openNotifierTab()
    await screen.findByRole('group', { name: 'Telegram' })
    expect(screen.getByRole('checkbox', { name: '盘中提醒-telegram' })).toBeInTheDocument()
    expect(screen.getByRole('checkbox', { name: '盘中提醒-bark' })).toBeInTheDocument()
    const headers = screen.getAllByRole('columnheader').map((h) => h.textContent)
    expect(headers).toContain('Telegram')
    expect(headers).toContain('Bark')
  })

  it('sends a test message with the edited values', async () => {
    const fetchMock = stubFetch({
      '/settings/llm': { llm: { primary: {}, fallback: {} }, platforms: {} },
      '/settings/notifier/test/telegram': { ok: true, error: '' },
      '/settings/notifier': notifierSettings,
    })
    await openNotifierTab()
    const fieldset = await screen.findByRole('group', { name: 'Telegram' })
    fireEvent.change(screen.getByLabelText(/Chat ID/), { target: { value: '-999' } })
    fireEvent.click(within(fieldset).getByRole('button', { name: /测试/ }))
    await vi.waitFor(() => {
      const call = fetchMock.mock.calls.find(([u]) => String(u).includes('/settings/notifier/test/telegram'))
      expect(call).toBeTruthy()
      const init = (call as unknown as [unknown, RequestInit])[1]
      const body = JSON.parse(String(init.body))
      expect(body.notifier.telegram.chat_id).toBe('-999')
      expect(body.notifier.telegram.bot_token).toBe('******')
    })
  })
})
