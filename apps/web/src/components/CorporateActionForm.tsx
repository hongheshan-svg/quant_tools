// 记一笔分红送转：按方案（每 10 股派息/送股/转增，按除权日前持仓计算）或按到账（直接填金额、股数）
import { Field, Input, Select, Tabs } from '@/components/ui'

export type ActionMode = 'plan' | 'direct'

export interface ActionForm {
  mode: ActionMode
  ex_date: string
  code: string
  note: string
  cash_per_10: string
  bonus_per_10: string
  transfer_per_10: string
  tax_rate: string
  action: string
  cash: string
  shares: string
}

export const emptyActionForm = (): ActionForm => ({
  mode: 'plan', ex_date: new Date().toISOString().slice(0, 10), code: '', note: '',
  cash_per_10: '0', bonus_per_10: '0', transfer_per_10: '0', tax_rate: '0', action: 'dividend', cash: '', shares: '',
})

/** 表单 -> 接口请求体 */
export function actionBody(f: ActionForm): Record<string, unknown> {
  const base = { ex_date: f.ex_date, code: f.code.trim(), note: f.note }
  if (f.mode === 'plan') {
    return {
      ...base,
      plan: {
        cash_per_10: Number(f.cash_per_10 || 0), bonus_per_10: Number(f.bonus_per_10 || 0),
        transfer_per_10: Number(f.transfer_per_10 || 0), tax_rate: Number(f.tax_rate || 0),
      },
    }
  }
  return { ...base, action: f.action, cash: Number(f.cash || 0), shares: Number(f.shares || 0) }
}

export function CorporateActionForm({ value, onChange }: { value: ActionForm; onChange: (v: ActionForm) => void }) {
  const set = (patch: Partial<ActionForm>) => onChange({ ...value, ...patch })
  return (
    <div>
      <Tabs
        tabs={[{ key: 'plan', label: '按方案' }, { key: 'direct', label: '按到账' }]}
        value={value.mode}
        onChange={(mode) => set({ mode })}
      />
      <div className="grid grid-cols-2 gap-3">
        <Field label="除权除息日"><Input type="date" value={value.ex_date} onChange={(e) => set({ ex_date: e.target.value })} /></Field>
        <Field label="股票"><Input value={value.code} placeholder="股票代码 / 名称 / 拼音" onChange={(e) => set({ code: e.target.value })} /></Field>
        {value.mode === 'plan' ? (
          <>
            <Field label="每 10 股派现（元，税前）"><Input type="number" step="0.01" value={value.cash_per_10} onChange={(e) => set({ cash_per_10: e.target.value })} /></Field>
            <Field label="每 10 股送股"><Input type="number" step="0.1" value={value.bonus_per_10} onChange={(e) => set({ bonus_per_10: e.target.value })} /></Field>
            <Field label="每 10 股转增"><Input type="number" step="0.1" value={value.transfer_per_10} onChange={(e) => set({ transfer_per_10: e.target.value })} /></Field>
            <Field label="红利税率（0~1）" hint="已扣税到账时填，如 0.1；默认 0">
              <Input type="number" step="0.01" value={value.tax_rate} onChange={(e) => set({ tax_rate: e.target.value })} />
            </Field>
          </>
        ) : (
          <>
            <Field label="类型">
              <Select className="w-full" value={value.action} onChange={(e) => set({ action: e.target.value })}>
                <option value="dividend">现金分红到账</option>
                <option value="bonus">送转股到账</option>
                <option value="tax">红利税补缴</option>
              </Select>
            </Field>
            {value.action === 'bonus' ? (
              <Field label="到账股数"><Input type="number" step="1" value={value.shares} onChange={(e) => set({ shares: e.target.value })} /></Field>
            ) : (
              <Field label="金额（元）"><Input type="number" step="0.01" value={value.cash} onChange={(e) => set({ cash: e.target.value })} /></Field>
            )}
          </>
        )}
        <Field label="备注"><Input value={value.note} onChange={(e) => set({ note: e.target.value })} /></Field>
      </div>
      {value.mode === 'plan' && <p className="mt-2 text-xs text-muted">按除权日前一刻的持仓数量计算，送转股向下取整；没有持仓时无法生成。</p>}
    </div>
  )
}
