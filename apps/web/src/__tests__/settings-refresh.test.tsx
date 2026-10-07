import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { LLMSettingsForm } from '@/pages/SettingsPage'
import { SettingsDraftBoundary } from '@/components/SettingsDraftBoundary'
import { http } from '@/api/client'
import { isSettingsWritePending } from '@/utils/settingsDrafts'

afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks() })

it('keeps editing and imports locked through the post-save refresh', async () => {
  let finish!: (response: Response) => void
  let saved = false
  const body = { llm: { primary: { model: 'old' }, backup: {}, vision: {} }, platforms: { custom: { name: '自定义', models: [] } } }
  const json = (value: unknown) => new Response(JSON.stringify(value), { headers: { 'Content-Type': 'application/json' } })
  vi.stubGlobal('fetch', vi.fn((_url, init) => {
    if (init?.method === 'PUT') { saved = true; return Promise.resolve(json({ ok: true })) }
    if (saved) return new Promise<Response>((resolve) => { finish = resolve })
    return Promise.resolve(json(body))
  }))
  const { container } = render(<SettingsDraftBoundary id="llm" paths={['/settings/llm']}><LLMSettingsForm /></SettingsDraftBoundary>)
  await waitFor(() => expect(container.querySelector('input[list="models-主力模型"]')).toBeEnabled())
  const field = container.querySelector('input[list="models-主力模型"]')!
  fireEvent.change(field, { target: { value: 'saved-model' } })
  fireEvent.click(screen.getByRole('button', { name: '保存' }))
  await waitFor(() => expect(finish).toBeDefined())
  expect(field).toBeDisabled()
  expect(isSettingsWritePending()).toBe(true)
  await expect(http.post('/settings/import', {})).rejects.toThrow('设置操作正在进行')
  await act(async () => finish(json({ ...body, llm: { ...body.llm, primary: { model: 'saved-model' } } })))
  await waitFor(() => expect(field).toBeEnabled())
  fireEvent.change(field, { target: { value: 'next-draft' } })
  expect(field).toHaveValue('next-draft')
  expect(isSettingsWritePending()).toBe(false)
})
