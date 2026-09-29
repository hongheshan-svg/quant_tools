import { useEffect, useState } from 'react'
import { api } from '@/api/endpoints'
import type { AlertRow, AlertRule, AlertSettings } from '@/api/types'
import { AlertRuleEditor, describeRule } from '@/components/AlertRuleEditor'
import { DataTable, type Column } from '@/components/DataTable'
import { Badge, Button, Card, ErrorBox, Field, Input, PageHeader, Spinner, Tabs } from '@/components/ui'
import { useApi } from '@/hooks/useApi'
import { useTask } from '@/hooks/useTask'
import { toast } from '@/stores/toast'

const LEVEL: Record<string, [string, 'down' | 'warn' | 'accent']> = { critical: ['紧急', 'down'], warning: ['注意', 'warn'], info: ['提示', 'accent'] }

type TabKey = 'records' | 'rules' | 'settings'

export function AlertsPage() {
  const [tab, setTab] = useState<TabKey>('records')
  const { data, error, loading, reload } = useApi(api.alerts)
  const check = useTask<{ alerts: number; skipped?: string }>()
  const columns: Column<AlertRow>[] = [
    { key: 'time', title: '时间', render: (r) => <span className="num text-xs">{r.time}</span> },
    { key: 'stock', title: '股票', render: (r) => <>{r.name} <span className="num text-xs text-muted">{r.code}</span></> },
    { key: 'type', title: '类型', render: (r) => r.type },
    { key: 'level', title: '级别', render: (r) => <Badge tone={(LEVEL[r.severity] ?? ['', 'accent'])[1]}>{(LEVEL[r.severity] ?? [r.severity])[0]}</Badge> },
    { key: 'message', title: '内容', className: 'max-w-lg', render: (r) => r.message },
    { key: 'pushed', title: '推送', render: (r) => (r.notified ? <span className="text-down">已推送</span> : <span className="text-xs text-muted">{r.reason || '未推送'}</span>) },
  ]
  return (
    <div>
      <PageHeader
        title="盘中提醒"
        description="交易时段内每次采集后检查今日信号股、持仓、自选股：封涨停、炸板、跌破/接近止损、目标价、大跌、大盘转弱和自定义技术指标规则"
        actions={
          <Button loading={check.running} onClick={() => check.run(api.checkAlerts, { success: (r) => (r.skipped ? `已跳过：${r.skipped}` : `新增 ${r.alerts} 条提醒`) }).then(reload).catch(() => {})}>
            立即检查
          </Button>
        }
      />
      <Tabs<TabKey>
        tabs={[{ key: 'records', label: '提醒记录' }, { key: 'rules', label: '提醒规则' }, { key: 'settings', label: '提醒设置' }]}
        value={tab}
        onChange={setTab}
      />
      {tab === 'records' && (
        <>
          {error && <ErrorBox message={error} onRetry={reload} />}
          <Card bodyClassName="p-0">
            <DataTable columns={columns} rows={data ?? []} rowKey={(r, i) => `${r.time}-${r.code}-${i}`} empty={loading ? '加载中…' : '还没有提醒记录'} maxHeight="75vh" />
          </Card>
        </>
      )}
      {tab === 'rules' && <RulesPanel />}
      {tab === 'settings' && <SettingsPanel />}
    </div>
  )
}

