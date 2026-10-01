import { fireEvent, render, screen, waitFor } from '@testing-library/react'
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

const notifierSettings = (groups: unknown[]) => ({
  notifier: {
    routes: {},
    telegram: { enabled: false, bot_token: '', chat_id: '-100' },
    email: { enabled: true, smtp_host: 'smtp.x.com', smtp_port: 465, password: '******', to: ['a@x.com'], groups },
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
})

const wlSettings = { daily_report: true, max_stocks: 50, workers: 3, single_notify: false, timeout_minutes: 0 }

function stub(calls: Call[], groups: unknown[] = []) {
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const method = (init?.method ?? 'GET').toUpperCase()
    calls.push({ url, method, body: typeof init?.body === 'string' ? init.body : undefined })
    if (url.includes('/settings/llm')) return json({ llm: { primary: {}, fallback: {} }, platforms: {} })
    if (url.includes('/settings/notifier')) return json(method === 'PUT' ? { ok: true } : notifierSettings(groups))
    if (url.includes('/settings/watchlist')) return json(method === 'PUT' ? JSON.parse(String(init?.body)) : wlSettings)
    if (url.includes('/watchlist/report')) return json(null)
    return json([])
  }))
}

const put = (calls: Call[], part: string) => calls.find((c) => c.method === 'PUT' && c.url.includes(part))

describe('邮件分组编辑', () => {
  async function openPush(calls: Call[], groups: unknown[]) {
    stub(calls, groups)
    render(<MemoryRouter initialEntries={['/settings']}><AppRoutes authEnabled={false} /></MemoryRouter>)
    fireEvent.click(screen.getByRole('tab', { name: '推送' }))
    // 等表单用接口数据完成初始化，否则输入会被随后的同步覆盖
    await waitFor(() => expect(screen.getByLabelText(/Chat ID/)).toHaveValue('-100'))
  }

  const save = async (calls: Call[]) => {
    fireEvent.click(screen.getAllByRole('button', { name: '保存' }).at(-1)!)
    await waitFor(() => expect(put(calls, '/settings/notifier')).toBeTruthy())
    return JSON.parse(put(calls, '/settings/notifier')!.body ?? '{}').notifier.email.groups
  }

  it('shows existing groups and keeps them on save', async () => {
    const calls: Call[] = []
    await openPush(calls, [{ name: '家人', stocks: ['600519', 'sh000300'], to: ['fam@x.com'] }])
    expect(await screen.findByDisplayValue('家人')).toBeInTheDocument()
    expect(screen.getByDisplayValue('600519, sh000300')).toBeInTheDocument()
    expect(screen.getByDisplayValue('fam@x.com')).toBeInTheDocument()
    expect(await save(calls)).toEqual([{ name: '家人', stocks: ['600519', 'sh000300'], to: ['fam@x.com'] }])
  })

  it('adds a group row, edits it and saves with groups in the PUT body', async () => {
    const calls: Call[] = []
    await openPush(calls, [])
    fireEvent.click(screen.getByRole('button', { name: '添加分组' }))
    fireEvent.change(await screen.findByLabelText(/组名 1/), { target: { value: '朋友' } })
    fireEvent.change(screen.getByLabelText(/股票代码（逗号分隔） 1/), { target: { value: '600519，510300; sh000300' } })
    fireEvent.change(screen.getByLabelText(/收件人（逗号分隔） 1/), { target: { value: 'a@x.com, b@x.com' } })
    expect(await save(calls)).toEqual([{ name: '朋友', stocks: ['600519', '510300', 'sh000300'], to: ['a@x.com', 'b@x.com'] }])
  })

  it('deletes a row', async () => {
    const calls: Call[] = []
    await openPush(calls, [
      { name: '家人', stocks: ['600519'], to: ['f@x.com'] },
      { name: '朋友', stocks: ['601919'], to: ['p@x.com'] },
    ])
    await screen.findByDisplayValue('家人')
    fireEvent.click(screen.getByRole('button', { name: /删除分组 1/ }))
    await waitFor(() => expect(screen.queryByDisplayValue('家人')).toBeNull())
    expect(screen.getByDisplayValue('朋友')).toBeInTheDocument()
    expect(await save(calls)).toEqual([{ name: '朋友', stocks: ['601919'], to: ['p@x.com'] }])
  })

  it('can delete all rows and save an empty list', async () => {
    const calls: Call[] = []
    await openPush(calls, [{ name: '家人', stocks: ['600519'], to: ['f@x.com'] }])
    await screen.findByDisplayValue('家人')
    fireEvent.click(screen.getByRole('button', { name: /删除分组 1/ }))
    expect(await save(calls)).toEqual([])
  })
})

describe('自选股仪表盘设置', () => {
  async function openDialog(calls: Call[]) {
    stub(calls)
    render(<MemoryRouter initialEntries={['/watchlist']}><AppRoutes authEnabled={false} /></MemoryRouter>)
    fireEvent.click(await screen.findByRole('button', { name: '仪表盘设置' }))
    // 等设置加载完成
    return screen.findByLabelText(/每只诊断完成后立即推送一条/)
  }

  it('loads current values and saves edits via PUT /settings/watchlist', async () => {
    const calls: Call[] = []
    const single = await openDialog(calls)
    expect(single).not.toBeChecked()
    const timeout = screen.getByLabelText(/总时长上限/)
    expect(timeout).toHaveValue(0)
    expect(screen.getByLabelText(/自选股上限/)).toHaveValue(50)
    expect(screen.getByLabelText(/同时诊断的股票数/)).toHaveValue(3)
    fireEvent.click(single)
    fireEvent.change(timeout, { target: { value: '20' } })
    fireEvent.change(screen.getByLabelText(/同时诊断的股票数/), { target: { value: '5' } })
    fireEvent.click(screen.getByLabelText(/收盘后自动生成并推送仪表盘/))
    fireEvent.click(screen.getByRole('button', { name: '保存' }))
    await waitFor(() => expect(put(calls, '/settings/watchlist')).toBeTruthy())
    expect(JSON.parse(put(calls, '/settings/watchlist')!.body ?? '{}')).toEqual({
      daily_report: false, max_stocks: 50, workers: 5, single_notify: true, timeout_minutes: 20,
    })
  })

  it('does not call PUT when closed without saving', async () => {
    const calls: Call[] = []
    await openDialog(calls)
    expect(put(calls, '/settings/watchlist')).toBeUndefined()
  })
})
