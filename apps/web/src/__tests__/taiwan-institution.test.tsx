import { render, screen } from '@testing-library/react'
import { expect, it } from 'vitest'
import { TaiwanInstitutionView } from '@/pages/OverseasPage'

it('preserves signs and zero while marking historical flows and missing official total', () => {
  render(<TaiwanInstitutionView data={{ status: 'stale', as_of: '2026-09-30', source: 'TWSE', unit: 'shares', fetched_at: '2026-09-30T08:00:00Z', foreign_net: -1234, trust_net: 0, dealer_net: 234, total_net: null, limitations: ['different_trading_day'], refresh_status: 'fetch_failed' }} />)
  expect(screen.getByText('-1,234 股')).toBeVisible()
  expect(screen.getByText('0 股')).toBeVisible()
  expect(screen.getByText('+234 股')).toBeVisible()
  expect(screen.getByText('—')).toBeVisible()
  expect(screen.getByText(/历史资料，非当前完整交易日/)).toBeVisible()
  expect(screen.getByText(/保留历史数值及原日期/)).toBeVisible()
})

it('shows missing data without inventing zero flows', () => {
  render(<TaiwanInstitutionView data={{ status: 'fetch_failed', limitations: ['source_unavailable'] }} />)
  expect(screen.getByText(/当前来源不可用/)).toBeVisible()
  expect(screen.queryByText('0 股')).not.toBeInTheDocument()
})
