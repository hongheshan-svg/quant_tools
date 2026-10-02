// 独立资讯源及单源拉取；原有 YAML 源仍在设置页管理。
import { useState } from 'react'
import { api } from '@/api/endpoints'
import { Button, Card, ErrorBox, Field, Input, PageHeader, Spinner } from '@/components/ui'
import { useApi } from '@/hooks/useApi'
import { useTask } from '@/hooks/useTask'
import { useT } from '@/i18n'
import { toast } from '@/stores/toast'

export function IntelligencePage() {
  const t = useT()
  const sources = useApi(api.intelligenceSources)
  const { run, running } = useTask()
  const [form, setForm] = useState({ name: '', url: '', symbol: '', sector: '' })
  const [saving, setSaving] = useState(false)
  return <div>
    <PageHeader title={t('资讯源管理')} description={t('为订阅源设置股票或行业范围，查看拉取状态和失败原因')} />
    <Card title={t('添加资讯源')} className="mb-4">
      <div className="grid gap-3 md:grid-cols-2">{(['name', 'url', 'symbol', 'sector'] as const).map((key) => <Field key={key} label={t({ name: '名称', url: '订阅地址', symbol: '股票代码（可空）', sector: '行业（可空）' }[key])}><Input value={form[key]} onChange={(e) => setForm({ ...form, [key]: e.target.value })} /></Field>)}</div>
      <Button className="mt-3" loading={saving} disabled={!form.name || !form.url} onClick={async () => {
        setSaving(true)
        try { await api.saveIntelligenceSource({ ...form, symbol: form.symbol || null, sector: form.sector || null, enabled: true, market: 'CN' }); setForm({ name: '', url: '', symbol: '', sector: '' }); await sources.reload(); toast.success(t('已保存')) }
        catch (e) { toast.error(e instanceof Error ? e.message : String(e)) } finally { setSaving(false) }
      }}>{t('添加')}</Button>
    </Card>
    {sources.error && <ErrorBox message={sources.error} onRetry={sources.reload} />}
    {sources.loading && !sources.data && <Spinner />}
    <div className="space-y-3">{sources.data?.map((s) => <Card key={s.id} title={s.name}>
      <p className="break-all text-xs text-muted">{s.url}</p><p className="mt-1 text-sm">{s.symbol || t('全市场')} {s.sector} · {s.last_fetched_at || t('尚未拉取')}</p>
      {s.last_error && <p className="text-sm text-danger">{s.last_error}</p>}
      <div className="mt-2 flex gap-2"><Button disabled={running} onClick={async () => { try { await run(() => api.fetchIntelligenceSource(s.id), { success: t('拉取完成') }); await sources.reload() } catch { /* useTask 已提示 */ } }}>{t('拉取此源')}</Button>
        {s.managed_by_config ? <span className="text-xs text-muted">{t('在资讯源设置中编辑')}</span> : <Button onClick={async () => { try { await api.saveIntelligenceSource({ ...s, enabled: !s.enabled }, s.id); await sources.reload() } catch (e) { toast.error(e instanceof Error ? e.message : String(e)) } }}>{s.enabled ? t('停用') : t('启用')}</Button>}
      </div>
    </Card>)}</div>
  </div>
}
