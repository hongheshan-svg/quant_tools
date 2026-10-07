// 每次选股独立保存的来源记录，旧批次不使用当前健康值回填。
import { useEffect } from 'react'
import { api } from '@/api/endpoints'
import { useApi } from '@/hooks/useApi'
import { useT } from '@/i18n'
import { Button, Card, ErrorBox } from '@/components/ui'

export function SourceRunHistory({ revision }: { revision?: number }) {
  const t = useT()
  const { data, error, reload } = useApi(api.screeningSourceHistory)
  const history = data?.summary && Array.isArray(data.items) ? data : null
  useEffect(() => { if (revision) void reload() }, [revision, reload])
  return <Card title={t('选股数据源运行历史')}>
    <Button onClick={reload}>{t('刷新')}</Button>
    {(error || data && !history) && <ErrorBox message={error || t('来源历史格式不兼容，请刷新后重试')} onRetry={reload} />}
    {history && <p className="my-2 text-sm">{t('已记录运行')} {history.summary.recorded}/{history.summary.runs} · {t('成功')} {history.summary.success} · {t('失败')} {history.summary.failure} · {t('回退恢复')} {history.summary.fallback_runs}</p>}
    {!history?.items.length && <p className="text-sm text-muted">{t('暂无运行记录')}</p>}
    {history?.items.map((row) => <details key={row.id} className="border-t border-line py-2 text-xs">
      <summary className="cursor-pointer">{row.created_at.replace('T', ' ')} · {row.trade_date} · {t(({ success: '成功', partial: '数据不完整', error: '失败' } as Record<string, string>)[row.status] ?? row.status)} · {row.sources ? `${t('成功')} ${row.sources.success} / ${t('失败')} ${row.sources.failure}` : t('旧运行未记录来源')}</summary>
      {row.sources && <div className="space-y-1 p-2">
        <p className="text-muted">{t('运行编号')}：{row.sources.run_id}</p>
        {row.sources.local_reads.map((read, i) => <p key={i}>{t('本地读取')} · {read.dataset} · {read.source} · {read.trade_date} · {read.rows} {t('行')}</p>)}
        {!row.sources.attempts.length && <p>{t('本次未记录网络取数')}</p>}
        {row.sources.attempts.map((attempt, i) => <p key={i} className={attempt.ok ? '' : 'text-warn'}>{attempt.dataset} · {attempt.source} · {attempt.ok ? t('成功') : t('失败')} · {attempt.ms}ms · {attempt.cache_hit && t('缓存')} {attempt.error}</p>)}
        {row.sources.fallback_datasets.length > 0 && <p>{t('回退恢复')}：{row.sources.fallback_datasets.join('、')}</p>}
        {row.sources.dropped > 0 && <p className="text-warn">{t('记录已截断')}</p>}
      </div>}
    </details>)}
  </Card>
}
