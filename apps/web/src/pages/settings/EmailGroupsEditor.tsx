// 设置 → 推送 → 邮件：按股票分组额外发给其他收件人（只用于自选股决策仪表盘和逐只推送）
import { useEffect, useRef, useState } from 'react'
import type { EmailGroup } from '@/api/types'
import { Button, Input } from '@/components/ui'
import { useT } from '@/i18n'

interface Row {
  id: number
  name: string
  stocks: string
  to: string
}

const split = (text: string) => text.split(/[,，;；\s]+/).map((x) => x.trim()).filter(Boolean)
const toRow = (g: EmailGroup, id: number): Row => ({ id, name: g.name ?? '', stocks: (g.stocks ?? []).join(', '), to: (g.to ?? []).join(', ') })
const toGroup = (r: Row): EmailGroup => ({ name: r.name.trim(), stocks: split(r.stocks), to: split(r.to) })

export function EmailGroupsEditor({ groups, onChange }: { groups: EmailGroup[]; onChange: (groups: EmailGroup[]) => void }) {
  const t = useT()
  const nextId = useRef(0)
  const [rows, setRows] = useState<Row[]>(() => groups.map((g) => toRow(g, nextId.current++)))

  // 外部（加载/重置）传入的分组与当前编辑内容不一致时，以外部为准
  useEffect(() => {
    if (JSON.stringify(rows.map(toGroup)) !== JSON.stringify(groups)) setRows(groups.map((g) => toRow(g, nextId.current++)))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [groups])

  const update = (next: Row[]) => {
    setRows(next)
    onChange(next.map(toGroup))
  }
  const edit = (id: number, patch: Partial<Row>) => update(rows.map((r) => (r.id === id ? { ...r, ...patch } : r)))

  return (
    <div className="space-y-2 md:col-span-3">
      <div className="flex items-center justify-between">
        <span className="text-xs text-muted">{t('分组（按股票额外发给其他收件人，用于自选股决策仪表盘和逐只推送）')}</span>
        <Button onClick={() => update([...rows, { id: nextId.current++, name: '', stocks: '', to: '' }])}>{t('添加分组')}</Button>
      </div>
      {rows.map((r, i) => (
        <div key={r.id} className="grid gap-2 md:grid-cols-[1fr_2fr_2fr_auto]">
          <Input aria-label={`${t('组名')} ${i + 1}`} placeholder={t('组名')} value={r.name} onChange={(e) => edit(r.id, { name: e.target.value })} />
          <Input aria-label={`${t('股票代码（逗号分隔）')} ${i + 1}`} placeholder={t('股票代码（逗号分隔）')} value={r.stocks} onChange={(e) => edit(r.id, { stocks: e.target.value })} />
          <Input aria-label={`${t('收件人（逗号分隔）')} ${i + 1}`} placeholder={t('收件人（逗号分隔）')} value={r.to} onChange={(e) => edit(r.id, { to: e.target.value })} />
          <Button aria-label={`${t('删除分组')} ${i + 1}`} onClick={() => update(rows.filter((x) => x.id !== r.id))}>{t('删除')}</Button>
        </div>
      ))}
    </div>
  )
}
