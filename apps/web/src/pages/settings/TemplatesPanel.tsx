// 设置 → 报告模板：用 Jinja2 模板自定义诊断、仪表盘、复盘、日报的 Markdown；左侧编辑，右侧预览
import { useEffect, useState } from 'react'
import { api } from '@/api/endpoints'
import { Markdown } from '@/components/Markdown'
import { Button, Card, ErrorBox, Field, Select, Spinner, Textarea } from '@/components/ui'
import { useApi } from '@/hooks/useApi'
import { useT } from '@/i18n'
import { toast } from '@/stores/toast'

export function TemplatesPanel() {
  const t = useT()
  const list = useApi(api.reportTemplates)
  const [name, setName] = useState('')
  const [text, setText] = useState('')
  const [custom, setCustom] = useState(false)
  const [loaded, setLoaded] = useState(false)
  const [preview, setPreview] = useState<{ markdown: string; error?: string } | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (!name && list.data?.length) setName(list.data[0].name)
  }, [list.data, name])

  useEffect(() => {
    if (!name) return
    let cancelled = false
    setLoaded(false)
    setPreview(null)
    api.reportTemplate(name).then((r) => {
      if (cancelled) return
      setText(r.text)
      setCustom(r.custom)
      setLoaded(true)
    }).catch((e) => toast.error(e instanceof Error ? e.message : String(e)))
    return () => { cancelled = true }
  }, [name])

  async function runPreview() {
    setBusy(true)
    try {
      const r = await api.previewReportTemplate(name, text)
      setPreview({ markdown: r.markdown, error: r.ok ? undefined : r.error })
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  async function save() {
    setBusy(true)
    try {
      await api.saveReportTemplate(name, text)
      setCustom(true)
      toast.success(t('已保存'))
      void list.reload()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  async function restore() {
    setBusy(true)
    try {
      await api.deleteReportTemplate(name)
      const r = await api.reportTemplate(name)
      setText(r.text)
      setCustom(r.custom)
      setPreview(null)
      toast.success(t('已恢复内置'))
      void list.reload()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  if (list.error) return <ErrorBox message={list.error} onRetry={list.reload} />
  if (list.loading && !list.data) return <Spinner />

  return (
    <div className="space-y-3">
      <p className="text-sm text-muted">{t('模板使用 Jinja2 语法；没有自定义模板时使用内置格式；模板出错时自动回退内置格式')}</p>
      <Card>
        <Field label="选择模板">
          <Select aria-label={t('选择模板')} value={name} onChange={(e) => setName(e.target.value)}>
            {(list.data ?? []).map((x) => (
              <option key={x.name} value={x.name}>{t(x.label)}{x.custom ? ` (${t('自定义')})` : ''}</option>
            ))}
          </Select>
        </Field>
        <div className="mt-1 text-xs text-muted">{custom ? t('当前使用自定义模板') : t('当前使用内置格式，下方为示例模板')}</div>
      </Card>
      <div className="grid gap-3 lg:grid-cols-2">
        <Card title={t('模板')}>
          <Textarea aria-label={t('模板内容')} className="min-h-96 font-mono text-xs" value={text} disabled={!loaded}
            onChange={(e) => setText(e.target.value)} />
          <div className="mt-2 flex gap-2">
            <Button onClick={() => void runPreview()} disabled={!loaded || busy}>{t('预览')}</Button>
            <Button variant="primary" onClick={() => void save()} disabled={!loaded || busy}>{t('保存')}</Button>
            <Button onClick={() => void restore()} disabled={!loaded || busy || !custom}>{t('恢复内置')}</Button>
          </div>
        </Card>
        <Card title={t('预览')}>
          {!preview ? <div className="text-sm text-muted">{t('点击「预览」查看渲染结果')}</div>
            : preview.error ? <ErrorBox message={preview.error} />
            : <Markdown text={preview.markdown} />}
        </Card>
      </div>
    </div>
  )
}
