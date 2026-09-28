// 设置：AI 模型（主/备）、推送渠道与路由、登录安全
import { useEffect, useState } from 'react'
import { api } from '@/api/endpoints'
import type { AuthStatus, LLMRole, LLMSettings, NotifierDiagnosis, NotifierSettings } from '@/api/types'
import { Button, Card, ErrorBox, Field, Input, PageHeader, Select, Spinner, Tabs } from '@/components/ui'
import { useApi } from '@/hooks/useApi'
import { toast } from '@/stores/toast'

type TabKey = 'llm' | 'notifier' | 'security'

export function SettingsPage() {
  const [tab, setTab] = useState<TabKey>('llm')
  return (
    <div>
      <PageHeader title="设置" description="保存后立即生效；设置会写回 config/settings.yaml（文件中的注释会丢失）" />
      <Card bodyClassName="p-3">
        <Tabs value={tab} onChange={setTab} tabs={[{ key: 'llm', label: 'AI 模型' }, { key: 'notifier', label: '推送' }, { key: 'security', label: '登录安全' }]} />
        {tab === 'llm' && <LLMSettingsForm />}
        {tab === 'notifier' && <NotifierForm />}
        {tab === 'security' && <SecurityForm />}
      </Card>
    </div>
  )
}

function RoleEditor({ title, role, platforms, onChange }: {
  title: string
  role: LLMRole
  platforms: LLMSettings['platforms']
  onChange: (role: LLMRole) => void
}) {
  const preset = platforms[role.provider ?? ''] ?? platforms.custom
  const listId = `models-${title}`
  return (
    <fieldset className="rounded-md border border-line p-3">
      <legend className="px-1 text-sm text-accent">{title}</legend>
      <div className="grid gap-3 md:grid-cols-2">
        <Field label="平台">
          <Select
            className="w-full"
            value={role.provider ?? 'custom'}
            onChange={(e) => {
              const p = platforms[e.target.value]
              onChange({ ...role, provider: e.target.value, base_url: p?.base_url ?? '', model: p?.default_model || role.model })
            }}
          >
            {Object.entries(platforms).map(([key, p]) => <option key={key} value={key}>{p.name}</option>)}
          </Select>
        </Field>
        <Field label="模型">
          <Input list={listId} value={role.model ?? ''} onChange={(e) => onChange({ ...role, model: e.target.value })} />
          <datalist id={listId}>{(preset?.models ?? []).map((m) => <option key={m} value={m} />)}</datalist>
        </Field>
        <Field label="API Key" hint={role.provider === 'ollama' ? '本地模型不需要 Key' : '已保存的 Key 只显示后 4 位，不修改就原样保留'}>
          <Input type="password" value={role.api_key ?? ''} onChange={(e) => onChange({ ...role, api_key: e.target.value })} autoComplete="off" />
        </Field>
        <Field label="Base URL" hint="Claude、Gemini 留空；其他平台按 OpenAI 兼容地址">
          <Input value={role.base_url ?? ''} onChange={(e) => onChange({ ...role, base_url: e.target.value })} />
        </Field>
        <Field label="温度">
          <Input type="number" step="0.1" min="0" max="2" value={role.temperature ?? 0.3} onChange={(e) => onChange({ ...role, temperature: Number(e.target.value) })} />
        </Field>
        <Field label="最大输出 token">
          <Input type="number" step="256" value={role.max_tokens ?? 4096} onChange={(e) => onChange({ ...role, max_tokens: Number(e.target.value) })} />
        </Field>
      </div>
    </fieldset>
  )
}

function LLMSettingsForm() {
  const { data, error, loading, reload } = useApi(api.llmSettings)
  const [primary, setPrimary] = useState<LLMRole>({})
  const [backup, setBackup] = useState<LLMRole>({})
  const [busy, setBusy] = useState('')

  useEffect(() => {
    if (data) {
      setPrimary(data.llm.primary ?? {})
      setBackup(data.llm.backup ?? {})
    }
  }, [data])

  if (loading && !data) return <Spinner />
  if (error || !data) return <ErrorBox message={error || '加载失败'} onRetry={reload} />

  const test = async (role: LLMRole, label: string) => {
    setBusy(label)
    try {
      const r = await api.testLlm({ primary: role })
      if (r.ok) toast.success(`${label}连接正常：${r.reply}`)
      else toast.error(`${label}连接失败：${r.error ?? '没有返回内容'}`)
    } finally {
      setBusy('')
    }
  }

  return (
    <div className="space-y-4">
      <RoleEditor title="主力模型" role={primary} platforms={data.platforms} onChange={setPrimary} />
      <RoleEditor title="备用模型（主力失败时切换）" role={backup} platforms={data.platforms} onChange={setBackup} />
      <div className="flex flex-wrap gap-2">
        <Button loading={busy === '主力模型'} onClick={() => test(primary, '主力模型')}>测试主力模型</Button>
        <Button loading={busy === '备用模型'} onClick={() => test(backup, '备用模型')}>测试备用模型</Button>
        <Button
          variant="primary"
          loading={busy === 'save'}
          onClick={async () => {
            setBusy('save')
            try {
              await api.saveLlm({ primary, backup })
              toast.success('AI 设置已保存')
              void reload()
            } catch (e) {
              toast.error(e instanceof Error ? e.message : String(e))
            } finally {
              setBusy('')
            }
          }}
        >
          保存
        </Button>
      </div>
    </div>
  )
}

