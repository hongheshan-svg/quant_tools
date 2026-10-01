import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AppRoutes } from '@/App'

type Call = { url: string; method: string; body?: string }

const LABELS: Record<string, string> = { conservative: '保守', balanced: '均衡', aggressive: '进取' }
const RESULTS: Record<string, { action: string; action_label: string; confidence: string; guardrails: string[] }> = {
  conservative: { action: 'watch', action_label: '观望', confidence: '中', guardrails: ['保守风格：评分 58 低于 65，降级为观望'] },
  balanced: { action: 'buy', action_label: '买入', confidence: '高', guardrails: [] },
  aggressive: { action: 'buy', action_label: '买入', confidence: '高', guardrails: [] },
}

const diagnosis = {
  id: 11, code: '600519', name: '贵州茅台', trade_date: '2026-09-25', action: 'buy', action_label: '买入', score: 58,
  confidence: '高', one_sentence: '茅台详情结论', decision_profile: 'balanced', guardrails: [],
  battle_plan: {}, position_advice: {}, catalysts: [], risks: [], checklist: [],
}
const detail = { id: 11, code: '600519', name: '贵州茅台', trade_date: '2026-09-25', action: 'buy', score: 58,
  created_at: '2026-09-28 15:40', result: diagnosis }
const historyList = {
  total: 1,
  items: [{ id: 11, code: '600519', name: '贵州茅台', trade_date: '2026-09-25', action: 'buy', score: 58, summary: '摘要', created_at: '2026-09-28 15:40' }],
}

const signal = {
  id: 7, code: '600519', name: '贵州茅台', action: 'buy', score: 82, confidence: '高', trade_date: '2026-09-21',
  status: 'active', status_reason: '', expires_on: '2026-09-28', horizon_days: 5, stop_loss: 9, target_price: 12,
  entry_low: 9.8, entry_high: 10.2, invalidation: '跌破9元', ret_1d: 1.2, ret_3d: null, ret_5d: null,
  max_adverse_pct: -1, max_favorable_pct: 2, feedback: '', feedback_note: '', profile: 'aggressive', profile_label: '进取',
}
const diagSettings = {
  decision_profile: 'balanced', mode: 'standard', shareholders: false, calibration: true, signal_review: true,
  skill_consult: { enabled: true, max_skills: 2 },
}

function stub(calls: Call[], saveStatus = 'created') {
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const method = (init?.method ?? 'GET').toUpperCase()
    const text = typeof init?.body === 'string' ? init.body : undefined
    calls.push({ url, method, body: text })
    let body: unknown = {}
    if (/\/stocks\/diagnoses\/11\/reassess/.test(url)) {
      const req = JSON.parse(text ?? '{}')
      const r = RESULTS[req.profile] ?? RESULTS.balanced
      body = {
        diagnosis_id: 11, code: '600519', name: '贵州茅台', profile: req.profile, profile_label: LABELS[req.profile], ...r,
        original: { profile: 'balanced', action: 'buy', action_label: '买入', confidence: '高' },
        changed: r.action !== 'buy',
        ...(req.persist ? { status: saveStatus, signal: { ...signal, profile: req.profile } } : {}),
      }
    } else if (/\/stocks\/diagnoses\/11/.test(url)) body = detail
    else if (url.includes('/stocks/diagnoses')) body = historyList
    else if (/\/signals\/stats/.test(url)) body = { total: 1, active: 1, hit_rate: 60, avg_ret: 1.5, by_status: {}, by_action: {} }
    else if (/\/signals\/skills|\/chat\/skills\/performance/.test(url)) body = []
    else if (/\/signals/.test(url)) body = { total: 1, items: [signal] }
    else if (url.includes('/settings/diagnosis')) body = method === 'PUT' ? JSON.parse(text ?? '{}') : { diagnosis: diagSettings, ...diagSettings }
    else if (url.includes('/settings/llm')) body = { llm: { primary: {}, backup: {}, vision: {} }, platforms: {} }
    return new Response(JSON.stringify(body), { status: 200, headers: { 'content-type': 'application/json' } })
  }))
}

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

function renderAt(path: string) {
  return render(<MemoryRouter initialEntries={[path]}><AppRoutes authEnabled={false} /></MemoryRouter>)
}

async function openDiagnosis() {
  renderAt('/history')
  fireEvent.click(await screen.findByText('贵州茅台'))
}

