import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '@/api/endpoints'
import type { Theme } from '@/api/types'
import { DataTable, type Column } from '@/components/DataTable'
import { Badge, Card, ErrorBox, PageHeader, Spinner, Tabs } from '@/components/ui'
import { useApi } from '@/hooks/useApi'
import { useT } from '@/i18n'
import { fmtNum } from '@/utils/format'

const PHASE_TONE: Record<string, 'up' | 'warn' | 'accent' | 'down' | 'default'> = { 加速: 'up', 启动: 'accent', 持续发酵: 'warn', 降温: 'down', 退潮: 'down' }

export function ThemesPage() {
  const t = useT()
  const [dimension, setDimension] = useState<'concept' | 'industry'>('concept')
  const { data, error, loading, reload } = useApi(() => api.themes(dimension), [dimension])
  const navigate = useNavigate()
  const hot = (data ?? []).filter((x) => x.heat >= 40 && !['降温', '退潮'].includes(x.phase)).slice(0, 5).map((x) => x.name)

  const columns: Column<Theme>[] = [
    { key: 'name', title: dimension === 'concept' ? t('题材') : t('行业'), render: (r) => <span className="font-medium">{r.name}</span> },
    { key: 'phase', title: t('阶段'), render: (r) => <Badge tone={PHASE_TONE[r.phase] ?? 'default'}>{t(r.phase)}</Badge> },
    { key: 'heat', title: t('热度'), align: 'right', render: (r) => <span className="num">{fmtNum(r.heat, 0)}</span> },
    { key: 'history', title: t('近 5 日热度'), render: (r) => <span className="num text-xs text-muted">{r.heat_history.map((h) => h.toFixed(0)).join(' → ')}</span> },
    { key: 'trend', title: t('趋势'), align: 'right', render: (r) => <span className={`num ${r.trend > 0 ? 'text-up' : r.trend < 0 ? 'text-down' : 'text-muted'}`}>{r.trend > 0 ? '+' : ''}{fmtNum(r.trend, 0)}</span> },
    { key: 'persistence', title: t('持续性'), align: 'right', render: (r) => <span className="num">{fmtNum(r.persistence, 0)}%</span> },
    { key: 'limit_up', title: t('涨停'), align: 'right', render: (r) => <span className="num">{r.limit_up}</span> },
    { key: 'ladder', title: t('梯队'), render: (r) => <span className="text-xs">{r.ladder}</span> },
    {
      key: 'leader', title: t('龙头'), render: (r) =>
        r.leader?.code ? <button type="button" className="text-up hover:underline" onClick={() => navigate(`/stocks/${r.leader.code}`)}>{r.leader.name}({t('{n}板', { n: r.leader.height ?? '-' })})</button> : '--',
    },
    { key: 'followers', title: t('跟风'), className: 'max-w-xs text-xs text-muted', render: (r) => r.followers.slice(0, 6).map((f) => `${f.name}(${f.height})`).join('、') },
  ]

  return (
    <div>
      <PageHeader
        title={t('主线分析')}
        description={t('用近 5 日涨停池计算热度（10×涨停家数 + 8×(最高连板-1) + 5×连板家数），40 以上为热门；题材维度至少 2 家涨停才列出')}
      />
      <Card bodyClassName="p-3">
        <Tabs value={dimension} onChange={setDimension} tabs={[{ key: 'concept', label: t('按题材') }, { key: 'industry', label: t('按行业') }]} />
        {hot.length > 0 && <p className="mb-2 text-sm">{t('当前主线：')}<span className="text-up">{hot.join('、')}</span></p>}
        {error && <ErrorBox message={error} onRetry={reload} />}
        {loading && !data ? <Spinner /> : <DataTable columns={columns} rows={data ?? []} rowKey={(r) => r.name} empty={t('暂无数据（题材需要同花顺涨停原因，可运行 fetch_history.py --mode concepts 补齐）')} />}
      </Card>
    </div>
  )
}
