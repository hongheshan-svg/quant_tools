// 自选股页：决策仪表盘设置（每日推送、数量上限、并发数、逐只推送、总时长上限）
import { useEffect, useState } from 'react'
import { api } from '@/api/endpoints'
import type { WatchlistSettings as Settings } from '@/api/types'
import { Button, Field, Input, Modal } from '@/components/ui'
import { useT } from '@/i18n'
import { toast } from '@/stores/toast'

export function WatchlistSettings({ open, onClose }: { open: boolean; onClose: () => void }) {
  const t = useT()
  const [form, setForm] = useState<Settings | null>(null)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    if (!open) return
    api.watchlistSettings().then(setForm).catch((e) => toast.error(e instanceof Error ? e.message : String(e)))
  }, [open])

  const num = (key: keyof Settings) => (e: { target: { value: string } }) => setForm((f) => (f ? { ...f, [key]: Number(e.target.value) } : f))

  async function save() {
    if (!form) return
    setSaving(true)
    try {
      const { daily_report, max_stocks, workers, single_notify, timeout_minutes } = form
      await api.saveWatchlistSettings({ daily_report, max_stocks, workers, single_notify, timeout_minutes })
      toast.success(t('已保存'))
      onClose()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e))
    } finally {
      setSaving(false)
    }
  }

  return (
    <Modal open={open} title={t('仪表盘设置')} onClose={onClose} footer={<Button variant="primary" loading={saving} disabled={!form} onClick={() => void save()}>{t('保存')}</Button>}>
      {form && (
        <div className="space-y-3">
          <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={form.daily_report} onChange={(e) => setForm({ ...form, daily_report: e.target.checked })} /> {t('收盘后自动生成并推送仪表盘')}</label>
          <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={form.single_notify} onChange={(e) => setForm({ ...form, single_notify: e.target.checked })} /> {t('每只诊断完成后立即推送一条')}</label>
          <Field label={t('自选股上限（1~500）')}><Input type="number" min={1} max={500} value={form.max_stocks} onChange={num('max_stocks')} /></Field>
          <Field label={t('同时诊断的股票数（1~10）')}><Input type="number" min={1} max={10} value={form.workers} onChange={num('workers')} /></Field>
          <Field label={t('总时长上限（分钟，0 表示不限）')} hint={t('超时后推送已完成的部分')}><Input type="number" min={0} max={600} value={form.timeout_minutes} onChange={num('timeout_minutes')} /></Field>
        </div>
      )}
    </Modal>
  )
}
