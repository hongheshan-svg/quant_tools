// 可视化配置行情源顺序与凭据；掩码原样保存时由后端保留原密钥。
import { ArrowDown, ArrowUp, Plus, X } from 'lucide-react'
import { useState } from 'react'
import { api } from '@/api/endpoints'
import type { DataSourceSettings, DataSourceSettingsResponse } from '@/api/types'
import { useApi } from '@/hooks/useApi'
import { useTask } from '@/hooks/useTask'
import type { DataSourceProbe } from '@/api/types'
import { useT } from '@/i18n'
import { toast } from '@/stores/toast'
import { Button, Card, ErrorBox, Input, Select, Spinner } from './ui'

const LABELS: Record<string, string> = { tencent: '腾讯财经', eastmoney: '东方财富', sina: '新浪', baostock: 'baostock', pytdx: '通达信', efinance: 'efinance', tushare: 'Tushare', tickflow: 'TickFlow' }

function SourceOrder({ title, value, options, onChange }: { title: string; value: string[]; options: string[]; onChange: (value: string[]) => void }) {
  const t = useT()
  const move = (index: number, direction: number) => {
    const next = [...value]
    const previous = next[index]
    next[index] = next[index + direction]
    next[index + direction] = previous
    onChange(next)
  }
  return <Card title={title}>
    <p className="mb-3 text-xs text-muted">{t('按顺序尝试，失败或数据无效时自动切换。')}</p>
    <ol className="space-y-2">
      {value.map((name, index) => <li key={name} className="flex items-center gap-2 rounded-md border border-line bg-panel-2 p-2">
        <span className="num w-6 text-xs text-muted">{index + 1}</span><span className="flex-1 text-sm">{LABELS[name] || name}</span>
        <Button aria-label={`${title} ${LABELS[name] || name} ${t('上移')}`} disabled={index === 0} onClick={() => move(index, -1)}><ArrowUp className="size-3" /></Button>
        <Button aria-label={`${title} ${LABELS[name] || name} ${t('下移')}`} disabled={index === value.length - 1} onClick={() => move(index, 1)}><ArrowDown className="size-3" /></Button>
        <Button aria-label={`${title} ${LABELS[name] || name} ${t('停用')}`} disabled={value.length === 1} onClick={() => onChange(value.filter((n) => n !== name))}><X className="size-3" /></Button>
      </li>)}
    </ol>
    <div className="mt-3 flex flex-wrap gap-2">{options.filter((n) => !value.includes(n)).map((name) => <Button key={name} onClick={() => onChange([...value, name])}><Plus className="size-3" />{LABELS[name] || name}</Button>)}</div>
  </Card>
}

