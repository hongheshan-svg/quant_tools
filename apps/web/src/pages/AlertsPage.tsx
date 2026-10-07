import { useCallback, useEffect, useState } from 'react'
import { api } from '@/api/endpoints'
import type { AlertRow, AlertRule, AlertSettings } from '@/api/types'
import { AlertRuleEditor, describeRule } from '@/components/AlertRuleEditor'
import { DataTable, type Column } from '@/components/DataTable'
import { Badge, Button, Card, ErrorBox, Field, Input, PageHeader, Select, Spinner, Tabs } from '@/components/ui'
import { useApi } from '@/hooks/useApi'
import { useTask } from '@/hooks/useTask'
import { useT } from '@/i18n'
import { toast } from '@/stores/toast'

const LEVEL: Record<string, [string, 'down' | 'warn' | 'accent']> = { critical: ['紧急', 'down'], warning: ['注意', 'warn'], info: ['提示', 'accent'] }

type TabKey = 'records' | 'rules' | 'settings'

export function AlertsPage() {
  const t = useT()
  const [tab, setTab] = useState<TabKey>('records')
  const [offset, setOffset] = useState(0)
  const [severity, setSeverity] = useState('')
  const [code, setCode] = useState('')
  const [channel, setChannel] = useState('')
  const fetchRecords = useCallback(() => api.alerts({ limit: 50, offset, severity, code: /^\d{6}$/.test(code) ? code : '', channel }), [offset, severity, code, channel])
  const { data, error, loading, reload } = useApi(fetchRecords)
  const check = useTask<{ alerts: number; skipped?: string }>()
  const columns: Column<AlertRow>[] = [
    { key: 'time', title: t('时间'), render: (r) => <span className="num text-xs">{r.time}</span> },
    { key: 'stock', title: t('股票'), render: (r) => <>{r.name} <span className="num text-xs text-muted">{r.code}</span></> },
    { key: 'type', title: t('类型'), render: (r) => t(r.type) },
    { key: 'level', title: t('级别'), render: (r) => <Badge tone={(LEVEL[r.severity] ?? ['', 'accent'])[1]}>{t((LEVEL[r.severity] ?? [r.severity])[0])}</Badge> },
    { key: 'message', title: t('内容'), className: 'max-w-lg', render: (r) => r.message },
    { key: 'pushed', title: t('推送'), render: (r) => (r.notified ? <span className="text-down">{t('已推送')}</span> : <span className="text-xs text-muted">{r.reason || t('未推送')}</span>) },
    { key: 'channels', title: t('渠道审计'), render: (r) => <div className="text-xs">{Object.entries(r.channels ?? {}).map(([name, ok]) => <div key={name} className={ok ? 'text-down' : 'text-warn'}>{name} · {t(ok ? '成功' : '失败')}</div>)}</div> },
  ]
  return (
    <div>
      <PageHeader
        title={t('盘中提醒')}
        description={t('交易时段内每次采集后检查今日信号股、持仓、自选股：封涨停、炸板、跌破/接近止损、目标价、大跌、大盘转弱和自定义技术指标规则')}
        actions={
          <Button loading={check.running} onClick={() => check.run(api.checkAlerts, { success: (r) => (r.skipped ? t('已跳过：{reason}', { reason: r.skipped }) : t('新增 {n} 条提醒', { n: r.alerts })) }).then(reload).catch(() => {})}>
            {t('立即检查')}
          </Button>
        }
      />
      <Tabs<TabKey>
        tabs={[{ key: 'records', label: t('提醒记录') }, { key: 'rules', label: t('提醒规则') }, { key: 'settings', label: t('提醒设置') }]}
        value={tab}
        onChange={setTab}
      />
      {tab === 'records' && (
        <>
          <div className="mb-3 flex flex-wrap gap-2">
            <Select aria-label={t('级别筛选')} value={severity} onChange={(e) => { setSeverity(e.target.value); setOffset(0) }}><option value="">{t('全部级别')}</option>{Object.entries(LEVEL).map(([key, [label]]) => <option key={key} value={key}>{t(label)}</option>)}</Select>
            <Input aria-label={t('股票代码筛选')} placeholder={t('股票代码')} value={code} onChange={(e) => { setCode(e.target.value); setOffset(0) }} />
            <Select aria-label={t('渠道筛选')} value={channel} onChange={(e) => { setChannel(e.target.value); setOffset(0) }}><option value="">{t('全部渠道')}</option>{['dingtalk', 'feishu', 'wecom', 'email', 'telegram', 'discord', 'slack', 'bark', 'ntfy', 'gotify', 'pushplus', 'serverchan', 'pushover', 'webhook'].map((name) => <option key={name}>{name}</option>)}</Select>
            <Button disabled={!offset} onClick={() => setOffset(Math.max(0, offset - 50))}>{t('上一页')}</Button><span className="self-center text-xs">{Math.floor(offset / 50) + 1}</span><Button disabled={(data?.length ?? 0) < 50} onClick={() => setOffset(offset + 50)}>{t('下一页')}</Button>
          </div>
          {error && <ErrorBox message={error} onRetry={reload} />}
          <Card bodyClassName="p-0">
            <DataTable columns={columns} rows={data ?? []} rowKey={(r, i) => r.id === undefined ? `${r.time}-${r.code}-${i}` : String(r.id)} empty={loading ? t('加载中…') : t('还没有提醒记录')} maxHeight="75vh" />
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
  const t = useT()
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
    for (const code of new Set(rules.map((r) => r.code).filter(Boolean))) {
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
      void reload()
      toast.success(t('提醒规则已保存'))
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
      setResults((x) => ({ ...x, [index]: `${r.triggered ? t('已触发：') : t('未触发：')}${r.message}` }))
    } catch (e) {
      setResults((x) => ({ ...x, [index]: e instanceof Error ? e.message : String(e) }))
    } finally {
      setTesting(null)
    }
  }

  const columns: Column<AlertRule>[] = [
    { key: 'stock', title: t('作用范围'), render: (r) => <>{r.scope && r.scope !== 'stock' ? <span>{t(({ watchlist: '动态自选集合', portfolio_holdings: '指定账户持仓', portfolio_account: '账户风险' } as Record<string, string>)[String(r.scope)] ?? String(r.scope))} {r.account}</span> : <>{names[r.code] || '-'} <span className="num text-xs text-muted">{r.code}</span></>}</> },
    { key: 'type', title: t('类型'), render: (r) => t(types[r.type]?.label ?? r.type) },
    { key: 'cond', title: t('条件'), render: (r) => describeRule(r, types) },
    {
      key: 'enabled',
      title: t('启用'),
      render: (r) => {
        const i = rules.indexOf(r)
        return <input type="checkbox" aria-label={t('启用')} disabled={data.summary?.readonly} checked={r.enabled !== false} onChange={(e) => update(rules.map((x, j) => (j === i ? { ...x, enabled: e.target.checked } : x)))} />
      },
    },
    { key: 'note', title: t('备注'), render: (r) => <span className="text-xs text-muted">{r.note || '-'}</span> },
    {
      key: 'ops',
      title: t('操作'),
      render: (r) => {
        const i = rules.indexOf(r)
        return (
          <div className="space-y-1">
            <div className="flex gap-1">
              <Button loading={testing === i} onClick={() => test(i)}>{t('测试')}</Button>
              <Button disabled={data.summary?.readonly} onClick={() => setEditing({ index: i, rule: r })}>{t('编辑')}</Button>
              <Button disabled={data.summary?.readonly} variant="danger" onClick={() => update(rules.filter((_, j) => j !== i))}>{t('删除')}</Button>
            </div>
            {results[i] && <div className="max-w-xs text-xs text-muted">{results[i]}</div>}
          </div>
        )
      },
    },
  ]

  return (
    <>
      {data.summary && <Card className="mb-3" bodyClassName="text-xs text-muted space-y-1">
        <p>{t('规则来源')}：{t(data.summary.source)} · {t('生效')} {data.summary.effective} / {data.summary.configured} · {t('停用')} {data.summary.disabled} · {t('无效')} {data.summary.invalid} · {t('文件规则')} {data.summary.file_configured}</p>
        <p>{t('自动止损、大跌和大盘提醒独立于自定义规则；同一持仓触发自定义止损时只保留自定义提醒。')}</p>
        {data.summary.readonly && <p>{t('环境变量覆盖生效；请修改 QUANT__ALERTS__RULES 后重启。')}</p>}
      </Card>}
      <Card
        title={t('自定义提醒规则')}
        actions={
          <div className="flex gap-2">
            <Button disabled={data.summary?.readonly} onClick={() => setEditing({ index: -1, rule: null })}>{t('新增规则')}</Button>
            <Button variant="primary" loading={saving} disabled={!dirty || data.summary?.readonly} onClick={save}>{t('保存')}</Button>
          </div>
        }
        bodyClassName="p-0"
      >
        <DataTable columns={columns} rows={rules} rowKey={(_, i) => String(i)} empty={t('还没有规则，点右上角「新增规则」')} />
      </Card>
      {dirty && <p className="mt-2 text-xs text-muted">{t('有未保存的修改，点「保存」后才会生效')}</p>}
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
  const t = useT()
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
      toast.success(t('提醒设置已保存'))
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e))
    } finally {
      setSaving(false)
    }
  }

  return (
    <Card title={t('提醒设置')} actions={<Button variant="primary" loading={saving} onClick={save}>{t('保存')}</Button>}>
      <div className="grid max-w-2xl gap-4 sm:grid-cols-2">
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={form.enabled} onChange={(e) => setForm({ ...form, enabled: e.target.checked })} />
          {t('启用盘中提醒')}
        </label>
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={form.market_regime} onChange={(e) => setForm({ ...form, market_regime: e.target.checked })} />
          {t('大盘环境转弱时提醒')}
        </label>
        {num('cooldown_minutes', t('冷却时间（分钟）'), t('同一提醒在冷却期内不重复推送'))}
        {num('big_drop_pct', t('大跌阈值（%）'), t('跌幅达到该值提醒，填负数，如 -7'))}
        {num('near_stop_pct', t('接近止损（%）'), t('持仓现价距止损价不到该百分比时提醒'))}
        {num('regime_score_drop', t('大盘评分下降（分）'), t('比前一交易日下降该分数以上时提醒'))}
        <Field label={t('最低推送级别')} hint={t('低于该级别的提醒只记录不推送；紧急提醒永远推送')}>
          <Select
            className="w-full"
            aria-label={t('最低推送级别')}
            value={form.min_severity ?? 'info'}
            onChange={(e) => setForm({ ...form, min_severity: e.target.value as AlertSettings['min_severity'] })}
          >
            <option value="info">{t('全部（提示及以上）')}</option>
            <option value="warning">{t('警告及以上')}</option>
            <option value="critical">{t('仅紧急')}</option>
          </Select>
        </Field>
        <div className="space-y-2">
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" aria-label={t('推送盘中提醒日报')} checked={!!form.daily_digest} onChange={(e) => setForm({ ...form, daily_digest: e.target.checked })} />
            {t('推送盘中提醒日报')}
          </label>
          <Field label={t('日报时间（HH:MM，工作日）')} hint={t('修改后需重启服务生效；当天没有提醒时不推送')}>
            <Input aria-label={t('日报时间')} value={form.digest_time ?? '15:10'} onChange={(e) => setForm({ ...form, digest_time: e.target.value })} />
          </Field>
        </div>
        <div className="sm:col-span-2">
          <Field label={t('额外关注的股票代码')} hint={t('逗号分隔，如 600519, 300750；自选股、持仓、今日信号股会自动监控')}>
            <Input aria-label={t('额外关注的股票代码')} value={watch} onChange={(e) => setWatch(e.target.value)} />
          </Field>
        </div>
      </div>
    </Card>
  )
}
