import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AppRoutes } from '@/App'

const SCHEDULER = {
  running: true,
  message: '',
  jobs: [
    { id: 'hot_search', name: '热搜数据采集', trigger: '每 30 分钟', next_run_time: '2026-09-30T09:00:00', paused: false },
    { id: 'daily_analysis', name: '每日综合分析', trigger: '工作日 15:30', next_run_time: '2026-09-30T15:30:00', paused: false },
  ],
}

interface Call { method: string; url: string; body: unknown }

function stubApi() {
  const calls: Call[] = []
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const method = (init?.method ?? 'GET').toUpperCase()
    let body: unknown = undefined
    if (typeof init?.body === 'string') { try { body = JSON.parse(init.body) } catch { body = init.body } }
    calls.push({ method, url, body })
    let data: unknown = []
    if (/\/system\/scheduler\/[^/]+\/run/.test(url)) {
      data = { id: 't1', kind: 'scheduler_job', label: '立即运行', status: 'done', progress: null, result: null, error: '' }
    } else if (url.includes('/system/scheduler')) data = SCHEDULER
    else if (url.includes('/settings/import')) data = { sections: ['web'], restored: 1, warnings: [] }
    else if (url.includes('/settings/llm')) data = { llm: { primary: {}, fallback: {} }, platforms: {} }
    return new Response(JSON.stringify(data), { status: 200, headers: { 'content-type': 'application/json' } })
  }))
  return calls
}

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

function openSettings() {
  render(<MemoryRouter initialEntries={['/settings']}><AppRoutes authEnabled={false} /></MemoryRouter>)
}

describe('SettingsPage scheduler tab', () => {
  it('has scheduler and backup tabs', async () => {
    stubApi()
    openSettings()
    expect(await screen.findByRole('tab', { name: '定时任务' })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: '备份与恢复' })).toBeInTheDocument()
  })

  it('renders jobs table and runs a job now via POST', async () => {
    const calls = stubApi()
    openSettings()
    fireEvent.click(await screen.findByRole('tab', { name: '定时任务' }))
    expect(await screen.findByText('热搜数据采集')).toBeInTheDocument()
    expect(screen.getByText('每日综合分析')).toBeInTheDocument()
    expect(screen.getByText(/工作日 15:30/)).toBeInTheDocument()
    const buttons = await screen.findAllByRole('button', { name: /立即运行/ })
    expect(buttons).toHaveLength(2)
    fireEvent.click(buttons[0])
    await waitFor(() =>
      expect(calls.some((c) => c.method === 'POST' && c.url.includes('/system/scheduler/hot_search/run'))).toBe(true))
  })
})

describe('SettingsPage backup tab', () => {
  it('export link toggles include_secrets', async () => {
    stubApi()
    openSettings()
    fireEvent.click(await screen.findByRole('tab', { name: '备份与恢复' }))
    const link = await screen.findByRole('link', { name: /导出/ })
    expect(link.getAttribute('href')).toContain('/settings/export')
    expect(link.getAttribute('href')).toContain('include_secrets=false')
    fireEvent.click(screen.getByLabelText(/包含密钥/))
    await waitFor(() =>
      expect(screen.getByRole('link', { name: /导出/ }).getAttribute('href')).toContain('include_secrets=true'))
  })

  it('imports pasted yaml via POST after confirm', async () => {
    const calls = stubApi()
    vi.stubGlobal('confirm', vi.fn(() => true))
    window.confirm = vi.fn(() => true)
    openSettings()
    fireEvent.click(await screen.findByRole('tab', { name: '备份与恢复' }))
    const box = await screen.findByPlaceholderText(/YAML/i)
    fireEvent.change(box, { target: { value: 'web:\n  port: 9000\n' } })
    fireEvent.click(screen.getByRole('button', { name: /导入/ }))
    await waitFor(() => expect(calls.some((c) => c.method === 'POST' && c.url.includes('/settings/import'))).toBe(true))
    const post = calls.find((c) => c.url.includes('/settings/import'))!
    expect(JSON.stringify(post.body)).toContain('port: 9000')
  })
})
