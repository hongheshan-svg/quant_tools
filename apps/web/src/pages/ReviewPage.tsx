import { api } from '@/api/endpoints'
import type { MarketReview } from '@/api/types'
import { Markdown } from '@/components/Markdown'
import { Badge, Button, Card, ErrorBox, PageHeader, Spinner } from '@/components/ui'
import { useApi } from '@/hooks/useApi'
import { useTask } from '@/hooks/useTask'
import { useT } from '@/i18n'

export function ReviewPage() {
  const t = useT()
  const { data, error, loading, reload, setData } = useApi(api.review)
  const task = useTask<MarketReview>()
  const generate = () =>
    task.run(api.generateReview, { success: t('大盘复盘已生成') }).then((r) => (r.error ? void reload() : setData(r))).catch(() => {})
  return (
    <div>
      <PageHeader
        title={t('大盘复盘')}
        description={t('按趋势结构、资金情绪、主线板块复盘，给出次日姿态、仓位、关注与回避方向；姿态不会比量化大盘环境更激进')}
        actions={<Button variant="primary" loading={task.running} onClick={generate}>{task.running ? t('AI 复盘中…') : t('生成复盘')}</Button>}
      />
      {error && <ErrorBox message={error} onRetry={reload} />}
      {loading && !data && <Spinner />}
      {!loading && !data && !error && <Card><p className="text-sm text-muted">{t('还没有复盘。收盘后定时任务会自动生成，也可以点「生成复盘」。')}</p></Card>}
      {data && (
        <Card
          title={<span className="flex items-center gap-2">{t('{date} 复盘', { date: data.trade_date })} {data.stance && <Badge tone={data.stance === '进攻' ? 'up' : data.stance === '防守' ? 'down' : 'warn'}>{t(data.stance)}</Badge>}</span>}
          actions={(
            <span className="flex items-center gap-3">
              <span className="text-xs text-muted">{t('生成于 {time}', { time: data.created_at })}</span>
              {!data.error && (
                <a className="inline-flex items-center rounded-md border border-line px-3 py-1.5 text-sm font-medium hover:bg-panel-2" href={api.reviewImageUrl(data.trade_date)} target="_blank" rel="noreferrer">{t('分享图')}</a>
              )}
            </span>
          )}
        >
          {data.error ? <ErrorBox message={data.error} /> : <Markdown text={data.markdown} />}
        </Card>
      )}
    </div>
  )
}
