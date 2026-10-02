import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AppRoutes } from '@/App'

function stubFetch(routes: Record<string, unknown>) {
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input)
    const key = Object.keys(routes).find((k) => url.includes(k))
    return new Response(JSON.stringify(key ? routes[key] : []), { status: 200, headers: { 'content-type': 'application/json' } })
  }))
}

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

const base = () => ({
  version: '1.0.0',
  info: vi.fn().mockResolvedValue({ version: '1.0.0', dataDir: '/data', packaged: true }),
  openDataDir: vi.fn().mockResolvedValue(''),
  openLogDir: vi.fn().mockResolvedValue(''),
  retry: vi.fn(),
})

async function openDesktopTab() {
  stubFetch({ '/settings/llm': { llm: { primary: {}, fallback: {} }, platforms: {} } })
  render(<MemoryRouter initialEntries={['/settings']}><AppRoutes authEnabled={false} /></MemoryRouter>)
  fireEvent.click(await screen.findByRole('tab', { name: '桌面端' }))
  await screen.findByText('/data')
}

describe('桌面端自动更新', () => {
  it('点击检查更新显示返回消息', async () => {
    const desktop = {
      ...base(),
      checkForUpdates: vi.fn().mockResolvedValue({ status: 'latest', message: '已是最新版本' }),
      getPrefs: vi.fn().mockResolvedValue({ autoCheckUpdates: true }),
      setPrefs: vi.fn().mockResolvedValue({ autoCheckUpdates: false }),
    }
    vi.stubGlobal('quantDesktop', desktop)
    await openDesktopTab()
    fireEvent.click(screen.getByRole('button', { name: /检查更新/ }))
    await waitFor(() => expect(desktop.checkForUpdates).toHaveBeenCalled())
    expect(await screen.findByText(/已是最新版本/)).toBeInTheDocument()
  })

  it('关闭启动时自动检查更新调用 setPrefs', async () => {
    const desktop = {
      ...base(),
      checkForUpdates: vi.fn().mockResolvedValue({ message: 'x' }),
      getPrefs: vi.fn().mockResolvedValue({ autoCheckUpdates: true }),
      setPrefs: vi.fn().mockResolvedValue({ autoCheckUpdates: false }),
    }
    vi.stubGlobal('quantDesktop', desktop)
    await openDesktopTab()
    const box = await screen.findByLabelText(/启动时自动检查更新/)
    await waitFor(() => expect((box as HTMLInputElement).checked).toBe(true))
    fireEvent.click(box)
    await waitFor(() => expect(desktop.setPrefs).toHaveBeenCalledWith({ autoCheckUpdates: false }))
  })

  it('缺少更新方法时不显示这两项', async () => {
    vi.stubGlobal('quantDesktop', base())
    await openDesktopTab()
    expect(screen.queryByRole('button', { name: /检查更新/ })).not.toBeInTheDocument()
    expect(screen.queryByLabelText(/启动时自动检查更新/)).not.toBeInTheDocument()
  })
})
