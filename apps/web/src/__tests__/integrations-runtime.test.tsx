import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, expect, it, vi } from 'vitest'
import { IntegrationsPage } from '@/pages/IntegrationsPage'
import { toast } from '@/stores/toast'

afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks() })

it('handles a cancelled runtime task and keeps the page usable', async () => {
  const error = vi.spyOn(toast, 'error')
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    let value: unknown = []
    if (url.includes('/settings/integrations')) value = { settings: {}, groups: [] }
    if (url.endsWith('/integrations/runtime/chat') && init?.method === 'POST') value = {
      id: 'cancelled-task', session_id: 'a'.repeat(32), status: 'error', kind: 'codex_runtime', error: '本地模型会话已取消',
    }
    return new Response(JSON.stringify(value), { headers: { 'Content-Type': 'application/json' } })
  }))
  render(<MemoryRouter><IntegrationsPage /></MemoryRouter>)
  fireEvent.change(await screen.findByLabelText('本地模型问题'), { target: { value: '检查研究' } })
  fireEvent.click(screen.getByRole('button', { name: /^发送$/ }))
  await waitFor(() => expect(error).toHaveBeenCalledWith('本地模型会话已取消'))
  await waitFor(() => expect(screen.getByRole('button', { name: /^发送$/ })).toBeEnabled())
  expect(screen.getByRole('button', { name: /^新会话$/ })).toBeEnabled()
})
