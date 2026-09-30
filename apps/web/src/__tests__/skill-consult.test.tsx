import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AppRoutes } from '@/App'
import type { Diagnosis } from '@/api/types'
import { DiagnosisView } from '@/components/DiagnosisView'

const DIAG = {
  code: '002594', name: '比亚迪', trade_date: '2026-09-25', created_at: '2026-09-25 16:00', score: 82, action: 'buy',
  action_label: '买入', confidence: '高', one_sentence: '主线龙头', trend_prediction: '', position_advice: {},
  battle_plan: {}, catalysts: [], risks: [], checklist: [], analysis: '', guardrails: [], theme_role: {}, market_regime: '均衡',
  data_quality: { score: 90, missing: [] }, fund_flow: '', chips: {}, earnings: '', valuation: '', agents: [], disagreement: '', calibration: '',
} as unknown as Diagnosis

const OPINIONS = [
  { skill: 'limit_up_relay', display_name: '打板接力', stance: '看多', score: 80, confidence: '高', reason: '封板早ABC', weight: 1.1 },
  { skill: 'dragon_head', display_name: '龙头战法', stance: '看空', score: 30, confidence: '中', reason: '高位分歧DEF', weight: 0.9 },
]

const PERF = [
  { skill: 'limit_up_relay', display_name: '打板接力', samples: 25, hits: 20, hit_rate: 80, avg_ret: 3.2, weight: 1.2 },
  { skill: 'dragon_head', display_name: '龙头战法', samples: 10, hits: 4, hit_rate: 40, avg_ret: -1.1, weight: 1.0 },
]

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('DiagnosisView 策略会诊', () => {
  it('显示策略会诊区块、各策略观点和共识', () => {
    render(<DiagnosisView d={{ ...DIAG, skill_opinions: OPINIONS, skill_consensus: { stance: '中性', score: 55, agreement: '分歧' } }} />)
    expect(screen.getByText(/策略会诊/)).toBeInTheDocument()
    expect(screen.getByText(/打板接力/)).toBeInTheDocument()
    expect(screen.getByText(/龙头战法/)).toBeInTheDocument()
    expect(screen.getByText(/封板早ABC/)).toBeInTheDocument()
    expect(screen.getByText(/高位分歧DEF/)).toBeInTheDocument()
    expect(screen.getByText(/共识/)).toBeInTheDocument()
    expect(screen.getByText(/（分歧）/)).toBeInTheDocument()
  })

  it('没有策略观点时不显示区块', () => {
    render(<DiagnosisView d={{ ...DIAG, skill_opinions: [] }} />)
    expect(screen.queryByText(/策略会诊/)).not.toBeInTheDocument()
    render(<DiagnosisView d={DIAG} />)
    expect(screen.queryByText(/策略会诊/)).not.toBeInTheDocument()
  })
})

describe('决策信号页 策略表现', () => {
  it('切到策略表现标签渲染表格', async () => {
    const calls: string[] = []
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      calls.push(url)
      let body: unknown = {}
      if (/\/chat\/skills\/performance/.test(url)) body = PERF
      else if (/\/signals\/stats/.test(url)) body = { total: 0, active: 0, hit_rate: 0, avg_ret: 0, by_status: {}, by_action: {} }
      else if (/\/signals/.test(url)) body = { total: 0, items: [] }
      return new Response(JSON.stringify(body), { status: 200, headers: { 'content-type': 'application/json' } })
    }))
    render(<MemoryRouter initialEntries={['/signals']}><AppRoutes authEnabled={false} /></MemoryRouter>)
    const tab = await screen.findByRole('tab', { name: /策略表现/ })
    await userEvent.click(tab)
    await waitFor(() => expect(calls.some((u) => u.includes('/chat/skills/performance'))).toBe(true))
    const cell = await screen.findByText('打板接力')
    const row = cell.closest('tr') as HTMLElement
    expect(row).toBeTruthy()
    expect(within(row).getByText('25')).toBeInTheDocument()
    expect(within(row).getByText('80.0%')).toBeInTheDocument()
    expect(within(row).getByText('1.20')).toBeInTheDocument()
    const row2 = screen.getByText('龙头战法').closest('tr') as HTMLElement
    expect(within(row2).getByText('40.0%')).toBeInTheDocument()
  })
})
