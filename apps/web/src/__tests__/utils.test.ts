import { describe, expect, it } from 'vitest'
import { movingAverage, toCandles } from '@/components/CandlestickChart'
import type { DailyBar } from '@/api/types'
import { fmtAmount, fmtPct, trendClass, verdictClass } from '@/utils/format'
import { importSummary } from '@/pages/WatchlistPage'

const bar = (trade_date: string, close: number, extra: Partial<DailyBar> = {}): DailyBar => ({
  code: '600519', name: '贵州茅台', trade_date, open: close, high: close + 1, low: close - 1, close,
  volume: null, amount: 1e8, change_pct: null, turnover: null, total_mv: null, circ_mv: null, ...extra,
})

describe('format', () => {
  it('formats percentages and amounts', () => {
    expect(fmtPct(1.234)).toBe('+1.23%')
    expect(fmtPct(-2)).toBe('-2.00%')
    expect(fmtPct(null)).toBe('--')
    expect(fmtPct(12.3, 1, false)).toBe('12.3%')
    expect(fmtAmount(3.21e8)).toBe('3.21亿')
    expect(fmtAmount(52000)).toBe('5.2万')
  })

  it('uses red for up and green for down', () => {
    expect(trendClass(1)).toBe('text-up')
    expect(trendClass(-1)).toBe('text-down')
    expect(trendClass(0)).toBe('text-muted')
    expect(verdictClass('买入')).toBe('text-up')
    expect(verdictClass('回避')).toBe('text-down')
    expect(verdictClass('观望')).toBe('text-warn')
  })

  it('summarizes watchlist imports', () => {
    expect(importSummary({ added: ['a', 'b'], existing: ['c'], unknown: ['x'], over_limit: [] })).toBe('新增 2 只，已存在 1 只，无法识别：x')
  })
})

describe('candles', () => {
  it('aggregates weeks and months', () => {
    const bars = [bar('2026-09-21', 10), bar('2026-09-22', 12), bar('2026-09-28', 11), bar('2026-10-08', 13)]
    const weeks = toCandles(bars, 'week')
    expect(weeks.map((c) => [c.date, c.open, c.close, c.high, c.low])).toEqual([
      ['2026-09-22', 10, 12, 13, 9],
      ['2026-09-28', 11, 11, 12, 10],
      ['2026-10-08', 13, 13, 14, 12],
    ])
    expect(weeks[0].amount).toBe(2e8)
    expect(weeks[1].change_pct).toBeCloseTo(-8.333, 2)
    expect(toCandles(bars, 'month').map((c) => c.date)).toEqual(['2026-09-28', '2026-10-08'])
  })

  it('computes moving averages', () => {
    expect(movingAverage([1, 2, 3, 4], 3)).toEqual([null, null, 2, 3])
  })
})