describe('诊断视图：决策风格', () => {
  it('标题行显示风格徽标', async () => {
    stub([])
    await openDiagnosis()
    expect(await screen.findByText('茅台详情结论')).toBeInTheDocument()
    expect(await screen.findByText('均衡')).toBeInTheDocument()
  })

  it('按其他风格评估弹窗显示三列结果，变化的标出，并能保存为决策信号', async () => {
    const calls: Call[] = []
    stub(calls)
    await openDiagnosis()
    fireEvent.click(await screen.findByRole('button', { name: /按其他风格评估/ }))

    await waitFor(() => {
      const bodies = calls.filter((c) => c.method === 'POST' && /\/stocks\/diagnoses\/11\/reassess/.test(c.url)).map((c) => JSON.parse(c.body ?? '{}'))
      expect(bodies.map((b) => b.profile).sort()).toEqual(['aggressive', 'balanced', 'conservative'])
      expect(bodies.every((b) => !b.persist)).toBe(true)
    })
    const cons = within(await screen.findByTestId('reassess-conservative'))
    const bal = within(await screen.findByTestId('reassess-balanced'))
    const aggr = within(await screen.findByTestId('reassess-aggressive'))
    expect(await cons.findByText(/保守风格：评分 58 低于 65，降级为观望/)).toBeInTheDocument()
    expect(cons.getByText('观望')).toBeInTheDocument()
    expect(await bal.findByText('买入')).toBeInTheDocument()
    expect(await aggr.findByText('买入')).toBeInTheDocument()
    // 变化的一列有标记，未变化的没有
    expect(cons.getByText(/与原结论不同/)).toBeInTheDocument()
    expect(bal.queryByText(/与原结论不同/)).not.toBeInTheDocument()
    expect(aggr.queryByText(/与原结论不同/)).not.toBeInTheDocument()

    expect(screen.getAllByRole('button', { name: /保存为决策信号/ })).toHaveLength(3)
    fireEvent.click(aggr.getByRole('button', { name: /保存为决策信号/ }))
    await waitFor(() => {
      const save = calls.find((c) => c.method === 'POST' && /reassess/.test(c.url) && JSON.parse(c.body ?? '{}').persist === true)
      expect(save).toBeTruthy()
      expect(JSON.parse(save!.body!).profile).toBe('aggressive')
    })
  })

  it('保存结果为 existing / skipped 时给出对应提示', async () => {
    stub([], 'existing')
    await openDiagnosis()
    fireEvent.click(await screen.findByRole('button', { name: /按其他风格评估/ }))
    const bal = within(await screen.findByTestId('reassess-balanced'))
    fireEvent.click(await bal.findByRole('button', { name: /保存为决策信号/ }))
    expect(await screen.findByText(/已存在/)).toBeInTheDocument()
  })

  it('没有 decision_profile 的旧诊断不显示风格徽标', async () => {
    stub([])
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      const body = /\/stocks\/diagnoses\/11/.test(url) ? { ...detail, result: { ...diagnosis, decision_profile: undefined } } : historyList
      return new Response(JSON.stringify(body), { status: 200, headers: { 'content-type': 'application/json' } })
    }))
    await openDiagnosis()
    expect(await screen.findByText('茅台详情结论')).toBeInTheDocument()
    expect(screen.queryByText('均衡')).not.toBeInTheDocument()
  })
})

describe('决策信号页：风格', () => {
  it('列表有风格列，筛选请求带 profile', async () => {
    const calls: Call[] = []
    stub(calls)
    renderAt('/signals')
    expect(await screen.findByText('贵州茅台')).toBeInTheDocument()
    expect(screen.getAllByText('进取').length).toBeGreaterThan(0)
    const select = screen.getByRole('combobox', { name: /风格/ })
    const labels = Array.from(select.querySelectorAll('option')).map((o) => o.textContent)
    expect(labels).toEqual(expect.arrayContaining(['全部风格', '保守', '均衡', '进取', '旧数据']))
    fireEvent.change(select, { target: { value: 'conservative' } })
    await waitFor(() => expect(calls.some((c) => /\/signals(\?|$)/.test(c.url) && c.url.includes('profile=conservative'))).toBe(true))
    await waitFor(() => expect(calls.some((c) => /\/signals\/stats/.test(c.url) && c.url.includes('profile=conservative'))).toBe(true))
    fireEvent.change(select, { target: { value: 'unknown' } })
    await waitFor(() => expect(calls.some((c) => c.url.includes('profile=unknown'))).toBe(true))
  })
})

describe('设置页：诊断设置', () => {
  it('AI 模型标签下可修改决策风格并保存 PUT', async () => {
    const calls: Call[] = []
    stub(calls)
    renderAt('/settings')
    fireEvent.click(await screen.findByRole('tab', { name: 'AI 模型' }))
    expect(await screen.findByText('诊断设置')).toBeInTheDocument()
    // 等表单初始化完成
    const aggressive = await screen.findByRole('radio', { name: /进取/ })
    await waitFor(() => expect((screen.getByRole('radio', { name: /均衡/ }) as HTMLInputElement).checked).toBe(true))
    fireEvent.click(aggressive)
    expect((screen.getByRole('radio', { name: /进取/ }) as HTMLInputElement).checked).toBe(true)
    const form = screen.getByRole('radio', { name: /进取/ }).closest('.space-y-4') as HTMLElement
    fireEvent.click(within(form).getByRole('button', { name: /保存/ }))
    await waitFor(() => expect(calls.some((c) => c.method === 'PUT' && c.url.includes('/settings/diagnosis'))).toBe(true))
    const put = calls.find((c) => c.method === 'PUT' && c.url.includes('/settings/diagnosis'))!
    const sent = JSON.parse(put.body!)
    const payload = sent.diagnosis ?? sent
    expect(payload.decision_profile).toBe('aggressive')
    expect(payload.skill_consult.max_skills).toBe(2)
  })
})
