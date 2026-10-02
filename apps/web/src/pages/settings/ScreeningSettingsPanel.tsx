import { useState } from 'react'
import { api } from '@/api/endpoints'
import type { ScreeningSettings } from '@/api/types'
import { Button, Card, ErrorBox, Input, Spinner } from '@/components/ui'
import { useApi } from '@/hooks/useApi'
import { useT } from '@/i18n'
import { toast } from '@/stores/toast'

const LABELS: Record<string, string> = { value: '估值', quality: '盈利质量', liquidity: '流动性', momentum: '动量', activity: '活跃度', stability: '稳定性', reversal: '反转', size: '规模', theme_heat: '题材热度' }

function Editor({ initial }: { initial: ScreeningSettings }) {
  const t = useT()
  const [form, setForm] = useState(initial)
  const [saving, setSaving] = useState(false)
  const pipeline = form.screening.pipeline ?? {}
  const patch = (key: string, value: number | boolean) => setForm({ ...form, screening: { ...form.screening, pipeline: { ...pipeline, [key]: value } } })
  return <div className="space-y-4">
    <Card title={t('候选排序与风险')}>
      <div className="grid gap-3 sm:grid-cols-2">
        {(['enabled', 'financial_enrichment', 'llm_rerank'] as const).map((key, i) => <label key={key} className="flex gap-2 text-sm"><input type="checkbox" checked={Boolean(pipeline[key])} onChange={(e) => patch(key, e.target.checked)} />{t(['启用多因子排序', '候选财务补数', '模型比较排序'][i])}</label>)}
        {(['financial_candidates', 'financial_timeout_seconds', 'llm_top_k', 'llm_timeout_seconds', 'post_analysis_top_k', 'risk_max_penalty', 'risk_veto_threshold', 'max_same_bucket', 'concentration_penalty'] as const).map((key, i) => <label key={key} className="text-xs text-muted">{t(['财务候选数', '财务补数预算', '模型候选数', '模型排序预算', '深度复核数量', '最大风险扣分', '风险否决阈值', '相同风险桶数量', '集中度扣分'][i])}<Input type="number" min={0} value={Number(pipeline[key] ?? 0)} onChange={(e) => patch(key, Number(e.target.value))} /></label>)}
      </div>
    </Card>
    <Card title={t('多因子策略权重')}>
      <p className="mb-3 text-xs text-muted">{t('权重自动归一化；缺失因子显示覆盖率，风险扣分独立于模型评分。')}</p>
      {form.profiles.map((profile, index) => <details key={profile.name} className="mb-2 rounded border border-line p-3"><summary className="cursor-pointer text-sm">{profile.label ?? profile.name}</summary><label className="mt-3 flex gap-2 text-sm"><input type="checkbox" checked={profile.enabled !== false} onChange={(e) => setForm({ ...form, profiles: form.profiles.map((p, i) => i === index ? { ...p, enabled: e.target.checked } : p) })} />{t('启用')}</label><div className="mt-3 flex flex-wrap gap-3">{Object.entries(profile.weights).map(([factor, weight]) => <label key={factor} className="w-28 text-xs text-muted">{t(LABELS[factor] ?? factor)}<Input type="number" min={0} step={0.05} value={weight} onChange={(e) => setForm({ ...form, profiles: form.profiles.map((p, i) => i === index ? { ...p, weights: { ...p.weights, [factor]: Number(e.target.value) } } : p) })} /></label>)}</div></details>)}
    </Card>
    <Button variant="primary" loading={saving} onClick={async () => { setSaving(true); try { setForm(await api.saveScreeningSettings(form)); toast.success(t('选股设置已保存')) } catch (e) { toast.error(e instanceof Error ? e.message : String(e)) } finally { setSaving(false) } }}>{t('保存')}</Button>
  </div>
}

export function ScreeningSettingsPanel() {
  const { data, error, reload } = useApi(api.screeningSettings)
  if (error) return <ErrorBox message={error} onRetry={reload} />
  return data ? <Editor initial={data} /> : <Spinner />
}
