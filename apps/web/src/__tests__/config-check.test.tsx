import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AppRoutes } from '@/App'

type Issue = { level: 'error' | 'warning'; path: string; message: string }

function result(issues: Issue[]) {
  const errors = issues.filter((i) => i.level === 'error').length
  return { ok: errors === 0, errors, warnings: issues.length - errors, issues }
}

function stub(calls: string[], payload: unknown, status = 200) {
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input)
    calls.push(url)
    let body: unknown = []
    if (url.includes('/system/config-check')) body = payload
    else if (url.includes('/settings/llm')) body = { llm: { primary: {}, backup: {}, vision: {} }, platforms: {} }
    else if (url.includes('/settings/report')) body = { language: 'zh' }
    return new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } })
  }))
}

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

async function open() {
  render(<MemoryRouter initialEntries={['/settings']}><AppRoutes authEnabled={false} /></MemoryRouter>)
  fireEvent.click(await screen.findByRole('tab', { name: '备份与恢复' }))
  return await screen.findByRole('button', { name: /检查配置/ })
}

describe('Settings 检查配置', () => {
  it('shows the card under the 备份与恢复 tab and does not call the API until clicked', async () => {
    const calls: string[] = []
    stub(calls, result([]))
    await open()
    expect(calls.some((c) => c.includes('/system/config-check'))).toBe(false)
  })

  it('lists errors and warnings with path and message after clicking', async () => {
    const calls: string[] = []
    stub(calls, result([
      { level: 'error', path: 'llm.primary.api_key', message: '主模型未配置 API Key，AI 功能都不可用' },
      { level: 'warning', path: 'web.prot', message: '未知配置项，可能拼写错误' },
      { level: 'warning', path: 'trading.auto_confirm', message: '自动确认只对模拟盘生效' },
    ]))
    fireEvent.click(await open())
    expect(await screen.findByText('llm.primary.api_key')).toBeInTheDocument()
    expect(screen.getByText(/主模型未配置 API Key/)).toBeInTheDocument()
    expect(screen.getByText('web.prot')).toBeInTheDocument()
    expect(screen.getByText(/未知配置项/)).toBeInTheDocument()
    expect(screen.getByText('trading.auto_confirm')).toBeInTheDocument()
    expect(screen.queryByText('配置检查通过')).not.toBeInTheDocument()
    expect(calls.filter((c) => c.includes('/system/config-check')).length).toBe(1)
  })

  it('shows the error and warning counts', async () => {
    stub([], result([
      { level: 'error', path: 'web.port', message: '端口超出范围' },
      { level: 'error', path: 'diagnosis.mode', message: '取值无效' },
      { level: 'warning', path: 'zzz', message: '未知配置项，可能拼写错误' },
    ]))
    fireEvent.click(await open())
    await screen.findByText('web.port')
    expect(screen.getAllByText(/2/).length).toBeGreaterThan(0)
    expect(screen.getAllByText(/1/).length).toBeGreaterThan(0)
  })

  it('shows the passed message when there are no issues', async () => {
    stub([], result([]))
    fireEvent.click(await open())
    expect(await screen.findByText('配置检查通过')).toBeInTheDocument()
  })

  it('can re-run the check', async () => {
    const calls: string[] = []
    stub(calls, result([]))
    const btn = await open()
    fireEvent.click(btn)
    await screen.findByText('配置检查通过')
    fireEvent.click(screen.getByRole('button', { name: /检查配置/ }))
    await waitFor(() => expect(calls.filter((c) => c.includes('/system/config-check')).length).toBe(2))
  })
})