const WEBHOOK_CHANNELS = ['wechat', 'dingtalk', 'feishu'] as const

function NotifierForm() {
  const { data, error, loading, reload } = useApi<NotifierSettings>(api.notifierSettings)
  const [form, setForm] = useState<Record<string, any>>({})
  const [check, setCheck] = useState<NotifierDiagnosis | null>(null)
  const [busy, setBusy] = useState('')

  useEffect(() => {
    if (data) setForm(structuredClone(data.notifier))
  }, [data])

  if (loading && !data) return <Spinner />
  if (error || !data) return <ErrorBox message={error || '加载失败'} onRetry={reload} />

  const set = (channel: string, key: string, value: unknown) => setForm((f) => ({ ...f, [channel]: { ...(f[channel] ?? {}), [key]: value } }))
  const payload = () => {
    const { quiet_hours_text, ...rest } = form
    const parts = String(quiet_hours_text ?? (form.quiet_hours ?? []).join('-')).split('-').map((s: string) => s.trim()).filter(Boolean)
    return { ...rest, quiet_hours: parts.length === 2 ? parts : [] }
  }
  const route = (kind: string) => new Set<string>(form.routes?.[kind] ?? [])
  const toggleRoute = (kind: string, channel: string) => {
    const next = route(kind)
    if (next.has(channel)) next.delete(channel)
    else next.add(channel)
    setForm((f) => ({ ...f, routes: { ...(f.routes ?? {}), [kind]: [...next] } }))
  }
  const email = form.email ?? {}

  return (
    <div className="space-y-4">
      <div className="grid gap-3 lg:grid-cols-3">
        {WEBHOOK_CHANNELS.map((ch) => (
          <fieldset key={ch} className="space-y-2 rounded-md border border-line p-3">
            <legend className="px-1 text-sm text-accent">{data.channels[ch]}机器人</legend>
            <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={!!form[ch]?.enabled} onChange={(e) => set(ch, 'enabled', e.target.checked)} /> 启用</label>
            <Field label="Webhook"><Input value={form[ch]?.webhook_url ?? ''} onChange={(e) => set(ch, 'webhook_url', e.target.value)} /></Field>
            {ch !== 'wechat' && <Field label="加签密钥（可空）"><Input value={form[ch]?.secret ?? ''} onChange={(e) => set(ch, 'secret', e.target.value)} /></Field>}
            <Button loading={busy === ch} onClick={async () => {
              setBusy(ch)
              const r = await api.testNotifier(ch, payload()).finally(() => setBusy(''))
              if (r.ok) toast.success(`${data.channels[ch]}测试消息已发送`)
              else toast.error(`${data.channels[ch]}发送失败：${r.error}`)
            }}>发送测试消息</Button>
          </fieldset>
        ))}
      </div>
      <fieldset className="rounded-md border border-line p-3">
        <legend className="px-1 text-sm text-accent">邮件（SMTP）</legend>
        <div className="grid gap-3 md:grid-cols-3">
          <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={!!email.enabled} onChange={(e) => set('email', 'enabled', e.target.checked)} /> 启用</label>
          <Field label="SMTP 服务器"><Input value={email.smtp_host ?? ''} onChange={(e) => set('email', 'smtp_host', e.target.value)} /></Field>
          <Field label="端口"><Input type="number" value={email.smtp_port ?? 465} onChange={(e) => set('email', 'smtp_port', Number(e.target.value))} /></Field>
          <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={email.use_ssl ?? true} onChange={(e) => set('email', 'use_ssl', e.target.checked)} /> SSL（465）；不勾选用 STARTTLS（587）</label>
          <Field label="账号"><Input value={email.username ?? ''} onChange={(e) => set('email', 'username', e.target.value)} /></Field>
          <Field label="密码/授权码"><Input type="password" value={email.password ?? ''} onChange={(e) => set('email', 'password', e.target.value)} /></Field>
          <Field label="发件人（可空）"><Input value={email.sender ?? ''} onChange={(e) => set('email', 'sender', e.target.value)} /></Field>
          <Field label="收件人（逗号分隔）">
            <Input value={(email.to ?? []).join(', ')} onChange={(e) => set('email', 'to', e.target.value.split(/[,，;；]/).map((x) => x.trim()).filter(Boolean))} />
          </Field>
          <div className="flex items-end">
            <Button loading={busy === 'email'} onClick={async () => {
              setBusy('email')
              const r = await api.testNotifier('email', payload()).finally(() => setBusy(''))
              if (r.ok) toast.success('测试邮件已发送')
              else toast.error(`邮件发送失败：${r.error}`)
            }}>发送测试邮件</Button>
          </div>
        </div>
      </fieldset>
      <fieldset className="rounded-md border border-line p-3">
        <legend className="px-1 text-sm text-accent">推送路由（都不勾选 = 推送到全部已启用渠道）</legend>
        <table className="text-sm">
          <thead>
            <tr><th /> {Object.entries(data.channels).map(([ch, label]) => <th key={ch} className="px-3 font-normal text-muted">{label}</th>)}</tr>
          </thead>
          <tbody>
            {Object.entries(data.kinds).map(([kind, label]) => (
              <tr key={kind}>
                <td className="pr-3 py-1">{label}</td>
                {Object.keys(data.channels).map((ch) => (
                  <td key={ch} className="px-3 text-center">
                    <input type="checkbox" aria-label={`${label}-${ch}`} checked={route(kind).has(ch)} onChange={() => toggleRoute(kind, ch)} />
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
        <div className="mt-3 max-w-xs">
          <Field label="免打扰时段（如 22:00-08:00，期间只推送紧急提醒）">
            <Input value={form.quiet_hours_text ?? (form.quiet_hours ?? []).join('-')} onChange={(e) => setForm((f) => ({ ...f, quiet_hours_text: e.target.value }))} />
          </Field>
        </div>
      </fieldset>
      {check && (
        <div className="rounded-md border border-line p-3 text-sm">
          {check.channels.filter((c) => c.enabled || (c.configured && c.issues.length)).map((c) => (
            <div key={c.channel}>{c.label}（{c.enabled ? '已启用' : '未启用'}）：{c.issues.length ? <span className="text-warn">{c.issues.join('；')}</span> : <span className="text-down">配置正常</span>}</div>
          ))}
          {check.routes.map((r) => <div key={r} className="text-warn">{r}</div>)}
          {!check.channels.some((c) => c.enabled) && <div className="text-muted">还没有启用任何推送渠道</div>}
        </div>
      )}
      <div className="flex gap-2">
        <Button onClick={async () => setCheck(await api.diagnoseNotifier(payload()))}>检查配置</Button>
        <Button variant="primary" loading={busy === 'save'} onClick={async () => {
          setBusy('save')
          try {
            await api.saveNotifier(payload())
            toast.success('推送设置已保存')
            void reload()
          } catch (e) {
            toast.error(e instanceof Error ? e.message : String(e))
          } finally {
            setBusy('')
          }
        }}>保存</Button>
      </div>
    </div>
  )
}

function SecurityForm() {
  const status = useApi<AuthStatus>(api.authStatus)
  const [password, setPassword] = useState('')
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  if (!status.data) return <Spinner />
  const s = status.data
  return (
    <div className="max-w-md space-y-5 text-sm">
      <div>
        <p className="mb-2">
          Web 登录：<b className={s.auth_enabled ? 'text-down' : 'text-warn'}>{s.auth_enabled ? '已开启' : '未开启（只允许本机访问）'}</b>
        </p>
        <p className="mb-3 text-xs text-muted">开启登录后，可以把 web.host 改成 0.0.0.0，让手机等局域网设备访问。</p>
        {!s.auth_enabled && !s.password_set && (
          <Field label="设置访问密码（至少 6 位）"><Input type="password" value={password} onChange={(e) => setPassword(e.target.value)} /></Field>
        )}
        <Button
          className="mt-2"
          variant={s.auth_enabled ? 'danger' : 'primary'}
          onClick={async () => {
            try {
              await api.setWebAuth(!s.auth_enabled, password)
              toast.success(s.auth_enabled ? '已关闭登录' : '已开启登录')
              setPassword('')
              void status.reload()
            } catch (e) {
              toast.error(e instanceof Error ? e.message : String(e))
            }
          }}
        >
          {s.auth_enabled ? '关闭登录' : '开启登录'}
        </Button>
      </div>
      {s.password_set && (
        <div className="space-y-2">
          <p className="font-medium">修改密码</p>
          <Field label="当前密码"><Input type="password" value={current} onChange={(e) => setCurrent(e.target.value)} /></Field>
          <Field label="新密码（至少 6 位）"><Input type="password" value={next} onChange={(e) => setNext(e.target.value)} /></Field>
          <Button onClick={async () => {
            try {
              await api.changePassword(current, next)
              toast.success('密码已修改')
              setCurrent('')
              setNext('')
            } catch (e) {
              toast.error(e instanceof Error ? e.message : String(e))
            }
          }}>修改密码</Button>
        </div>
      )}
    </div>
  )
}