function SettingsEditor({ initial, onSaved }: { initial: DataSourceSettingsResponse; onSaved: (value: DataSourceSettingsResponse) => void }) {
  const t = useT()
  const [value, setValue] = useState(initial.data_sources)
  const [saving, setSaving] = useState(false)
  const [probeSource, setProbeSource] = useState(initial.daily_options[0])
  const [probeCode, setProbeCode] = useState('600519')
  const [probeResult, setProbeResult] = useState<DataSourceProbe | null>(null)
  const probe = useTask<DataSourceProbe>()
  const patch = <K extends keyof DataSourceSettings>(key: K, next: DataSourceSettings[K]) => setValue((v) => ({ ...v, [key]: next }))
  const save = async () => {
    setSaving(true)
    try {
      const response = await api.saveDataSourceSettings(value)
      setValue(response.data_sources)
      onSaved(response)
      toast.success(t('数据源配置已保存并生效'))
    } catch (error) { toast.error(error instanceof Error ? error.message : String(error)) }
    finally { setSaving(false) }
  }
  return <div className="space-y-4">
    <div className="grid gap-4 lg:grid-cols-2">
      <SourceOrder title={t('实时行情回退顺序')} value={value.realtime} options={initial.realtime_options} onChange={(v) => patch('realtime', v)} />
      <SourceOrder title={t('日线回补顺序')} value={value.daily_history} options={initial.daily_options} onChange={(v) => patch('daily_history', v)} />
    </div>
    <Card title={t('供应商与数据质量')}>
      <div className="grid gap-4 md:grid-cols-2">
        <label className="space-y-1 text-xs text-muted">Tushare Token<Input type="password" autoComplete="new-password" value={value.tushare_token} onChange={(e) => patch('tushare_token', e.target.value)} /></label>
        <label className="space-y-1 text-xs text-muted">TickFlow API Key<Input type="password" autoComplete="new-password" value={value.tickflow_api_key} onChange={(e) => patch('tickflow_api_key', e.target.value)} /></label>
        <label className="space-y-1 text-xs text-muted">Miaoxiang API Key<Input type="password" autoComplete="new-password" value={value.miaoxiang_api_key ?? ''} onChange={(e) => patch('miaoxiang_api_key', e.target.value)} /><span>{t('仅补充 A 股个股资金流与筹码，不提供全市场行情或日线。')}</span></label>
        <label className="space-y-1 text-xs text-muted">{t('Tushare 兼容网关')}<Input placeholder={t('留空使用官方 HTTPS 接口')} value={value.tushare_http_url} onChange={(e) => patch('tushare_http_url', e.target.value)} /></label>
        <label className="space-y-1 text-xs text-muted">{t('TickFlow 日线复权')}<Select value={value.tickflow_kline_adjust} onChange={(e) => patch('tickflow_kline_adjust', e.target.value)}>
          {['none', 'forward', 'backward', 'forward_additive', 'backward_additive'].map((v) => <option key={v} value={v}>{t({ none: '不复权', forward: '前复权', backward: '后复权', forward_additive: '前复权（加法）', backward_additive: '后复权（加法）' }[v] || v)}</option>)}
        </Select></label>
        <label className="space-y-1 text-xs text-muted">{t('供应商超时（秒）')}<Input type="number" min={1} max={60} value={value.request_timeout_seconds} onChange={(e) => patch('request_timeout_seconds', Number(e.target.value))} /></label>
        <label className="space-y-1 text-xs text-muted">{t('数据集回退总预算（秒）')}<Input type="number" min={1} max={600} value={value.stage_timeout_seconds ?? 60} onChange={(e) => patch('stage_timeout_seconds', Number(e.target.value))} /></label>
        <label className="space-y-1 text-xs text-muted">{t('整条采集预算（秒）')}<Input type="number" min={1} max={900} value={value.collect_timeout_seconds ?? 240} onChange={(e) => patch('collect_timeout_seconds', Number(e.target.value))} /></label>
        <label className="space-y-1 text-xs text-muted">{t('全市场最低样本数')}<Input type="number" min={0} max={10000} value={value.minimum_realtime_rows} onChange={(e) => patch('minimum_realtime_rows', Number(e.target.value))} /></label>
      </div>
      <p className="mt-3 text-xs text-muted">{t('密钥显示为掩码，原样保存可保留；清空可删除。实时行情需要对应供应商权限，未配置密钥的源自动跳过。')}</p>
      <p className="mt-2 text-xs text-muted">{t('前复权与不复权日线可能来自不同来源，请结合个股页展示的实际口径使用。全市场样本数为 0 时关闭覆盖门槛。')}</p>
    </Card>
    <Button variant="primary" loading={saving} onClick={() => void save()}>{t('保存数据源配置')}</Button>
    <Card title={t('单源日线校验')}>
      <p className="mb-3 text-xs text-muted">{t('使用已保存的配置读取一只股票最近日线，记录健康状态，不写入行情库。')}</p>
      <div className="flex flex-wrap gap-2"><Select aria-label={t('校验来源')} value={probeSource} onChange={(e) => { setProbeSource(e.target.value); setProbeResult(null) }}>{initial.daily_options.map((name) => <option key={name} value={name}>{LABELS[name] || name}</option>)}</Select>
        <Input className="w-40" aria-label={t('校验股票代码')} value={probeCode} onChange={(e) => { setProbeCode(e.target.value); setProbeResult(null) }} />
        <Button disabled={saving || !probeCode.trim()} loading={probe.running} onClick={() => { setProbeResult(null); void probe.run(() => api.probeDataSource(probeSource, probeCode)).then(setProbeResult).catch(() => {}) }}>{t('校验日线数据')}</Button></div>
      {probeResult && <p className="mt-3 text-sm text-down">{t('已读取 {n} 根日线，最新交易日 {date}', { n: probeResult.bars, date: probeResult.latest_date })}</p>}
    </Card>
  </div>
}

export function DataSourceSettingsPanel() {
  const { data, error, reload, setData } = useApi(api.dataSourceSettings)
  if (error) return <ErrorBox message={error} onRetry={reload} />
  if (!data) return <Spinner />
  return <SettingsEditor initial={data} onSaved={setData} />
}
