import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '@/api/endpoints'
import type { Theme } from '@/api/types'
import { DataTable, type Column } from '@/components/DataTable'
import { Badge, Card, ErrorBox, PageHeader, Spinner, Tabs } from '@/components/ui'
import { useApi } from '@/hooks/useApi'
import { fmtNum } from '@/utils/format'

const PHASE_TONE: Record<string, 'up' | 'warn' | 'accent' | 'down' | 'default'> = { 加速: 'up', 启动: 'accent', 持续发酵: 'warn', 降温: 'down', 退潮: 'down' }

export function ThemesPage() {
  const [dimension, setDimension] = useState<'concept' | 'industry'>('concept')
  const { data, error, loading, reload } = useApi(() => api.themes(dimension), [dimension])
  const navigate = useNavigate()
  const hot = (data ?? []).filter((t) => t.heat >= 40 && !['降温', '退潮'].includes(t.phase)).slice(0, 5).map((t) => t.name)

  const columns: Column<Theme>[] = [
    { key: 'name', title: dimension === 'concept' ? '题材' : '行业', render: (t) => <span className="font-medium">{t.name}</span> },
    { key: 'phase', title: '阶段', render: (t) => <Badge tone={PHASE_TONE[t.phase] ?? 'default'}>{t.phase}</Badge> },
    { key: 'heat', title: '热度', align: 'right', render: (t) => <span className="num">{fmtNum(t.heat, 0)}</span> },
    { key: 'history', title: '近 5 日热度', render: (t) => <span className="num text-xs text-muted">{t.heat_history.map((h) => h.toFixed(0)).join(' → ')}</span> },
    { key: 'trend', title: '趋势', align: 'right', render: (t) => <span className={`num ${t.trend > 0 ? 'text-up' : t.trend < 0 ? 'text-down' : 'text-muted'}`}>{t.trend > 0 ? '+' : ''}{fmtNum(t.trend, 0)}</span> },
    { key: 'persistence', title: '持续性', align: 'right', render: (t) => <span className="num">{fmtNum(t.persistence, 0)}%</span> },
    { key: 'limit_up', title: '涨停', align: 'right', render: (t) => <span className="num">{t.limit_up}</span> },
    { key: 'ladder', title: '梯队', render: (t) => <span className="text-xs">{t.ladder}</span> },
    {
      key: 'leader', title: '龙头', render: (t) =>
        t.leader?.code ? <button type="button" className="text-up hover:underline" onClick={() => navigate(`/stocks/${t.leader.code}`)}>{t.leader.name}({t.leader.height}板)</button> : '--',
    },
    { key: 'followers', title: '跟风', className: 'max-w-xs text-xs text-muted', render: (t) => t.followers.slice(0, 6).map((f) => `${f.name}(${f.height})`).join('、') },
  ]

  return (
    <div>
      <PageHeader
        title="主线分析"
        description="用近 5 日涨停池计算热度（10×涨停家数 + 8×(最高连板-1) + 5×连板家数），40 以上为热门；题材维度至少 2 家涨停才列出"
      />
      <Card bodyClassName="p-3">
        <Tabs value={dimension} onChange={setDimension} tabs={[{ key: 'concept', label: '按题材' }, { key: 'industry', label: '按行业' }]} />
        {hot.length > 0 && <p className="mb-2 text-sm">当前主线：<span className="text-up">{hot.join('、')}</span></p>}
        {error && <ErrorBox message={error} onRetry={reload} />}
        {loading && !data ? <Spinner /> : <DataTable columns={columns} rows={data ?? []} rowKey={(t) => t.name} empty="暂无数据（题材需要同花顺涨停原因，可运行 fetch_history.py --mode concepts 补齐）" />}
      </Card>
    </div>
  )
}
