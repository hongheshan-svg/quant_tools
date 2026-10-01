// 设置 → AI 模型：AI 输出语言（诊断、复盘、问股、深度研究和推送报告）；切换后立即保存
import { useEffect, useState } from 'react'
import { api } from '@/api/endpoints'
import type { ReportLanguage } from '@/api/types'
import { Card, Field, Select } from '@/components/ui'
import { useApi } from '@/hooks/useApi'
import { useT } from '@/i18n'
import { toast } from '@/stores/toast'

export function ReportLanguageSection() {
  const t = useT()
  const { data } = useApi(api.reportSettings)
  const [language, setLanguage] = useState<ReportLanguage>('zh')
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    if (data) setLanguage(data.language === 'en' ? 'en' : 'zh')
  }, [data])

  async function change(next: ReportLanguage) {
    const previous = language
    setLanguage(next)
    setSaving(true)
    try {
      await api.saveReportSettings(next)
      toast.success(t('已保存'))
    } catch (e) {
      setLanguage(previous)
      toast.error(e instanceof Error ? e.message : String(e))
    } finally {
      setSaving(false)
    }
  }

  return (
    <Card className="mt-3">
      <Field label="AI 输出语言" hint="影响诊断、复盘、问股、深度研究和推送报告；界面语言在右上角切换">
        <Select value={language} disabled={saving} onChange={(e) => void change(e.target.value as ReportLanguage)}>
          <option value="zh">{t('中文')}</option>
          <option value="en">English</option>
        </Select>
      </Field>
    </Card>
  )
}
