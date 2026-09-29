import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AppRoutes } from '@/App'

type Call = { url: string; method: string; body?: string }

const llmSettings = {
  llm: {
    primary: { provider: 'deepseek', model: 'deepseek-chat', api_key: ['******1111', '******2222'], base_url: 'https://api.deepseek.com' },
    backup: {},
    vision: {},
  },
  platforms: { deepseek: { name: 'DeepSeek', base_url: 'https://api.deepseek.com', default_model: 'deepseek-chat', models: ['deepseek-chat'] } },
}

function stub(calls: Call[]) {
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const method = (init?.method ?? 'GET').toUpperCase()
    calls.push({ url, method, body: typeof init?.body === 'string' ? init.body : undefined })
    let body: unknown = []
    if (url.includes('/settings/llm/models')) body = { models: ['fetched-model-a', 'fetched-model-b'] }
    else if (url.includes('/settings/llm')) body = method === 'PUT' ? { ok: true } : llmSettings
    return new Response(JSON.stringify(body), { status: 200, headers: { 'content-type': 'application/json' } })
  }))
}

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

async function open() {
  render(<MemoryRouter initialEntries={['/settings']}><AppRoutes authEnabled={false} /></MemoryRouter>)
  fireEvent.click(await screen.findByRole('tab', { name: 'AI 模型' }))
}

describe('SettingsPage AI 模型 multi key', () => {
  it('shows API Key as a multi-line textarea with one key per line', async () => {
    stub([])
    await open()
    const boxes = await screen.findAllByLabelText(/API Key/)
    const box = boxes[0] as HTMLTextAreaElement
    expect(box.tagName).toBe('TEXTAREA')
    await waitFor(() => expect((screen.getAllByLabelText(/API Key/)[0] as HTMLTextAreaElement).value).toBe('******1111\n******2222'))
  })

  it('saves two lines as an array in the PUT body', async () => {
    const calls: Call[] = []
    stub(calls)
    await open()
    const box = (await screen.findAllByLabelText(/API Key/))[0]
    fireEvent.change(box, { target: { value: 'sk-one\nsk-two' } })
    fireEvent.click(screen.getByRole('button', { name: '保存' }))
    await waitFor(() => expect(calls.some((c) => c.method === 'PUT' && c.url.includes('/settings/llm'))).toBe(true))
    const put = calls.find((c) => c.method === 'PUT' && c.url.includes('/settings/llm'))!
    expect(JSON.parse(put.body!).llm.primary.api_key).toEqual(['sk-one', 'sk-two'])
  })

  it('fetches model list and offers them as datalist options', async () => {
    const calls: Call[] = []
    stub(calls)
    await open()
    await screen.findAllByLabelText(/API Key/)
    fireEvent.click(screen.getAllByRole('button', { name: /获取模型列表/ })[0])
    await waitFor(() => expect(calls.some((c) => c.method === 'POST' && c.url.includes('/settings/llm/models'))).toBe(true))
    const post = calls.find((c) => c.url.includes('/settings/llm/models'))!
    expect(JSON.parse(post.body!).role).toBe('primary')
    await waitFor(() => {
      const values = Array.from(document.querySelectorAll('datalist option')).map((o) => o.getAttribute('value'))
      expect(values).toContain('fetched-model-a')
      expect(values).toContain('fetched-model-b')
    })
  })
})
