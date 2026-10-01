import { render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { Diagnosis } from '@/api/types'
import { DiagnosisView } from '@/components/DiagnosisView'

const DIAG = {
  code: '002594', name: '比亚迪', trade_date: '2026-09-25', created_at: '2026-09-25 16:00', score: 82, action: 'buy',
  action_label: '买入', confidence: '高', one_sentence: '主线龙头', trend_prediction: '', position_advice: {},
  battle_plan: {}, catalysts: [], risks: [], checklist: [], analysis: '', guardrails: [], theme_role: {}, market_regime: '均衡',
  data_quality: { score: 90, missing: [] }, fund_flow: '', chips: {}, earnings: '', valuation: '', agents: [], disagreement: '', calibration: '',
} as unknown as Diagnosis

const TEXT = '股东户数 45.8 万（2026-07-31，较上期 -1.79%，筹码非常分散）；机构 277 家'

afterEach(() => {
  vi.restoreAllMocks()
})

describe('DiagnosisView 股东行', () => {
  it('有股东摘要时显示「股东：」行', () => {
    render(<DiagnosisView d={{ ...DIAG, shareholders: TEXT }} />)
    expect(screen.getByText('股东：')).toBeInTheDocument()
    expect(screen.getByText(/股东户数 45.8 万/)).toBeInTheDocument()
  })

  it('没有股东数据时不显示该行', () => {
    render(<DiagnosisView d={{ ...DIAG, shareholders: '' }} />)
    expect(screen.queryByText('股东：')).not.toBeInTheDocument()
    render(<DiagnosisView d={DIAG} />)
    expect(screen.queryByText('股东：')).not.toBeInTheDocument()
  })
})
