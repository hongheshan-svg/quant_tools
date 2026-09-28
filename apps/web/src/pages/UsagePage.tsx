// AI 用量：调用次数、token、估算费用，按天、按功能、按模型
import { useState } from 'react'
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { api } from '@/api/endpoints'
import type { UsageRow } from '@/api/types'
import { DataTable, type Column } from '@/components/DataTable'
import { Card, ErrorBox, PageHeader, Select, Spinner, Stat } from '@/components/ui'
import { useApi } from '@/hooks/useApi'

export const fmtTokens = (n: number) => (n >= 1e6 ? `${(n / 1e6).toFixed(2)}M` : n >= 1e3 ? `${(n / 1e3).toFixed(1)}K` : String(n))
const fmtCost = (v: number) => (v ? `$${v.toFixed(v < 1 ? 4 : 2)}` : '--')

function usageColumns(keyTitle: string): Column<UsageRow>[] {
  return [
    { key: 'key', title: keyTitle, render: (r) => r.key },
    { key: 'calls', title: '调用', align: 'right', render: (r) => <span className="num">{r.calls}</span> },
    { key: 'cached', title: '命中缓存', align: 'right', render: (r) => <span className="num text-muted">{r.cached}</span> },
    { key: 'failed', title: '失败', align: 'right', render: (r) => <span className={`num ${r.failed ? 'text-danger' : 'text-muted'}`}>{r.failed}</span> },
    { key: 'in', title: '输入 token', align: 'right', render: (r) => <span className="num">{fmtTokens(r.prompt_tokens)}</span> },
    { key: 'out', title: '输出 token', align: 'right', render: (r) => <span className="num">{fmtTokens(r.completion_tokens)}</span> },
    { key: 'cost', title: '估算费用', align: 'right', render: (r) => <span className="num">{fmtCost(r.cost_usd)}</span> },
  ]
}

export function UsagePage() {
  const [days, setDays] = useState(30)
  const { data, error, loading, reload } = useApi(() => api.usage(days), [days])
  const total = data?.total
  return (
    <div>
      <PageHeader
        title="AI 用量"
        description="每次大模型调用都会记录（含命中缓存和失败），功能按调用模块自动归类；费用按 LiteLLM 价格表或 llm.pricing 估算"
        actions={
          <Select value={days} onChange={(e) => setDays(Number(e.target.value))} aria-label="统计天数">
            {[7, 30, 90].map((d) => <option key={d} value={d}>近 {d} 天</option>)}
          </Select>
        }
      />
      {error && <ErrorBox message={error} onRetry={reload} />}
      {loading && !data && <Spinner />}
      {total && (
        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-2 md:grid-cols-5">
            <Stat label="调用次数" value={total.calls} />
            <Stat label="命中缓存" value={total.cached} sub={<span className="text-muted">{total.calls ? `${((total.cached / total.calls) * 100).toFixed(0)}%` : ''}</span>} />
            <Stat label="失败" value={<span className={total.failed ? 'text-danger' : ''}>{total.failed}</span>} />
            <Stat label="Token" value={fmtTokens(total.tokens)} />
            <Stat label="估算费用" value={fmtCost(total.cost_usd)} />
          </div>
          <Card title="每日 Token">
            <div className="h-64">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={data.by_day}>
                  <CartesianGrid strokeDasharray="3 3" stroke="var(--line)" />
                  <XAxis dataKey="key" tick={{ fill: 'var(--muted)', fontSize: 11 }} />
                  <YAxis tickFormatter={fmtTokens} tick={{ fill: 'var(--muted)', fontSize: 11 }} />
                  <Tooltip
                    formatter={(v) => fmtTokens(Number(v))}
                    contentStyle={{ background: 'var(--panel)', border: '1px solid var(--line)', color: 'var(--text)' }}
                  />
                  <Bar dataKey="prompt_tokens" name="输入" stackId="t" fill="var(--accent-strong)" />
                  <Bar dataKey="completion_tokens" name="输出" stackId="t" fill="var(--warn)" />
                </BarChart>
              </ResponsiveContainer>
            </div>
          </Card>
          <div className="grid gap-4 xl:grid-cols-2">
            <Card title="按功能" bodyClassName="p-0"><DataTable columns={usageColumns('功能')} rows={data.by_feature} rowKey={(r) => r.key} /></Card>
            <Card title="按模型" bodyClassName="p-0"><DataTable columns={usageColumns('模型')} rows={data.by_model} rowKey={(r) => r.key} /></Card>
          </div>
        </div>
      )}
    </div>
  )
}
