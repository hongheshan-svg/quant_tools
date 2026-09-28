// K 线图（SVG）：蜡烛、MA5/10/20、成交额柱，悬停显示当日数据；红涨绿跌
import { useEffect, useMemo, useRef, useState } from 'react'
import type { DailyBar } from '@/api/types'
import { fmtAmount, fmtNum, fmtPct } from '@/utils/format'

export type Period = 'day' | 'week' | 'month'

export interface Candle {
  date: string
  open: number
  high: number
  low: number
  close: number
  amount: number
  change_pct: number | null
}

const MA = [
  { n: 5, color: '#f5c542' },
  { n: 10, color: '#c792ea' },
  { n: 20, color: '#56b6f7' },
]

/** 日线转成蜡烛；周线/月线按自然周、自然月聚合 */
export function toCandles(bars: DailyBar[], period: Period = 'day'): Candle[] {
  const valid = bars.filter((b) => b.close != null).sort((a, b) => a.trade_date.localeCompare(b.trade_date))
  const daily: Candle[] = valid.map((b) => ({
    date: b.trade_date,
    open: b.open ?? b.close!,
    high: b.high ?? Math.max(b.open ?? b.close!, b.close!),
    low: b.low ?? Math.min(b.open ?? b.close!, b.close!),
    close: b.close!,
    amount: b.amount ?? 0,
    change_pct: b.change_pct,
  }))
  if (period === 'day') return daily
  const key = (d: string) => {
    if (period === 'month') return d.slice(0, 7)
    const date = new Date(`${d}T00:00:00`)
    const monday = new Date(date)
    monday.setDate(date.getDate() - ((date.getDay() + 6) % 7))
    return monday.toISOString().slice(0, 10)
  }
  const groups = new Map<string, Candle>()
  for (const c of daily) {
    const k = key(c.date)
    const g = groups.get(k)
    if (!g) groups.set(k, { ...c })
    else {
      g.high = Math.max(g.high, c.high)
      g.low = Math.min(g.low, c.low)
      g.close = c.close
      g.amount += c.amount
      g.date = c.date
    }
  }
  const result = [...groups.values()]
  result.forEach((c, i) => {
    const prev = result[i - 1]
    c.change_pct = prev ? ((c.close / prev.close - 1) * 100) : null
  })
  return result
}

export function movingAverage(values: number[], n: number): (number | null)[] {
  return values.map((_, i) => (i + 1 < n ? null : values.slice(i + 1 - n, i + 1).reduce((a, b) => a + b, 0) / n))
}

export function CandlestickChart({ candles, height = 380, visible = 120 }: { candles: Candle[]; height?: number; visible?: number }) {
  const ref = useRef<HTMLDivElement>(null)
  const [width, setWidth] = useState(800)
  const [hover, setHover] = useState<number | null>(null)

  useEffect(() => {
    const el = ref.current
    if (!el) return
    const observer = new ResizeObserver(([entry]) => setWidth(Math.max(320, entry.contentRect.width)))
    observer.observe(el)
    return () => observer.disconnect()
  }, [])

  const data = useMemo(() => candles.slice(-visible), [candles, visible])
  const mas = useMemo(() => {
    const closes = candles.map((c) => c.close)
    const offset = candles.length - data.length
    return MA.map((m) => ({ ...m, values: movingAverage(closes, m.n).slice(offset) }))
  }, [candles, data.length])

  if (!data.length) return <div ref={ref} className="py-10 text-center text-sm text-muted">暂无 K 线数据</div>

  const pad = { left: 8, right: 56, top: 10, bottom: 18 }
  const priceH = height * 0.72
  const volTop = priceH + 12
  const volH = height - volTop - pad.bottom
  const step = (width - pad.left - pad.right) / data.length
  const bodyW = Math.max(1, Math.min(10, step * 0.7))
  const lows = data.map((c) => c.low)
  const highs = data.map((c) => c.high)
  const maValues = mas.flatMap((m) => m.values.filter((v): v is number => v != null))
  const minP = Math.min(...lows, ...maValues)
  const maxP = Math.max(...highs, ...maValues)
  const span = maxP - minP || 1
  const y = (p: number) => pad.top + (maxP - p) / span * (priceH - pad.top)
  const x = (i: number) => pad.left + step * i + step / 2
  const maxAmt = Math.max(...data.map((c) => c.amount), 1)
  const current = hover != null ? data[hover] : data[data.length - 1]
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((t) => minP + span * t)

  return (
    <div ref={ref} className="relative select-none">
      <div className="num mb-1 flex flex-wrap gap-x-3 text-xs text-muted">
        <span>{current.date}</span>
        <span>开 {fmtNum(current.open)}</span>
        <span>高 {fmtNum(current.high)}</span>
        <span>低 {fmtNum(current.low)}</span>
        <span>收 {fmtNum(current.close)}</span>
        <span className={current.change_pct == null ? '' : current.change_pct >= 0 ? 'text-up' : 'text-down'}>{fmtPct(current.change_pct)}</span>
        <span>额 {fmtAmount(current.amount)}</span>
        {mas.map((m) => {
          const v = m.values[hover ?? data.length - 1]
          return <span key={m.n} style={{ color: m.color }}>MA{m.n} {fmtNum(v)}</span>
        })}
      </div>
      <svg
        width={width}
        height={height}
        role="img"
        aria-label="K线图"
        onMouseMove={(e) => {
          const rect = (e.currentTarget as SVGElement).getBoundingClientRect()
          const i = Math.floor((e.clientX - rect.left - pad.left) / step)
          setHover(i >= 0 && i < data.length ? i : null)
        }}
        onMouseLeave={() => setHover(null)}
      >
        {ticks.map((t) => (
          <g key={t}>
            <line x1={pad.left} x2={width - pad.right} y1={y(t)} y2={y(t)} stroke="var(--line)" strokeDasharray="2 4" />
            <text x={width - pad.right + 4} y={y(t) + 4} fontSize="10" fill="var(--muted)">{t.toFixed(2)}</text>
          </g>
        ))}
        {data.map((c, i) => {
          const up = c.close >= c.open
          const color = up ? 'var(--up)' : 'var(--down)'
          const top = y(Math.max(c.open, c.close))
          const bodyH = Math.max(1, Math.abs(y(c.open) - y(c.close)))
          const volBarH = (c.amount / maxAmt) * volH
          return (
            <g key={c.date}>
              <line x1={x(i)} x2={x(i)} y1={y(c.high)} y2={y(c.low)} stroke={color} />
              <rect x={x(i) - bodyW / 2} y={top} width={bodyW} height={bodyH} fill={up ? 'var(--panel)' : color} stroke={color} />
              <rect x={x(i) - bodyW / 2} y={volTop + volH - volBarH} width={bodyW} height={volBarH} fill={color} opacity={0.6} />
            </g>
          )
        })}
        {mas.map((m) => (
          <polyline
            key={m.n}
            fill="none"
            stroke={m.color}
            strokeWidth={1}
            points={m.values.map((v, i) => (v == null ? '' : `${x(i)},${y(v)}`)).filter(Boolean).join(' ')}
          />
        ))}
        {hover != null && <line x1={x(hover)} x2={x(hover)} y1={pad.top} y2={height - pad.bottom} stroke="var(--muted)" strokeDasharray="3 3" />}
        <text x={pad.left} y={height - 4} fontSize="10" fill="var(--muted)">{data[0].date}</text>
        <text x={width - pad.right} y={height - 4} fontSize="10" fill="var(--muted)" textAnchor="end">{data[data.length - 1].date}</text>
      </svg>
    </div>
  )
}
