import { api } from '@/api/endpoints'
import type { MarketReview } from '@/api/types'
import { Markdown } from '@/components/Markdown'
import { Badge, Button, Card, ErrorBox, PageHeader, Spinner } from '@/components/ui'
import { useApi } from '@/hooks/useApi'
import { useTask } from '@/hooks/useTask'

export function ReviewPage() {
  const { data, error, loading, reload, setData } = useApi(api.review)
  const task = useTask<MarketReview>()
  const generate = () =>
    task.run(api.generateReview, { success: '大盘复盘已生成' }).then((r) => (r.error ? void reload() : setData(r))).catch(() => {})
  return (
    <div>
      <PageHeader
        title="大盘复盘"
        description="按趋势结构、资金情绪、主线板块复盘，给出次日姿态、仓位、关注与回避方向；姿态不会比量化大盘环境更激进"
        actions={<Button variant="primary" loading={task.running} onClick={generate}>{task.running ? 'AI 复盘中…' : '生成复盘'}</Button>}
      />
      {error && <ErrorBox message={error} onRetry={reload} />}
      {loading && !data && <Spinner />}
      {!loading && !data && !error && <Card><p className="text-sm text-muted">还没有复盘。收盘后定时任务会自动生成，也可以点「生成复盘」。</p></Card>}
      {data && (
        <Card
          title={<span className="flex items-center gap-2">{data.trade_date} 复盘 {data.stance && <Badge tone={data.stance === '进攻' ? 'up' : data.stance === '防守' ? 'down' : 'warn'}>{data.stance}</Badge>}</span>}
          actions={<span className="text-xs text-muted">生成于 {data.created_at}</span>}
        >
          {data.error ? <ErrorBox message={data.error} /> : <Markdown text={data.markdown} />}
        </Card>
      )}
    </div>
  )
}
