import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AppRoutes } from '@/App'

const CAPS = [
  {
    dataset: 'realtime',
    label: '实时行情',
    sources: [
      { name: 'tencent', label: '腾讯财经', configured: true, note: '', health: { status: 'ok' } },
      { name: 'pytdx', label: '通达信能力源', configured: true, note: '', health: { status: 'unknown' } },
    ],
  },
  {
    dataset: 'daily_history',
    label: '个股日线',
    sources: [
      { name: 'tushare', label: 'Tushare数据', configured: false, note: '未配置 token', health: { status: 'unknown' } },
    ],
  },
]

function stub() {
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input)
    let data: unknown = []
    if (url.includes('/system/data-center')) data = { matrix: [], snapshots: [] }
    else if (url.includes('/system/capabilities')) data = CAPS
    else if (url.includes('/sources')) data = []
    return new Response(JSON.stringify(data), { status: 200, headers: { 'content-type': 'application/json' } })
  }))
}

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('SourcesPage capabilities tab', () => {
  it('has both tabs and renders capabilities', async () => {
    stub()
    render(<MemoryRouter initialEntries={['/sources']}><AppRoutes authEnabled={false} /></MemoryRouter>)
    expect(await screen.findByRole('tab', { name: '运行状态' })).toBeInTheDocument()
    fireEvent.click(await screen.findByRole('tab', { name: '能力总览' }))
    expect(await screen.findByText('实时行情')).toBeInTheDocument()
    expect(screen.getByText('个股日线')).toBeInTheDocument()
    expect(screen.getByText(/腾讯财经/)).toBeInTheDocument()
    expect(screen.getByText(/通达信能力源/)).toBeInTheDocument()
    expect(screen.getByText(/Tushare数据/)).toBeInTheDocument()
  })

  it('refreshes current batch quality together with provider capabilities', async () => {
    stub()
    render(<MemoryRouter initialEntries={['/sources']}><AppRoutes authEnabled={false} /></MemoryRouter>)
    fireEvent.click(await screen.findByRole('tab', { name: '能力总览' }))
    await screen.findByText('实时行情')
    await waitFor(() => expect(screen.getByRole('button', { name: '刷新' })).toBeEnabled())
    const fetcher = vi.mocked(fetch)
    fetcher.mockClear()
    fireEvent.click(screen.getByRole('button', { name: '刷新' }))
    await waitFor(() => {
      expect(fetcher.mock.calls.some(([input]) => String(input).includes('/system/capabilities'))).toBe(true)
      expect(fetcher.mock.calls.some(([input]) => String(input).includes('/system/data-center'))).toBe(true)
    })
  })
})
