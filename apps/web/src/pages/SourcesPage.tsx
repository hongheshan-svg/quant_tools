import { api } from '@/api/endpoints'
import type { SourceStatus } from '@/api/types'
import { DataTable, type Column } from '@/components/DataTable'
import { Badge, Button, Card, ErrorBox, PageHeader } from '@/components/ui'
import { useApi } from '@/hooks/useApi'
import { fmtNum } from '@/utils/format'

const STATUS: Record<string, [string, 'down' | 'warn' | 'up']> = { ok: ['正常', 'down'], failing: ['失败', 'warn'], circuit_open: ['熔断中', 'up'] }
const time = (v: string | null) => (v ? v.slice(11, 19) : '--')

export function SourcesPage() {
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
      <PageHeader title="数据源状态" description="本进程内各数据源的成功/失败与熔断状态（连续失败 3 次的数据源熔断 5 分钟后再试）" actions={<Button onClick={reload} loading={loading}>刷新</Button>} />
      {error && <ErrorBox message={error} onRetry={reload} />}
      <Card bodyClassName="p-0">
        <DataTable columns={columns} rows={data ?? []} rowKey={(r) => `${r.dataset}-${r.source}`} empty="采集运行后显示" />
      </Card>
    </div>
  )
}
