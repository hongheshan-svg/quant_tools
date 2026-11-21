import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, expect, it, vi } from 'vitest'
import { IntegrationsPage } from '@/pages/IntegrationsPage'

afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks() })

it('shows OAuth capability and rechecks it after saving integration settings', async () => {
  let checks = 0
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    let value: unknown = []
    if (url.includes('/settings/integrations')) value = { settings: {}, groups: [] }
    if (url.endsWith('/integrations/longbridge/status')) {
      checks++
      value = { status: 'oauth_unsupported', oauth_supported: false, legacy_configured: true, message: '当前 SDK 不支持 OAuth；可使用完整旧版凭据' }
    }
    return new Response(JSON.stringify(value), { headers: { 'Content-Type': 'application/json' } })
  }))
  render(<MemoryRouter><IntegrationsPage /></MemoryRouter>)
  expect(await screen.findByText('当前 SDK 不支持 OAuth；可使用完整旧版凭据')).toBeVisible()
  fireEvent.click(await screen.findByRole('button', { name: '保存接入配置' }))
  await waitFor(() => expect(checks).toBe(2))
})

it('shows local runtime status without the removed App Server chat', async () => {
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    let value: unknown = []
    if (url.includes('/settings/integrations')) value = { settings: {}, groups: [] }
    if (url.endsWith('/integrations/runtime')) value = [{ provider: 'codex_cli', available: true, capabilities: ['generation'], reason: '已安装，登录状态需调用验证' }]
    return new Response(JSON.stringify(value), { headers: { 'Content-Type': 'application/json' } })
  }))
  render(<MemoryRouter><IntegrationsPage /></MemoryRouter>)
  expect(await screen.findByText('已安装，登录状态需调用验证')).toBeVisible()
  expect(screen.queryByText('Codex App Server 只读问股')).toBeNull()
  expect(screen.queryByLabelText('本地模型问题')).toBeNull()
})
