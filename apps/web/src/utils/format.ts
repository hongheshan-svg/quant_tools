// 数字格式化与涨跌配色（A 股：红涨绿跌）

export const isNum = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v)

export function fmtNum(v: unknown, digits = 2): string {
  return isNum(v) ? v.toFixed(digits) : '--'
}

/** 百分比：signed 时带正负号 */
export function fmtPct(v: unknown, digits = 2, signed = true): string {
  if (!isNum(v)) return '--'
  return `${signed && v > 0 ? '+' : ''}${v.toFixed(digits)}%`
}

/** 金额：自动换算成 亿 / 万 */
export function fmtAmount(v: unknown): string {
  if (!isNum(v)) return '--'
  const abs = Math.abs(v)
  if (abs >= 1e8) return `${(v / 1e8).toFixed(2)}亿`
  if (abs >= 1e4) return `${(v / 1e4).toFixed(1)}万`
  return v.toFixed(0)
}

export function fmtMoney(v: unknown, signed = false): string {
  if (!isNum(v)) return '--'
  const text = v.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
  return signed && v > 0 ? `+${text}` : text
}

/** 涨跌颜色类名 */
export function trendClass(v: unknown): string {
  if (!isNum(v) || v === 0) return 'text-muted'
  return v > 0 ? 'text-up' : 'text-down'
}

/** 操作建议 / AI 研判的颜色 */
export function verdictClass(text: string | null | undefined): string {
  const t = text ?? ''
  if (/买入|加仓|强烈|看多/.test(t)) return 'text-up'
  if (/卖出|回避|减仓|看空/.test(t)) return 'text-down'
  if (/观望|持有|中性/.test(t)) return 'text-warn'
  return 'text-muted'
}

export const RECOMMENDATION_LABELS: Record<string, string> = {
  strong_buy: '强推',
  buy: '推荐',
  hold: '观望',
  avoid: '回避',
}
