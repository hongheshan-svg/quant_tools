import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AppRoutes } from '@/App'

const SKILLS = [
  { name: 'general', display_name: '综合', description: '综合', category: 'framework', aliases: [], market_regimes: [], source: 'builtin', instructions: 'x' },
]
const SESSION = { id: 's1', title: '新会话', perspective: '综合', turns: [], updated_at: '' }
const enc = new TextEncoder()
const ev = (o: unknown) => `data: ${JSON.stringify(o)}\n\n`
const DONE = (answer: string, error = '') => ev({ type: 'done', turn: { question: '茅台能买吗', answer, perspective: '综合', tools: [], asked_at: '2026-09-30 10:00', error } })

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } })

function controlledStream() {
  let controller!: ReadableStreamDefaultController<Uint8Array>
  const body = new ReadableStream<Uint8Array>({ start: (c) => { controller = c } })
  return {
    response: () => new Response(body, { status: 200, headers: { 'content-type': 'text/event-stream' } }),
    push: (s: string) => controller.enqueue(enc.encode(s)),
    close: () => controller.close(),
  }
}

type Handler = (url: string, init?: RequestInit) => Response | Promise<Response> | undefined

function stubFetch(extra: Handler) {
  const fn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const custom = await extra(url, init)
    if (custom) return custom
    if (url.includes('/chat/skills')) return json(SKILLS)
    if (/\/chat\/sessions$/.test(url)) return json([])
    if (/\/chat\/sessions\/s1$/.test(url)) return json(SESSION)
    return json([])
  })
  vi.stubGlobal('fetch', fn)
  return fn
}

const called = (fn: ReturnType<typeof vi.fn>, part: string) => fn.mock.calls.some((c) => String(c[0]).includes(part))

async function ask(text = '茅台能买吗') {
  render(<MemoryRouter initialEntries={['/chat/s1']}><AppRoutes authEnabled={false} /></MemoryRouter>)
  const box = await screen.findByPlaceholderText(/输入问题/)
  await userEvent.type(box, text)
  await userEvent.click(screen.getByRole('button', { name: '发送' }))
}

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('ChatPage streaming', () => {
  it('shows deltas progressively, including an event split across chunks', async () => {
    const s = controlledStream()
    const fetchMock = stubFetch((url) => (url.includes('/chat/sessions/s1/ask/stream') ? s.response() : undefined))
    await ask()
    await waitFor(() => expect(called(fetchMock, '/chat/sessions/s1/ask/stream')).toBe(true))

    s.push(ev({ type: 'status', text: '思考中' }))
    s.push(ev({ type: 'delta', text: '观望' }))
    expect(await screen.findByText(/观望/)).toBeInTheDocument()
    expect(screen.queryByText(/量能不足/)).toBeNull()

    // 一个事件被拆在两块之间
    const second = ev({ type: 'delta', text: '，量能不足' })
    s.push(second.slice(0, 15))
    s.push(second.slice(15))
    expect(await screen.findByText(/量能不足/)).toBeInTheDocument()

    s.push(DONE('观望，量能不足'))
    s.close()
    await waitFor(() => expect(screen.getByText(/观望，量能不足/)).toBeInTheDocument())
    await waitFor(() => expect(screen.queryByRole('button', { name: /停止/ })).toBeNull())
    expect(called(fetchMock, '/chat/sessions/s1/ask/stream')).toBe(true)
    expect(fetchMock.mock.calls.some((c) => /\/chat\/sessions\/s1\/ask$/.test(String(c[0])))).toBe(false)
  })

  it('shows a stop button while streaming and calls the cancel endpoint', async () => {
    const s = controlledStream()
    const fetchMock = stubFetch((url) => {
      if (url.includes('/chat/sessions/s1/ask/stream')) return s.response()
      if (url.includes('/chat/sessions/s1/cancel')) return json({ ok: true })
      return undefined
    })
    await ask()
    s.push(ev({ type: 'delta', text: '先说一半' }))
    await screen.findByText(/先说一半/)

    await userEvent.click(await screen.findByRole('button', { name: /停止/ }))
    await waitFor(() => expect(called(fetchMock, '/chat/sessions/s1/cancel')).toBe(true))
    const cancelCall = fetchMock.mock.calls.find((c) => String(c[0]).includes('/cancel'))
    expect((cancelCall?.[1] as RequestInit | undefined)?.method).toBe('POST')

    s.push(DONE('先说一半', '已取消'))
    s.close()
    await waitFor(() => expect(screen.queryByRole('button', { name: /停止/ })).toBeNull())
    expect(screen.getByText(/先说一半/)).toBeInTheDocument()
  })

  it('falls back to the /ask task endpoint when the stream endpoint returns 404', async () => {
    const fetchMock = stubFetch((url) => {
      if (url.includes('/ask/stream')) return json({ detail: 'Not Found' }, 404)
      if (/\/chat\/sessions\/s1\/ask$/.test(url)) {
        return json({ id: 't1', kind: 'chat', label: '问股', status: 'done', progress: null, error: '', created_at: '2026-09-30 10:00:00', started_at: null, finished_at: null,
          result: { question: '茅台能买吗', answer: '回退路径的回答', perspective: '综合', tools: [], asked_at: '2026-09-30 10:00', error: '' } })
      }
      return undefined
    })
    await ask()
    expect(await screen.findByText(/回退路径的回答/, {}, { timeout: 5000 })).toBeInTheDocument()
    expect(called(fetchMock, '/ask/stream')).toBe(true)
    expect(fetchMock.mock.calls.some((c) => /\/chat\/sessions\/s1\/ask$/.test(String(c[0])))).toBe(true)
  })
})
