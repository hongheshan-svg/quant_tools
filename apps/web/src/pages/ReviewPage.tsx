import { useEffect, useState } from 'react'
import { api } from '@/api/endpoints'
import type { MarketReview, ReviewRegion, ReviewSettings } from '@/api/types'
import { Markdown } from '@/components/Markdown'
import { Badge, Button, Card, ErrorBox, PageHeader, Select, Spinner } from '@/components/ui'
import { SettingsDraftBoundary, SettingsNavigationGuard } from '@/components/SettingsDraftBoundary'
import { useApi } from '@/hooks/useApi'
import { useTask } from '@/hooks/useTask'
import { useT } from '@/i18n'
import { toast } from '@/stores/toast'

const markets: [ReviewRegion, string][] = [['CN', 'A 股'], ['HK', '港股'], ['US', '美股'], ['JP', '日股'], ['KR', '韩股'], ['TW', '台股']]

export function ReviewPage() {
  const [region, setRegion] = useState<ReviewRegion>('CN')
  return <div className="space-y-4"><SettingsNavigationGuard />
    <RegionalReview key={region} region={region} onRegion={setRegion} />
    <SettingsDraftBoundary id="market-review" paths={['/settings/market-review']}><ReviewScheduleSettings /></SettingsDraftBoundary>
  </div>
}

function RegionalReview({ region, onRegion }: { region: ReviewRegion; onRegion: (region: ReviewRegion) => void }) {
  const t = useT()
  const { data, error, loading, reload, setData } = useApi(() => api.review(region))
  const task = useTask<MarketReview>()
  const [generationError, setGenerationError] = useState('')
  const generate = () => { setGenerationError(''); return task.run(() => api.generateReview(region), { success: t('大盘复盘已生成') })
    .then((r) => (r.error ? void reload() : setData(r))).catch((error) => setGenerationError(String(error))) }
  return <div className="space-y-4">
    <PageHeader title={t('大盘复盘')}
      description={region === 'CN' ? t('按趋势结构、资金情绪、主线板块复盘，给出次日姿态、仓位、关注与回避方向；姿态不会比量化大盘环境更激进') : '按市场本地时区和最近已完成交易日复盘；指数、新闻缺失时显示数据边界。'}
      actions={<Button variant="primary" loading={task.running} onClick={generate}>{task.running ? t('AI 复盘中…') : t('生成复盘')}</Button>} />
    <label className="flex items-center gap-3 text-sm">复盘市场<Select aria-label="复盘市场" value={region} disabled={task.running} onChange={(event) => onRegion(event.target.value as ReviewRegion)}>{markets.map(([code, label]) => <option value={code} key={code}>{label}</option>)}</Select></label>
    {error && <ErrorBox message={error} onRetry={reload} />}
    {generationError && <ErrorBox message={generationError} />}
    {loading && !data && <Spinner />}
    {!loading && !data && !error && <Card><p className="text-sm text-muted">{t('还没有复盘。收盘后定时任务会自动生成，也可以点「生成复盘」。')}</p></Card>}
    {data && <Card
      title={<span className="flex items-center gap-2">{region} · {t('{date} 复盘', { date: data.trade_date })} {data.stance && <Badge tone={data.stance === '进攻' ? 'up' : data.stance === '防守' ? 'down' : 'warn'}>{t(data.stance)}</Badge>}{data.quality && <Badge>{data.quality.status === 'available' ? '数据完整' : '部分数据缺失'}</Badge>}</span>}
      actions={<span className="flex items-center gap-3"><span className="text-xs text-muted">{t('生成于 {time}', { time: data.created_at })}</span>{!data.error && <a className="inline-flex items-center rounded-md border border-line px-3 py-1.5 text-sm font-medium hover:bg-panel-2" href={api.reviewImageUrl(data.trade_date, region)} target="_blank" rel="noreferrer">{t('分享图')}</a>}</span>}>
      {data.error ? <ErrorBox message={data.error} /> : <Markdown text={data.markdown} />}
    </Card>}
  </div>
}

function ReviewScheduleSettings() {
  const settings = useApi(api.reviewSettings)
  const [form, setForm] = useState<ReviewSettings>({ enabled: true, regions: ['CN'] })
  const [busy, setBusy] = useState(false)
  useEffect(() => { if (settings.data) setForm(settings.data) }, [settings.data])
  if (settings.error) return <ErrorBox message={settings.error} onRetry={settings.reload} />
  if (!settings.data) return <Spinner />
  return <Card><h2 className="font-semibold">定时复盘范围</h2>
    <p className="my-2 text-sm text-muted">沿用设置中的每日报告时间。海外市场尚未收盘时使用上一完整交易日；单个市场失败不会中断其他地区，中国节假日仍会检查所选海外市场。</p>
    <fieldset disabled={busy}><label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={form.enabled} onChange={(event) => setForm({ ...form, enabled: event.target.checked })} />启用定时 AI 复盘</label>
      <div className="my-3 flex flex-wrap gap-4">{markets.map(([code, label]) => <label className="flex items-center gap-2 text-sm" key={code}><input type="checkbox" aria-label={`定时复盘-${label}`} checked={form.regions.includes(code)} onChange={(event) => setForm({ ...form, regions: event.target.checked ? [...form.regions, code] : form.regions.filter((item) => item !== code) })} />{label}</label>)}</div>
      <Button disabled={!form.regions.length} loading={busy} onClick={async () => { setBusy(true); try { await api.saveReviewSettings(form); await settings.reload(); toast.success('复盘范围已保存') } catch (error) { toast.error(String(error)) } finally { setBusy(false) } }}>保存复盘范围</Button>
    </fieldset>
  </Card>
}
