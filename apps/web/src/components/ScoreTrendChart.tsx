// 诊断评分走势：左轴评分、右轴收盘价，点按操作建议着色（红涨绿跌）
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { api } from '@/api/endpoints'
import type { DiagnosisTrendPoint } from '@/api/types'
import { useT } from '@/i18n'
import { useApi } from '@/hooks/useApi'

const LABELS: Record<string, string> = { buy: '买入', add: '加仓', hold: '持有', watch: '观望', reduce: '减仓', sell: '卖出', avoid: '回避' }
const BULLISH = ['buy', 'add']
const BEARISH = ['reduce', 'sell', 'avoid']

export const actionColor = (action: string) =>
  BULLISH.includes(action) ? 'var(--up)' : BEARISH.includes(action) ? 'var(--down)' : 'var(--muted)'

interface DotProps { cx?: number; cy?: number; payload?: DiagnosisTrendPoint }

function ActionDot({ cx, cy, payload }: DotProps) {
  if (cx === undefined || cy === undefined || !payload) return null
  return <circle cx={cx} cy={cy} r={4} fill={actionColor(payload.action)} stroke="var(--panel)" strokeWidth={1} />
}

interface TipProps { active?: boolean; payload?: { payload: DiagnosisTrendPoint }[] }

function TrendTip({ active, payload }: TipProps) {
  const t = useT()
  const p = active ? payload?.[0]?.payload : undefined
  if (!p) return null
  return (
    <div className="rounded border border-line bg-panel px-3 py-2 text-xs shadow">
      <div className="num text-muted">{p.created_at}</div>
      <div>{t('建议：')}<span style={{ color: actionColor(p.action) }}>{t(LABELS[p.action] ?? (p.action || '--'))}</span></div>
      <div>{t('评分：')}<span className="num">{p.score ?? '--'}</span></div>
      <div>{t('收盘价：')}<span className="num">{p.close ?? '--'}</span></div>
    </div>
  )
}

/** 传入 points 直接绘制；只传 code 时自己请求 /stocks/{code}/diagnosis-trend。 */
export function ScoreTrendChart({ points, code }: { points?: DiagnosisTrendPoint[]; code?: string }) {
  const t = useT()
  const { data } = useApi<DiagnosisTrendPoint[]>(
    () => (points || !code ? Promise.resolve([]) : api.diagnosisTrend(code)),
    [code, points],
  )
  const rows = points ?? data ?? []
  if (rows.length < 2) {
    return <p className="text-sm text-muted">{t('诊断记录不足 2 次，暂无评分走势')}</p>
  }
  const axisTick = { fill: 'var(--muted)', fontSize: 11 }
  return (
    <div className="h-56" aria-label={t('诊断评分走势')}>
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={rows} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--line)" />
          <XAxis dataKey="created_at" tickFormatter={(v: string) => v.slice(5, 10)} tick={axisTick} />
          <YAxis yAxisId="score" domain={[0, 100]} tick={axisTick} width={32} />
          <YAxis yAxisId="close" orientation="right" domain={['auto', 'auto']} tick={axisTick} width={44} />
          <Tooltip content={<TrendTip />} />
          <Line yAxisId="close" dataKey="close" name={t('收盘价')} stroke="var(--muted)" strokeDasharray="4 3" dot={false} connectNulls isAnimationActive={false} />
          <Line yAxisId="score" dataKey="score" name={t('评分')} stroke="var(--accent-strong)" strokeWidth={2} dot={<ActionDot />} activeDot={<ActionDot />} isAnimationActive={false} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  )
}
