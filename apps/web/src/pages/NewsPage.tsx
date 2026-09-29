import { api } from '@/api/endpoints'
import type { NewsItem } from '@/api/types'
import { Badge, Button, Card, ErrorBox, PageHeader, Spinner } from '@/components/ui'
import { useApi } from '@/hooks/useApi'
import { cn } from '@/utils/cn'

const SOURCE_LABELS: Record<string, string> = {
  cailianshe: '财联社', cailianshe_global: '财联社国际', jiuyan: '韭研公社', eastmoney: '东方财富', eastmoney_global: '东财全球',
  eastmoney_us_earnings: '美股财报', wallstreetcn: '华尔街见闻', wallstreetcn_us: '华尔街见闻', jin10: '金十数据', akshare: 'AKShare', rss: 'RSS',
}

// RSS 条目的 level 字段是订阅源名称，显示为「RSS·源名称」
function sourceLabel(n: NewsItem): string {
  const label = SOURCE_LABELS[n.source] ?? n.source
  return label === 'RSS' && n.level ? `RSS·${n.level}` : label
}

export function NewsPage() {
  const { data, error, loading, reload } = useApi(api.news)
  return (
    <div>
      <PageHeader title="实时资讯流" description="财联社、韭研公社、国际新闻、美股财报合并，重要消息置顶" actions={<Button onClick={reload} loading={loading}>刷新</Button>} />
      {error && <ErrorBox message={error} onRetry={reload} />}
      {loading && !data && <Spinner />}
      <Card bodyClassName="p-0">
        <ul className="divide-y divide-line">
          {(data ?? []).map((n, i) => (
            <li key={`${n.time}-${i}`} className="flex gap-3 px-4 py-2.5">
              <span className="num w-28 shrink-0 text-xs text-muted">{n.time}</span>
              <div className="min-w-0">
                <a
                  href={n.url || undefined}
                  target="_blank"
                  rel="noreferrer"
                  className={cn('text-sm', n.level === 'red' || n.level === 'important' ? 'font-semibold text-up' : 'text-text', n.url && 'hover:underline')}
                >
                  {n.title}
                </a>
                <div className="mt-1 flex flex-wrap gap-1">
                  <Badge>{sourceLabel(n)}</Badge>
                  {(n.tags ?? []).slice(0, 4).map((t) => <Badge key={t} tone="accent">{t}</Badge>)}
                </div>
              </div>
            </li>
          ))}
        </ul>
        {data && !data.length && <p className="p-4 text-sm text-muted">暂无资讯，先在交易决策页采集数据</p>}
      </Card>
    </div>
  )
}
