import { api } from '@/api/endpoints'
import type { AlertRow } from '@/api/types'
import { DataTable, type Column } from '@/components/DataTable'
import { Badge, Button, Card, ErrorBox, PageHeader } from '@/components/ui'
import { useApi } from '@/hooks/useApi'
import { useTask } from '@/hooks/useTask'

const LEVEL: Record<string, [string, 'down' | 'warn' | 'accent']> = { critical: ['紧急', 'down'], warning: ['注意', 'warn'], info: ['提示', 'accent'] }

export function AlertsPage() {
  const { data, error, loading, reload } = useApi(api.alerts)
  const check = useTask<{ alerts: number; skipped?: string }>()
  const columns: Column<AlertRow>[] = [
    { key: 'time', title: '时间', render: (r) => <span className="num text-xs">{r.time}</span> },
    { key: 'stock', title: '股票', render: (r) => <>{r.name} <span className="num text-xs text-muted">{r.code}</span></> },
    { key: 'type', title: '类型', render: (r) => r.type },
    { key: 'level', title: '级别', render: (r) => <Badge tone={(LEVEL[r.severity] ?? ['', 'accent'])[1]}>{(LEVEL[r.severity] ?? [r.severity])[0]}</Badge> },
    { key: 'message', title: '内容', className: 'max-w-lg', render: (r) => r.message },
    { key: 'pushed', title: '推送', render: (r) => (r.notified ? <span className="text-down">已推送</span> : <span className="text-xs text-muted">{r.reason || '未推送'}</span>) },
  ]
  return (
    <div>
      <PageHeader
        title="盘中提醒"
        description="交易时段内每次采集后检查今日信号股、持仓、自选股：封涨停、炸板、跌破/接近止损、目标价、大跌、大盘转弱和自定义技术指标规则"
        actions={
          <Button loading={check.running} onClick={() => check.run(api.checkAlerts, { success: (r) => (r.skipped ? `已跳过：${r.skipped}` : `新增 ${r.alerts} 条提醒`) }).then(reload).catch(() => {})}>
            立即检查
          </Button>
        }
      />
      {error && <ErrorBox message={error} onRetry={reload} />}
      <Card bodyClassName="p-0">
        <DataTable columns={columns} rows={data ?? []} rowKey={(r, i) => `${r.time}-${r.code}-${i}`} empty={loading ? '加载中…' : '还没有提醒记录'} maxHeight="75vh" />
      </Card>
    </div>
  )
}
