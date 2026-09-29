// ETF / 指数的识别：指数的规范代码带交易所前缀（sh000300），ETF 是场内基金代码段的 6 位代码
export type FundKind = 'index' | 'etf'

export const FUND_LABELS: Record<FundKind, string> = { index: '指数', etf: 'ETF' }

export function fundKind(code: string): FundKind | null {
  const c = code.trim().toLowerCase()
  if (/^(sh|sz|bj)\d{6}$/.test(c)) return 'index'
  if (/^(50|51|52|56|58|15|16)\d{4}$/.test(c)) return 'etf'
  return null
}
