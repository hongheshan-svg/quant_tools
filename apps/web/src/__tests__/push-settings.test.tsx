import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AppRoutes } from '@/App'
import { AlertsPage } from '@/pages/AlertsPage'

type Call = { url: string; method: string; body?: string }

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

const notifierSettings = {
  notifier: {
    routes: {},
    wechat: { enabled: true, webhook_url: 'https://qyapi.weixin.qq.com/x?key=1' },
    telegram: { enabled: true, bot_token: '******', chat_id: '-100' },
    email: { enabled: false },
    image: { channels: [], kinds: ['daily_report'], max_chars: 1500 },
  },
  channels: { wechat: '企业微信', dingtalk: '钉钉', feishu: '飞书', email: '邮件', telegram: 'Telegram' },
  kinds: { daily_report: '每日报告', alert: '盘中提醒', system_error: '系统错误' },
  image_channels: ['wechat', 'telegram', 'email', 'discord', 'ntfy'],
  fields: {
    telegram: [
      { key: 'bot_token', label: 'Bot Token', required: true, secret: true, placeholder: '', type: 'password' },
      { key: 'chat_id', label: 'Chat ID', required: true, secret: false, placeholder: '', type: 'text' },
    ],
  },
}

function stubSettings(calls: Call[]) {
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const method = (init?.method ?? 'GET').toUpperCase()
    calls.push({ url, method, body: typeof init?.body === 'string' ? init.body : undefined })
    let body: unknown = []
    if (url.includes('/settings/llm')) body = { llm: { primary: {}, fallback: {} }, platforms: {} }
    else if (url.includes('/settings/notifier')) body = method === 'PUT' ? { ok: true } : notifierSettings
    return new Response(JSON.stringify(body), { status: 200, headers: { 'content-type': 'application/json' } })
  }))
}

describe('SettingsPage image push', () => {
  it('saves notifier.image.channels after ticking a channel', async () => {
    const calls: Call[] = []
    stubSettings(calls)
    render(<MemoryRouter initialEntries={['/settings']}><AppRoutes authEnabled={false} /></MemoryRouter>)
    fireEvent.click(screen.getByRole('tab', { name: '推送' }))
    const block = await screen.findByRole('group', { name: /图片推送/ })
    // 只有支持图片的渠道可选（钉钉、飞书不在其中）
    expect(within(block).queryByRole('checkbox', { name: /钉钉/ })).toBeNull()
    // 等表单用接口数据完成初始化，否则勾选会被随后的同步覆盖
    await waitFor(() => expect(screen.getByLabelText(/Chat ID/)).toHaveValue('-100'))
    const box = within(block).getByRole('checkbox', { name: /企业微信|wechat/ })
    fireEvent.click(box)
    await waitFor(() => expect(box).toBeChecked())
    fireEvent.click(screen.getAllByRole('button', { name: '保存' }).at(-1)!)
    await waitFor(() => expect(calls.some((c) => c.method === 'PUT' && c.url.includes('/settings/notifier'))).toBe(true))
    const put = calls.find((c) => c.method === 'PUT' && c.url.includes('/settings/notifier'))!
    const sent = JSON.parse(put.body ?? '{}')
    expect(sent.notifier.image.channels).toContain('wechat')
  })
})

describe('AlertsPage settings: severity and digest', () => {
  it('saves min_severity and daily_digest', async () => {
    const calls: Call[] = []
    const settings = {
      enabled: true, cooldown_minutes: 30, big_drop_pct: -7, near_stop_pct: 2, market_regime: true, regime_score_drop: 15,
      watchlist: [], min_severity: 'info', daily_digest: false, digest_time: '15:20',
    }
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const method = (init?.method ?? 'GET').toUpperCase()
      calls.push({ url, method, body: typeof init?.body === 'string' ? init.body : undefined })
      let body: unknown = []
      if (url.includes('/alerts/settings')) body = method === 'PUT' ? JSON.parse(String(init?.body)) : settings
      else if (url.includes('/alerts/rules')) body = { rules: [], types: {} }
      return new Response(JSON.stringify(body), { status: 200, headers: { 'content-type': 'application/json' } })
    }))
    render(<MemoryRouter><AlertsPage /></MemoryRouter>)
    fireEvent.click(await screen.findByRole('tab', { name: '提醒设置' }))
    const level = await screen.findByLabelText(/最低推送级别/)
    fireEvent.change(level, { target: { value: 'critical' } })
    fireEvent.click(screen.getByLabelText(/提醒日报/))
    fireEvent.click(screen.getByRole('button', { name: /保存/ }))
    await waitFor(() => expect(calls.some((c) => c.method === 'PUT' && c.url.includes('/alerts/settings'))).toBe(true))
    const sent = JSON.parse(calls.find((c) => c.method === 'PUT' && c.url.includes('/alerts/settings'))!.body ?? '{}')
    expect(sent.min_severity).toBe('critical')
    expect(sent.daily_digest).toBe(true)
  })
})
