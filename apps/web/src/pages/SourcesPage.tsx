import { useState } from 'react'
import { api } from '@/api/endpoints'
import type { DataCapability, SourceStatus } from '@/api/types'
import { DataTable, type Column } from '@/components/DataTable'
import { Badge, Button, Card, ErrorBox, PageHeader, Tabs } from '@/components/ui'
import { useApi } from '@/hooks/useApi'
import { fmtNum } from '@/utils/format'

const STATUS: Record<string, [string, 'down' | 'warn' | 'up']> = { ok: ['正常', 'down'], failing: ['失败', 'warn'], circuit_open: ['熔断中', 'up'] }
const HEALTH: Record<string, [string, 'down' | 'warn' | 'up' | 'default']> = {
  ok: ['正常', 'down'], failing: ['失败', 'warn'], open: ['熔断中', 'up'], unknown: ['未知', 'default'],
}
const time = (v: string | null) => (v ? v.slice(11, 19) : '--')

function Capabilities() {
  const { data, error, loading, reload } = useApi(api.capabilities)
  return (
    <div>
      <div className="mb-3 flex items-center justify-between gap-3">
        <p className="text-sm text-muted">各数据集按回退顺序列出数据源；健康状态来自本进程内的运行记录，未运行过为「未知」。</p>
        <Button onClick={reload} loading={loading}>刷新</Button>
      </div>
      {error && <ErrorBox message={error} onRetry={reload} />}
      <div className="space-y-3">
        {(data ?? []).map((ds: DataCapability) => (
          <Card key={ds.dataset} title={ds.label} bodyClassName="p-0">
            {ds.sources.length === 0 ? (
              <p className="px-4 py-3 text-sm text-muted">暂无已配置的数据源</p>
            ) : (
              <ol className="divide-y divide-line">
                {ds.sources.map((s, i) => {
                  const [text, tone] = HEALTH[s.health.status] ?? HEALTH.unknown
                  return (
                    <li key={s.name} className="flex flex-wrap items-center gap-x-3 gap-y-1 px-4 py-2 text-sm">
                      <span className="num w-5 text-muted">{i + 1}</span>
                      <span className="font-medium">{s.label}</span>
                      <Badge tone={s.configured ? 'down' : 'warn'}>{s.configured ? '已配置' : '未配置'}</Badge>
                      <Badge tone={tone}>{text}</Badge>
                      {s.note && <span className="text-xs text-muted">{s.note}</span>}
                      {s.health.last_error && <span className="max-w-full text-xs text-muted">最近错误：{s.health.last_error}</span>}
                    </li>
                  )
                })}
              </ol>
            )}
          </Card>
        ))}
      </div>
    </div>
  )
}

export function SourcesPage() {
  const [tab, setTab] = useState<'status' | 'capabilities'>('status')
  return (
    <div>
      <PageHeader title="数据源状态" description="本进程内各数据源的成功/失败与熔断状态（连续失败 3 次的数据源熔断 5 分钟后再试）" />
      <Tabs<'status' | 'capabilities'> tabs={[{ key: 'status', label: '运行状态' }, { key: 'capabilities', label: '能力总览' }]} value={tab} onChange={setTab} />
      {tab === 'status' ? <StatusTable /> : <Capabilities />}
    </div>
  )
}

function StatusTable() {
  const { data, error, loading, reload } = useApi(api.sources)
  const columns: Column<SourceStatus>[] = [
    { key: 'dataset', title: '数据集', render: (r) => r.dataset },
    { key: 'source', title: '数据源', render: (r) => r.source },
    { key: 'status', title: '状态', render: (r) => <Badge tone={(STATUS[r.status] ?? ['', 'warn'])[1]}>{(STATUS[r.status] ?? [r.status])[0]}</Badge> },
    { key: 'ok', title: '最近成功', render: (r) => <span className="num">{time(r.last_success)}</span> },
    { key: 'fail', title: '最近失败', render: (r) => <span className="num">{time(r.last_failure)}</span> },
    { key: 'streak', title: '连续失败', align: 'right', render: (r) => <span className="num">{r.consecutive_failures}</span> },
    { key: 'total', title: '累计成功/失败', align: 'right', render: (r) => <span className="num">{r.total_success}/{r.total_failure}</span> },
    { key: 'elapsed', title: '耗时(秒)', align: 'right', render: (r) => <span className="num">{fmtNum(r.last_elapsed, 2)}</span> },
    { key: 'error', title: '最近错误', className: 'max-w-sm text-xs text-muted', render: (r) => r.last_error },
  ]
  return (
    <div>
      <div className="mb-3 flex justify-end"><Button onClick={reload} loading={loading}>刷新</Button></div>
      {error && <ErrorBox message={error} onRetry={reload} />}
      <Card bodyClassName="p-0">
        <DataTable columns={columns} rows={data ?? []} rowKey={(r) => `${r.dataset}-${r.source}`} empty="采集运行后显示" />
      </Card>
    </div>
  )
}