/** 提醒规则：本地编辑，点「保存」整体提交 */
function RulesPanel() {
  const { data, error, loading, reload } = useApi(api.alertRules)
  const [rules, setRules] = useState<AlertRule[]>([])
  const [names, setNames] = useState<Record<string, string>>({})
  const [dirty, setDirty] = useState(false)
  const [saving, setSaving] = useState(false)
  const [editing, setEditing] = useState<{ index: number; rule: AlertRule | null } | null>(null)
  const [results, setResults] = useState<Record<number, string>>({})
  const [testing, setTesting] = useState<number | null>(null)

  useEffect(() => {
    if (data) {
      setRules(data.rules)
      setDirty(false)
      setResults({})
    }
  }, [data])

  useEffect(() => {
    // 规则里只有代码，名称按代码搜索补上（失败就只显示代码）
    for (const code of new Set(rules.map((r) => r.code))) {
      if (names[code] !== undefined) continue
      setNames((n) => ({ ...n, [code]: '' }))
      api.searchStocks(code, 1).then((found) => {
        const hit = found.find((f) => f.code === code)
        if (hit) setNames((n) => ({ ...n, [code]: hit.name }))
      }).catch(() => {})
    }
  }, [rules, names])

  if (error) return <ErrorBox message={error} onRetry={reload} />
  if (!data) return loading ? <Spinner /> : null
  const types = data.types

  const update = (next: AlertRule[]) => {
    setRules(next)
    setDirty(true)
    setResults({})
  }
  const save = async () => {
    setSaving(true)
    try {
      const r = await api.saveAlertRules(rules)
      setRules(r.rules)
      setDirty(false)
      toast.success('提醒规则已保存')
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e))
    } finally {
      setSaving(false)
    }
  }
  const test = async (index: number) => {
    setTesting(index)
    try {
      const r = await api.testAlertRule(rules[index])
      setResults((x) => ({ ...x, [index]: `${r.triggered ? '已触发：' : '未触发：'}${r.message}` }))
    } catch (e) {
      setResults((x) => ({ ...x, [index]: e instanceof Error ? e.message : String(e) }))
    } finally {
      setTesting(null)
    }
  }

  const columns: Column<AlertRule>[] = [
    { key: 'stock', title: '股票', render: (r) => <>{names[r.code] || '-'} <span className="num text-xs text-muted">{r.code}</span></> },
    { key: 'type', title: '类型', render: (r) => types[r.type]?.label ?? r.type },
    { key: 'cond', title: '条件', render: (r) => describeRule(r, types) },
    {
      key: 'enabled',
      title: '启用',
      render: (r) => {
        const i = rules.indexOf(r)
        return <input type="checkbox" aria-label="启用" checked={r.enabled !== false} onChange={(e) => update(rules.map((x, j) => (j === i ? { ...x, enabled: e.target.checked } : x)))} />
      },
    },
    { key: 'note', title: '备注', render: (r) => <span className="text-xs text-muted">{r.note || '-'}</span> },
    {
      key: 'ops',
      title: '操作',
      render: (r) => {
        const i = rules.indexOf(r)
        return (
          <div className="space-y-1">
            <div className="flex gap-1">
              <Button loading={testing === i} onClick={() => test(i)}>测试</Button>
              <Button onClick={() => setEditing({ index: i, rule: r })}>编辑</Button>
              <Button variant="danger" onClick={() => update(rules.filter((_, j) => j !== i))}>删除</Button>
            </div>
            {results[i] && <div className="max-w-xs text-xs text-muted">{results[i]}</div>}
          </div>
        )
      },
    },
  ]

  return (
    <>
      <Card
        title="自定义提醒规则"
        actions={
          <div className="flex gap-2">
            <Button onClick={() => setEditing({ index: -1, rule: null })}>新增规则</Button>
            <Button variant="primary" loading={saving} disabled={!dirty} onClick={save}>保存</Button>
          </div>
        }
        bodyClassName="p-0"
      >
        <DataTable columns={columns} rows={rules} rowKey={(_, i) => String(i)} empty="还没有规则，点右上角「新增规则」" />
      </Card>
      {dirty && <p className="mt-2 text-xs text-muted">有未保存的修改，点「保存」后才会生效</p>}
      <AlertRuleEditor
        open={editing !== null}
        initial={editing?.rule ?? null}
        initialName={editing?.rule ? names[editing.rule.code] : undefined}
        types={types}
        onClose={() => setEditing(null)}
        onSubmit={(rule, name) => {
          if (name) setNames((n) => ({ ...n, [rule.code]: name }))
          if (editing && editing.index >= 0) update(rules.map((x, j) => (j === editing.index ? { ...x, ...rule } : x)))
          else update([...rules, rule])
          setEditing(null)
        }}
      />
    </>
  )
}

/** 提醒设置：冷却时间、阈值、开关和额外关注的股票 */
function SettingsPanel() {
  const { data, error, loading, reload } = useApi(api.alertSettings)
  const [form, setForm] = useState<AlertSettings | null>(null)
  const [watch, setWatch] = useState('')
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    if (data) {
      setForm(data)
      setWatch(data.watchlist.join(', '))
    }
  }, [data])

  if (error) return <ErrorBox message={error} onRetry={reload} />
  if (!form) return loading ? <Spinner /> : null

  const num = (key: 'cooldown_minutes' | 'big_drop_pct' | 'near_stop_pct' | 'regime_score_drop', label: string, hint: string) => (
    <Field label={label} hint={hint}>
      <Input type="number" step="any" aria-label={label} value={form[key]} onChange={(e) => setForm({ ...form, [key]: e.target.value === '' ? NaN : Number(e.target.value) })} />
    </Field>
  )
  const save = async () => {
    setSaving(true)
    try {
      const watchlist = watch.split(/[,，、\s]+/).filter(Boolean)
      const saved = await api.saveAlertSettings({ ...form, watchlist })
      setForm(saved)
      setWatch(saved.watchlist.join(', '))
      toast.success('提醒设置已保存')
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e))
    } finally {
      setSaving(false)
    }
  }

  return (
    <Card title="提醒设置" actions={<Button variant="primary" loading={saving} onClick={save}>保存</Button>}>
      <div className="grid max-w-2xl gap-4 sm:grid-cols-2">
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={form.enabled} onChange={(e) => setForm({ ...form, enabled: e.target.checked })} />
          启用盘中提醒
        </label>
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={form.market_regime} onChange={(e) => setForm({ ...form, market_regime: e.target.checked })} />
          大盘环境转弱时提醒
        </label>
        {num('cooldown_minutes', '冷却时间（分钟）', '同一提醒在冷却期内不重复推送')}
        {num('big_drop_pct', '大跌阈值（%）', '跌幅达到该值提醒，填负数，如 -7')}
        {num('near_stop_pct', '接近止损（%）', '持仓现价距止损价不到该百分比时提醒')}
        {num('regime_score_drop', '大盘评分下降（分）', '比前一交易日下降该分数以上时提醒')}
        <div className="sm:col-span-2">
          <Field label="额外关注的股票代码" hint="逗号分隔，如 600519, 300750；自选股、持仓、今日信号股会自动监控">
            <Input aria-label="额外关注的股票代码" value={watch} onChange={(e) => setWatch(e.target.value)} />
          </Field>
        </div>
      </div>
    </Card>
  )
}
