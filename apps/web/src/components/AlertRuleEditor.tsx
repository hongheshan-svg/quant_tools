// 提醒规则的新增/编辑弹窗：选股票、选类型，再按类型的字段动态渲染输入
import { useEffect, useState } from 'react'
import type { AlertRule, AlertRuleType, StockRef } from '@/api/types'
import { StockSearch } from '@/components/StockSearch'
import { Button, Field, Input, Modal, Select } from '@/components/ui'
import { t as tr, useT } from '@/i18n'

/** 类型的字段默认值（没有默认值的字段留空） */
export function defaultsOf(type: AlertRuleType | undefined): Record<string, string | number> {
  const out: Record<string, string | number> = {}
  for (const f of type?.fields ?? []) if (f.default !== undefined && f.default !== null) out[f.key] = f.default
  return out
}

/** 一句话描述规则条件（表格里显示） */
export function describeRule(rule: AlertRule, types: Record<string, AlertRuleType>): string {
  const def = types[rule.type]
  if (!def) return rule.type
  const parts = def.fields.map((f) => {
    const v = rule[f.key]
    if (v === undefined || v === '') return ''
    const label = f.options?.find(([value]) => value === v)?.[1]
    return label ? tr(label) : `${tr(f.label).replace(/\s*[（(].*[）)]/, '')} ${v}`
  })
  return tr('{label}：{parts}', { label: tr(def.label), parts: parts.filter(Boolean).join(tr('，')) })
}

export function AlertRuleEditor({ open, initial, initialName, types, onClose, onSubmit }: {
  open: boolean
  initial: AlertRule | null
  initialName?: string
  types: Record<string, AlertRuleType>
  onClose: () => void
  onSubmit: (rule: AlertRule, name: string) => void
}) {
  const t = useT()
  const typeKeys = Object.keys(types)
  const [stock, setStock] = useState<StockRef | null>(null)
  const [type, setType] = useState('')
  const [values, setValues] = useState<Record<string, string | number>>({})
  const [note, setNote] = useState('')
  const [error, setError] = useState('')

  useEffect(() => {
    if (!open) return
    const first = initial?.type ?? typeKeys[0] ?? ''
    setStock(initial ? { code: initial.code, name: initialName ?? '' } : null)
    setType(first)
    setValues(initial ? Object.fromEntries(types[first]?.fields.map((f) => [f.key, initial[f.key] as string | number]).filter(([, v]) => v !== undefined) ?? []) : defaultsOf(types[first]))
    setNote(initial?.note ?? '')
    setError('')
    // 只在打开时初始化
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open])

  const def = types[type]
  const changeType = (next: string) => {
    setType(next)
    setValues(defaultsOf(types[next]))
  }
  const submit = () => {
    if (!stock) return setError(t('请先选择股票'))
    for (const f of def?.fields ?? []) {
      if (f.type === 'number' && !(Number(values[f.key]) > 0)) return setError(t('{label}必须大于 0', { label: t(f.label) }))
    }
    const rule: AlertRule = { code: stock.code, type, enabled: initial?.enabled ?? true, note: note.trim() }
    for (const f of def?.fields ?? []) rule[f.key] = f.type === 'number' ? Number(values[f.key]) : values[f.key]
    onSubmit(rule, stock.name)
  }

  return (
    <Modal
      open={open}
      title={initial ? t('编辑规则') : t('新增规则')}
      onClose={onClose}
      footer={
        <>
          <Button onClick={onClose}>{t('取消')}</Button>
          <Button variant="primary" onClick={submit}>{t('确定')}</Button>
        </>
      }
    >
      <div className="space-y-3">
        <Field label={t('股票')}>
          {stock ? (
            <div className="flex items-center justify-between rounded-md border border-line px-2.5 py-1.5 text-sm">
              <span>{stock.name} <span className="num text-xs text-muted">{stock.code}</span></span>
              {!initial && <button type="button" className="text-xs text-muted hover:text-text" onClick={() => setStock(null)}>{t('重选')}</button>}
            </div>
          ) : (
            <StockSearch onSelect={setStock} autoFocus />
          )}
        </Field>
        <Field label={t('规则类型')}>
          <Select value={type} onChange={(e) => changeType(e.target.value)} className="w-full" aria-label={t('规则类型')}>
            {typeKeys.map((k) => <option key={k} value={k}>{t(types[k].label)}</option>)}
          </Select>
        </Field>
        {def?.fields.map((f) => (
          <Field key={f.key} label={t(f.label)}>
            {f.type === 'select' ? (
              <Select value={String(values[f.key] ?? '')} onChange={(e) => setValues({ ...values, [f.key]: e.target.value })} className="w-full" aria-label={t(f.label)}>
                {f.options?.map(([v, text]) => <option key={v} value={v}>{t(text)}</option>)}
              </Select>
            ) : (
              <Input type="number" step="any" min="0" aria-label={t(f.label)} value={values[f.key] ?? ''} onChange={(e) => setValues({ ...values, [f.key]: e.target.value })} />
            )}
          </Field>
        ))}
        <Field label={t('备注（可空）')}>
          <Input value={note} onChange={(e) => setNote(e.target.value)} maxLength={60} aria-label={t('备注')} />
        </Field>
        {error && <p className="text-sm text-danger">{error}</p>}
      </div>
    </Modal>
  )
}
