import { api } from '@/api/endpoints'
import { useState } from 'react'
import { Link } from 'react-router-dom'
import type { NewsItem } from '@/api/types'
import { Badge, Button, Card, ErrorBox, PageHeader, Spinner, Input } from '@/components/ui'
import { useApi } from '@/hooks/useApi'
import { useT } from '@/i18n'
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
  const t = useT()
  const { data, error, loading, reload } = useApi(api.news)
  const [symbol, setSymbol] = useState('')
  const [sector, setSector] = useState('')
  const validSymbol = /^(?:(?:sh|sz|bj)\.?\d{6}|\d{6}(?:\.(?:sh|sz|bj))?)$/i.test(symbol.trim()) ? symbol.trim() : undefined
  const scoped = useApi(() => api.intelligenceItems({ symbol: validSymbol, sector: sector || undefined }), [validSymbol, sector])
  return (
    <div>
      <PageHeader title={t('实时资讯流')} description={t('财联社、韭研公社、国际新闻、美股财报合并，重要消息置顶')} actions={<Button onClick={reload} loading={loading}>{t('刷新')}</Button>} />
      <Card className="mb-4" title={t('范围资讯')} actions={<Link to="/intelligence" className="text-sm text-accent">{t('管理资讯源')}</Link>}>
        <div className="mb-3 flex gap-2"><Input aria-label={t('股票代码')} placeholder={t('股票代码，可空')} value={symbol} onChange={(e) => setSymbol(e.target.value)} /><Input aria-label={t('行业')} placeholder={t('行业，可空')} value={sector} onChange={(e) => setSector(e.target.value)} /></div>
        {scoped.error && <ErrorBox message={scoped.error} />}
        <ul className="space-y-2">{scoped.data?.map((i) => <li key={i.id}><a href={i.url || undefined} target="_blank" rel="noreferrer">{i.title}</a><span className="ml-2 text-xs text-muted">{i.source} · {i.published_at?.slice(0, 16) || t('发布时间未知')}</span></li>)}</ul>
      </Card>
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
                  className={cn('text-sm', n.important ? 'font-semibold text-up' : 'text-text', n.url && 'hover:underline')}
                >
                  {n.title}
                </a>
                <div className="mt-1 flex flex-wrap gap-1">
                  <Badge>{t(sourceLabel(n))}</Badge>
                  {(n.tags ?? []).slice(0, 4).map((tag) => <Badge key={tag} tone="accent">{tag}</Badge>)}
                </div>
              </div>
            </li>
          ))}
        </ul>
        {data && !data.length && <p className="p-4 text-sm text-muted">{t('暂无资讯，先在交易决策页采集数据')}</p>}
      </Card>
    </div>
  )
}
