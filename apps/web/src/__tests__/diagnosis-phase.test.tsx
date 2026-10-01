import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { Diagnosis } from '@/api/types'
import { DiagnosisView } from '@/components/DiagnosisView'

const BASE = {
  code: '002594', name: '比亚迪', trade_date: '2026-09-25', created_at: '2026-09-25 16:00', score: 82, action: 'buy',
  action_label: '买入', confidence: '高', one_sentence: '主线龙头', trend_prediction: '', position_advice: {},
  battle_plan: {}, catalysts: [], risks: [], checklist: [], analysis: '', guardrails: [], theme_role: {}, market_regime: '均衡',
  data_quality: { score: 90, missing: [] }, fund_flow: '', chips: {}, earnings: '', valuation: '', agents: [], disagreement: '', calibration: '',
} as unknown as Diagnosis

const PHASE = {
  phase_decision: {
    phase: 'premarket', phase_label: '盘前', trading_window: '开盘后30分钟观察承接', immediate_action: '等待开盘确认',
    watch_conditions: ['放量突破前高', '不破5日线'], next_check_time: '下一交易日 9:25', data_limitations: ['行情停留在 2026-09-25'],
  },
  signal_attribution: { technical: 40, news: 30, fundamentals: 20, market: 10, strongest_bullish: '龙头二板封单强', strongest_bearish: '高位分歧风险' },
  market_phase: { phase: 'premarket', label: '盘前', now: '2026-09-28 08:30', effective_daily_bar_date: '2026-09-25' },
}

describe('DiagnosisView 阶段决策与信号归因', () => {
  it('渲染阶段决策卡片', () => {
    render(<DiagnosisView d={{ ...BASE, ...PHASE } as Diagnosis} />)
    expect(screen.getByText('阶段决策')).toBeInTheDocument()
    expect(screen.getByText('盘前')).toBeInTheDocument()
    expect(screen.getByText(/开盘后30分钟观察承接/)).toBeInTheDocument()
    expect(screen.getByText(/等待开盘确认/)).toBeInTheDocument()
    expect(screen.getByText(/放量突破前高/)).toBeInTheDocument()
    expect(screen.getByText(/不破5日线/)).toBeInTheDocument()
    expect(screen.getByText(/下一交易日 9:25/)).toBeInTheDocument()
    expect(screen.getByText(/行情停留在 2026-09-25/)).toBeInTheDocument()
  })

  it('渲染信号归因四项与最强信号', () => {
    render(<DiagnosisView d={{ ...BASE, ...PHASE } as Diagnosis} />)
    expect(screen.getByText('信号归因')).toBeInTheDocument()
    for (const p of ['40%', '30%', '20%', '10%']) expect(screen.getByText(p)).toBeInTheDocument()
    const bull = screen.getByText(/龙头二板封单强/)
    const bear = screen.getByText(/高位分歧风险/)
    expect(bull.closest('.text-up') ?? bull).toBeTruthy()
    expect(bear.closest('.text-down') ?? bear).toBeTruthy()
  })

  it('字段缺失时不渲染两张卡片', () => {
    render(<DiagnosisView d={BASE} />)
    expect(screen.queryByText('阶段决策')).not.toBeInTheDocument()
    expect(screen.queryByText('信号归因')).not.toBeInTheDocument()
  })

  it('空对象时不渲染', () => {
    render(<DiagnosisView d={{ ...BASE, phase_decision: {}, signal_attribution: {}, market_phase: {} } as unknown as Diagnosis} />)
    expect(screen.queryByText('阶段决策')).not.toBeInTheDocument()
    expect(screen.queryByText('信号归因')).not.toBeInTheDocument()
  })

  it('只有归因时只渲染归因', () => {
    render(<DiagnosisView d={{ ...BASE, signal_attribution: PHASE.signal_attribution } as Diagnosis} />)
    expect(screen.queryByText('阶段决策')).not.toBeInTheDocument()
    expect(screen.getByText('信号归因')).toBeInTheDocument()
  })
})
