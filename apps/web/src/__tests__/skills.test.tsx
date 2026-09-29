import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AppRoutes } from '@/App'

const SKILLS = [
  { name: 'general', display_name: '综合', description: '综合判断说明ZH', category: 'framework', aliases: [], market_regimes: [], source: 'builtin', instructions: 'x' },
  { name: 'dragon_head', display_name: '龙回头', description: '龙回头说明ABC', category: 'reversal', aliases: ['lht'], market_regimes: ['均衡'], source: 'builtin', instructions: 'y' },
  { name: 'ma_cross', display_name: '均线金叉', description: '金叉说明DEF', category: 'trend', aliases: [], market_regimes: [], source: 'builtin', instructions: 'z' },
]

function stubFetch() {
  const fn = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input)
    let body: unknown = []
    if (url.includes('/chat/skills')) body = SKILLS
    else if (url.includes('/chat/perspectives')) body = Object.fromEntries(SKILLS.map((s) => [s.display_name, s.description]))
    return new Response(JSON.stringify(body), { status: 200, headers: { 'content-type': 'application/json' } })
  })
  vi.stubGlobal('fetch', fn)
  return fn
}

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('ChatPage strategy skills', () => {
  it('renders skills from /chat/skills grouped by category and shows description', async () => {
    const fetchMock = stubFetch()
    render(<MemoryRouter initialEntries={['/chat']}><AppRoutes authEnabled={false} /></MemoryRouter>)
    await waitFor(() => expect(fetchMock.mock.calls.some((c) => String(c[0]).includes('/chat/skills'))).toBe(true))

    const select = (await screen.findAllByRole('combobox')).find((el) => within(el).queryByText('龙回头')) as HTMLElement
    expect(select).toBeTruthy()
    const groups = select.querySelectorAll('optgroup')
    expect(groups.length).toBeGreaterThanOrEqual(3)
    const dragon = within(select).getByText('龙回头')
    expect(dragon.closest('optgroup')).not.toBeNull()
    expect(within(select).getByText('均线金叉').closest('optgroup')).not.toBe(dragon.closest('optgroup'))

    await userEvent.selectOptions(select, '龙回头')
    expect(await screen.findByText(/龙回头说明ABC/)).toBeInTheDocument()
  })
})
