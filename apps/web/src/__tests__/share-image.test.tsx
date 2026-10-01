import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AppRoutes } from '@/App'

type Call = { url: string; method: string; body?: string }

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } })

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

const review = { trade_date: '2026-09-25', created_at: '2026-09-25 16:10', headline: '', stance: '均衡', position: '5成', markdown: '## 复盘正文' }
const wlReport = { trade_date: '2026-09-25', created_at: '2026-09-25 16:30', markdown: '## 仪表盘正文', items: [], failed: [] }

const notifierSettings = {
  notifier: {
    routes: {},
    wechat: { enabled: true, webhook_url: 'https://qyapi.weixin.qq.com/x?key=1' },
    telegram: { enabled: true, bot_token: '******', chat_id: '-100' },
    email: { enabled: false },
    image: { channels: ['wechat'], kinds: ['daily_report'], max_chars: 1500, brand: '旧品牌', footer: '旧页脚', qr_url: '' },
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

function stub(calls: Call[]) {
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const method = (init?.method ?? 'GET').toUpperCase()
    calls.push({ url, method, body: typeof init?.body === 'string' ? init.body : undefined })
    if (url.includes('/market/review')) return json(review)
    if (url.includes('/watchlist/report')) return json(wlReport)
    if (url.endsWith('/watchlist')) return json([])
    if (url.includes('/settings/llm')) return json({ llm: { primary: {}, fallback: {} }, platforms: {} })
    if (url.includes('/settings/notifier')) return json(method === 'PUT' ? { ok: true } : notifierSettings)
    return json([])
  }))
}

const renderAt = (path: string) =>
  render(<MemoryRouter initialEntries={[path]}><AppRoutes authEnabled={false} /></MemoryRouter>)

describe('分享图按钮', () => {
  it('复盘页有分享图链接，指向 /market/review/image', async () => {
    stub([])
    renderAt('/review')
    await screen.findByText(/2026-09-25 复盘/)
    const link = await screen.findByRole('link', { name: /分享图/ })
    expect(link.getAttribute('href')).toContain('/market/review/image')
  })

  it('复盘页没有复盘时不显示分享图按钮', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => json(null)))
    renderAt('/review')
    await screen.findByText(/还没有复盘/)
    expect(screen.queryByRole('link', { name: /分享图/ })).toBeNull()
  })

  it('自选股页决策仪表盘有分享图链接，指向 /watchlist/report/image', async () => {
    stub([])
    renderAt('/watchlist')
    await screen.findByText('仪表盘正文')
    const link = await screen.findByRole('link', { name: /分享图/ })
    expect(link.getAttribute('href')).toContain('/watchlist/report/image')
  })

  it('自选股页没有仪表盘时不显示分享图按钮', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) =>
      json(String(input).includes('/watchlist/report') ? null : [])))
    renderAt('/watchlist')
    await screen.findByText(/还没有仪表盘/)
    expect(screen.queryByRole('link', { name: /分享图/ })).toBeNull()
  })
})

describe('设置页分享图品牌', () => {
  it('品牌字段显示已有值，修改后保存的 PUT body 含 brand/footer/qr_url', async () => {
    const calls: Call[] = []
    stub(calls)
    renderAt('/settings')
    fireEvent.click(screen.getByRole('tab', { name: '推送' }))
    const block = await screen.findByRole('group', { name: /图片推送/ })
    // 等表单用接口数据完成初始化，否则输入会被随后的同步覆盖
    await waitFor(() => expect(screen.getByLabelText(/Chat ID/)).toHaveValue('-100'))
    const brand = within(block).getByLabelText(/品牌/)
    const footer = within(block).getByLabelText(/底部文字/)
    const qr = within(block).getByLabelText(/二维码/)
    await waitFor(() => expect(brand).toHaveValue('旧品牌'))
    expect(footer).toHaveValue('旧页脚')
    fireEvent.change(brand, { target: { value: '新品牌' } })
    fireEvent.change(footer, { target: { value: '新页脚' } })
    fireEvent.change(qr, { target: { value: 'https://example.com/me' } })
    await waitFor(() => expect(brand).toHaveValue('新品牌'))
    fireEvent.click(screen.getAllByRole('button', { name: '保存' }).at(-1)!)
    await waitFor(() => expect(calls.some((c) => c.method === 'PUT' && c.url.includes('/settings/notifier'))).toBe(true))
    const put = calls.find((c) => c.method === 'PUT' && c.url.includes('/settings/notifier'))!
    const image = JSON.parse(put.body ?? '{}').notifier.image
    expect(image.brand).toBe('新品牌')
    expect(image.footer).toBe('新页脚')
    expect(image.qr_url).toBe('https://example.com/me')
    expect(image.channels).toContain('wechat')
  })
})
