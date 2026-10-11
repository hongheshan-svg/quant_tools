import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AppRoutes } from '@/App'

// 问股会话状态：服务端确认的主讨论对象、状态版本、策略选择三态、冲突与已接受后的中断
const SKILLS = [
  { name: 'general', display_name: '综合', description: '综合', category: 'framework', aliases: [], market_regimes: [], source: 'builtin', instructions: 'x' },
  { name: 'dragon_head', display_name: '龙头', description: '龙头', category: 'emotion', aliases: [], market_regimes: [], source: 'builtin', instructions: 'x' },
]
const MAOTAI = { code: '600519', name: '贵州茅台', kind: 'stock', source: 'message', confirmed_at: '2026-10-11 10:00:00' }
const TURN = { question: '分析600519', answer: '茅台结论', perspective: '综合', tools: [], asked_at: '2026-10-11 10:00', error: '', status: 'done' }
const SESSION = {
  id: 's1', title: '分析600519', perspective: '综合', turns: [TURN], updated_at: '', source: 'web',
  active_stock_context: MAOTAI, session_state_version: 3, session_state: { selected_skill_ids: ['dragon_head'] },
}
const ev = (o: unknown) => `data: ${JSON.stringify(o)}\n\n`
const sse = (body: string) => new Response(body, { status: 200, headers: { 'content-type': 'text/event-stream' } })
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } })
const DONE = (answer: string) => ev({ type: 'done', turn: { ...TURN, question: '分析风险', answer } })

type Handler = (url: string, init?: RequestInit) => Response | Promise<Response> | undefined

function stubFetch(extra: Handler, session: unknown = SESSION, list: unknown = []) {
  const fn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const custom = await extra(url, init)
    if (custom) return custom
    if (url.includes('/chat/skills')) return json(SKILLS)
    if (/\/chat\/sessions$/.test(url)) return json(list)
    if (/\/chat\/sessions\/s1$/.test(url)) return json(session)
    return json([])
  })
  vi.stubGlobal('fetch', fn)
  return fn
}

const bodies = (fn: ReturnType<typeof vi.fn>, part: string) =>
  fn.mock.calls.filter((c) => String(c[0]).includes(part)).map((c) => JSON.parse(String((c[1] as RequestInit).body)))

async function open() {
  render(<MemoryRouter initialEntries={['/chat/s1']}><AppRoutes authEnabled={false} /></MemoryRouter>)
  return screen.findByPlaceholderText(/输入问题/)
}

async function send(box: HTMLElement, text: string) {
  await userEvent.type(box, text)
  await userEvent.click(screen.getByRole('button', { name: '发送' }))
}

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('ChatPage session state', () => {
  it('shows the server-confirmed subject and sends the known version without unchanged skills', async () => {
    const fetchMock = stubFetch((url) => (url.includes('/ask/stream')
      ? sse(ev({ type: 'accepted', session_id: 's1', message_id: 'm1', active_stock_context: MAOTAI, session_state_version: 4, session_state: { selected_skill_ids: ['dragon_head'] } }) + DONE('风险可控'))
      : undefined))
    const box = await open()
    expect(await screen.findByText('当前讨论：贵州茅台（600519）')).toBeInTheDocument()
    await send(box, '分析风险')
    await screen.findByText(/风险可控/)
    await send(box, '风险呢')
    await waitFor(() => expect(bodies(fetchMock, '/ask/stream')).toHaveLength(2))
    const [first, second] = bodies(fetchMock, '/ask/stream')
    expect(first.state_version).toBe(3)
    expect('skills' in first).toBe(false) // 没改过策略选择：沿用会话已保存的选择
    expect(second.state_version).toBe(4) // accepted 之后用新版本
  })

  it('sends skills only after the user changes the selection', async () => {
    const fetchMock = stubFetch((url) => (url.includes('/ask/stream')
      ? sse(ev({ type: 'accepted', session_id: 's1', message_id: 'm1', active_stock_context: MAOTAI, session_state_version: 4, session_state: { selected_skill_ids: [] } }) + DONE('好'))
      : undefined))
    const box = await open()
    await screen.findByText('当前讨论：贵州茅台（600519）')
    const checkbox = await screen.findByRole('checkbox', { name: '龙头' })
    expect(checkbox).toBeChecked() // 从 session_state 恢复已保存的选择
    await userEvent.click(checkbox)
    await send(box, '分析风险')
    await waitFor(() => expect(bodies(fetchMock, '/ask/stream')).toHaveLength(1))
    expect(bodies(fetchMock, '/ask/stream')[0].skills).toEqual([])
  })

  it('shows not confirmed for an old API without state fields', async () => {
    stubFetch(() => undefined, { id: 's1', title: '旧会话', perspective: '综合', turns: [TURN], updated_at: '' })
    await open()
    expect(await screen.findByText('当前讨论：未确认')).toBeInTheDocument()
  })

  it('keeps the draft and does not resend on a version conflict', async () => {
    const fetchMock = stubFetch((url) => (url.includes('/ask/stream')
      ? json({ detail: '会话已在其他窗口或机器人中更新，请刷新后重试' }, 409)
      : undefined))
    const box = await open()
    await screen.findByText('当前讨论：贵州茅台（600519）')
    await send(box, '分析风险')
    await waitFor(() => expect(screen.getByPlaceholderText(/输入问题/)).toHaveValue('分析风险'))
    expect(fetchMock.mock.calls.some((c) => /\/chat\/sessions\/s1\/ask$/.test(String(c[0])))).toBe(false)
  })

  it('refreshes from the server instead of falling back when the stream breaks after acceptance', async () => {
    let detailCalls = 0
    const accepted = ev({ type: 'accepted', session_id: 's1', message_id: 'm1', active_stock_context: MAOTAI, session_state_version: 4, session_state: { selected_skill_ids: ['dragon_head'] } })
    const fetchMock = stubFetch((url) => {
      if (url.includes('/ask/stream')) {
        const body = new ReadableStream<Uint8Array>({
          start: (controller) => {
            controller.enqueue(new TextEncoder().encode(accepted))
            // 读到 accepted 之后连接才断开（同步 error 会丢弃已排队的数据）
            setTimeout(() => controller.error(new TypeError('network error')), 20)
          },
        })
        return new Response(body, { status: 200, headers: { 'content-type': 'text/event-stream' } })
      }
      if (/\/chat\/sessions\/s1$/.test(url)) {
        detailCalls += 1
        const saved = { ...TURN, question: '分析风险', answer: '服务端保存的回答' }
        return json(detailCalls === 1 ? SESSION : { ...SESSION, turns: [TURN, saved], session_state_version: 4 })
      }
      return undefined
    })
    const box = await open()
    await screen.findByText('当前讨论：贵州茅台（600519）')
    await send(box, '分析风险')
    expect(await screen.findByText('服务端保存的回答')).toBeInTheDocument()
    expect(fetchMock.mock.calls.some((c) => /\/chat\/sessions\/s1\/ask$/.test(String(c[0])))).toBe(false)
  })

  it('labels bot sessions in the list', async () => {
    stubFetch(() => undefined, SESSION, [{ id: 's1', title: '茅台怎么样', perspective: '综合', turns: 1, updated_at: '2026-10-11 10:00', source: 'dingtalk' }])
    await open()
    expect(await screen.findByText('钉钉')).toBeInTheDocument()
  })
})
