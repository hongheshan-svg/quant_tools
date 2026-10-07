import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { createMemoryRouter, RouterProvider } from 'react-router-dom'
import { afterEach, expect, it, vi } from 'vitest'
import { useState } from 'react'
import { SettingsDraftBoundary, SettingsNavigationGuard } from '@/components/SettingsDraftBoundary'
import { hasSettingsDrafts, isSettingsWritePending } from '@/utils/settingsDrafts'
import { http } from '@/api/client'

afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks() })

function Form() {
  const [value, setValue] = useState('0.2')
  return <SettingsDraftBoundary id="weights" paths={['/settings/screening']}><input aria-label="动量" value={value} onChange={(e) => setValue(e.target.value)} /></SettingsDraftBoundary>
}

it('retains draft across hidden tabs, blocks back and unload, allows saved navigation', async () => {
  const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false)
  const router = createMemoryRouter([{ path: '/settings', element: <><SettingsNavigationGuard /><Form /></> }, { path: '/', element: <p>Home</p> }], { initialEntries: ['/', '/settings'], initialIndex: 1 })
  render(<RouterProvider router={router} />)
  fireEvent.change(screen.getByLabelText('动量'), { target: { value: '0.73' } })
  expect(hasSettingsDrafts()).toBe(true)
  const event = new Event('beforeunload', { cancelable: true })
  window.dispatchEvent(event)
  expect(event.defaultPrevented).toBe(true)
  await act(() => router.navigate(-1))
  await waitFor(() => expect(confirm).toHaveBeenCalled())
  expect(screen.getByLabelText('动量')).toHaveValue('0.73')
  vi.stubGlobal('fetch', vi.fn(async () => new Response('{"ok":true}', { headers: { 'Content-Type': 'application/json' } })))
  await act(() => http.put('/settings/screening', {}))
  await waitFor(() => expect(hasSettingsDrafts()).toBe(false))
  await act(() => router.navigate('/'))
  expect(await screen.findByText('Home')).toBeInTheDocument()
})

it('failed save retains draft and import/logout cancellation sends no request', async () => {
  vi.spyOn(window, 'confirm').mockReturnValue(false)
  const fetcher = vi.fn(async () => new Response('{"detail":"失败"}', { status: 500, headers: { 'Content-Type': 'application/json' } }))
  vi.stubGlobal('fetch', fetcher)
  render(<Form />)
  fireEvent.change(screen.getByLabelText('动量'), { target: { value: '0.73' } })
  await expect(http.put('/settings/screening', {})).rejects.toThrow('失败')
  expect(hasSettingsDrafts()).toBe(true)
  await expect(http.post('/settings/import', {})).rejects.toThrow('已取消')
  await expect(http.post('/auth/logout')).rejects.toThrow('已取消')
  expect(fetcher).toHaveBeenCalledTimes(1)
})

it('serializes saves and imports, disables editing and protects unload until response', async () => {
  let finish!: (response: Response) => void
  const fetcher = vi.fn(() => new Promise<Response>((resolve) => { finish = resolve }))
  vi.stubGlobal('fetch', fetcher)
  render(<><SettingsNavigationGuard /><Form /></>)
  fireEvent.change(screen.getByLabelText('动量'), { target: { value: '0.73' } })
  let saving!: Promise<unknown>
  act(() => { saving = http.put('/settings/screening', { weight: 0.73 }) })
  expect(isSettingsWritePending()).toBe(true)
  expect(screen.getByLabelText('动量')).toBeDisabled()
  await expect(http.post('/settings/import', {})).rejects.toThrow('设置操作正在进行')
  await expect(http.del('/settings/templates/custom')).rejects.toThrow('设置操作正在进行')
  expect(fetcher).toHaveBeenCalledTimes(1)
  const event = new Event('beforeunload', { cancelable: true })
  window.dispatchEvent(event)
  expect(event.defaultPrevented).toBe(true)
  await act(async () => { finish(new Response('{"ok":true}', { headers: { 'Content-Type': 'application/json' } })); await saving })
  expect(isSettingsWritePending()).toBe(false)
  expect(screen.getByLabelText('动量')).toBeEnabled()
  expect(hasSettingsDrafts()).toBe(false)
})
