// 设置 → AI 模型：诊断设置（决策风格、多智能体模式、股东数据、历史校准、信号复盘、多策略会诊）
import { useEffect, useState } from 'react'
import { api } from '@/api/endpoints'
import type { DecisionProfile, DiagnosisSettings } from '@/api/types'
import { Button, Card, Field, Input, Select } from '@/components/ui'
import { useApi } from '@/hooks/useApi'
import { useT } from '@/i18n'
import { toast } from '@/stores/toast'

const PROFILES: { key: DecisionProfile; label: string; hint: string }[] = [
  { key: 'conservative', label: '保守', hint: '评分 65 以上才买入，数据完整度 75% 以上，大盘防守不开新仓，信心低不买，买入必须有失效条件或止损价' },
  { key: 'balanced', label: '均衡', hint: '评分 50 以上、数据完整度 60% 以上才买入（默认）' },
  { key: 'aggressive', label: '进取', hint: '评分 45 以上、数据完整度 50% 以上即可买入，资金净流出容忍到 8%，买入必须有失效条件或止损价' },
]
const DEFAULTS: DiagnosisSettings = {
  decision_profile: 'balanced', mode: 'single', shareholders: true, calibration: true, signal_review: true,
  skill_consult: { enabled: false, max_skills: 2 },
}

function Toggle({ label, hint, checked, onChange }: { label: string; hint: string; checked: boolean; onChange: (v: boolean) => void }) {
  const t = useT()
  return (
    <label className="flex items-start gap-2 text-sm">
      <input type="checkbox" className="mt-1" checked={checked} onChange={(e) => onChange(e.target.checked)} />
      <span>{t(label)}<span className="block text-xs text-muted">{t(hint)}</span></span>
    </label>
  )
}

export function DiagnosisSettingsSection() {
  const t = useT()
  const { data } = useApi(api.diagnosisSettings)
  const [form, setForm] = useState<DiagnosisSettings>(DEFAULTS)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    if (data?.diagnosis) setForm({ ...DEFAULTS, ...data.diagnosis, skill_consult: { ...DEFAULTS.skill_consult, ...data.diagnosis.skill_consult } })
  }, [data])

  const set = (patch: Partial<DiagnosisSettings>) => setForm((f) => ({ ...f, ...patch }))

  async function save() {
    setSaving(true)
    try {
      const r = await api.saveDiagnosisSettings(form)
      if (r?.diagnosis) setForm(r.diagnosis)
      toast.success(t('已保存'))
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e))
    } finally {
      setSaving(false)
    }
  }

  return (
    <Card className="mt-3" title={t('诊断设置')}>
      <div className="space-y-4">
        <div>
          <div className="mb-1 text-xs text-muted">{t('决策风格')}</div>
          <div className="grid gap-2 md:grid-cols-3">
            {PROFILES.map((p) => (
              <label key={p.key} className="flex cursor-pointer items-start gap-2 rounded-md border border-line p-2 text-sm has-[:checked]:border-accent">
                <input type="radio" name="decision_profile" className="mt-1" checked={form.decision_profile === p.key} onChange={() => set({ decision_profile: p.key })} />
                <span>{t(p.label)}<span className="block text-xs text-muted">{t(p.hint)}</span></span>
              </label>
            ))}
          </div>
          <p className="mt-1 text-xs text-muted">{t('同一份诊断可以在诊断详情里按其他风格重新评估，不会重新调用 AI。大盘冰点不开仓、核心数据不足、严重风险公告等安全护栏对所有风格都有效。')}</p>
        </div>
        <Field label="多智能体模式" hint="single 一次调用；standard 技术面与情报分析员先分析再决策；full 再加风险分析员（调用次数更多）">
          <Select value={form.mode} onChange={(e) => set({ mode: e.target.value as DiagnosisSettings['mode'] })}>
            <option value="single">single</option>
            <option value="standard">standard</option>
            <option value="full">full</option>
          </Select>
        </Field>
        <div className="grid gap-3 md:grid-cols-2">
          <Toggle label="股东数据" hint="诊断时获取股东户数变化、十大流通股东和机构持仓" checked={form.shareholders} onChange={(v) => set({ shareholders: v })} />
          <Toggle label="历史校准" hint="把近 90 天诊断的事后准确率写进提示词" checked={form.calibration} onChange={(v) => set({ calibration: v })} />
          <Toggle label="信号复盘" hint="把该股历史决策信号的复盘写进提示词" checked={form.signal_review} onChange={(v) => set({ signal_review: v })} />
          <Toggle label="多策略会诊" hint="按个股特征挑选策略各给观点，每次诊断多几次模型调用" checked={form.skill_consult.enabled}
            onChange={(v) => set({ skill_consult: { ...form.skill_consult, enabled: v } })} />
        </div>
        <Field label="会诊策略数量" hint="1~5 个">
          <Input type="number" min={1} max={5} className="w-24" value={form.skill_consult.max_skills}
            onChange={(e) => set({ skill_consult: { ...form.skill_consult, max_skills: Math.min(5, Math.max(1, Number(e.target.value) || 1)) } })} />
        </Field>
        <div className="flex justify-end"><Button variant="primary" loading={saving} onClick={() => void save()}>{t('保存诊断设置')}</Button></div>
      </div>
    </Card>
  )
}
