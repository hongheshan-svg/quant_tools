// 设置：AI 模型（主/备）、推送渠道与路由、聊天机器人、登录安全；在桌面端里多一个「桌面端」页
import { useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { api } from '@/api/endpoints'
import type { AuthStatus, BotSettings, CollectRssResult, IntelligenceSource, IntelligenceTestResult, LLMRole, LLMSettings, NotifierDiagnosis, NotifierField, NotifierSettings, SchedulerJob, SchedulerStatus, SearchTestResult, SettingsImportResult } from '@/api/types'
import { DataTable } from '@/components/DataTable'
import { HelpButton } from '@/components/HelpButton'
import { Button, Card, ErrorBox, Field, Input, PageHeader, Select, Spinner, Tabs, Textarea } from '@/components/ui'
import { useApi } from '@/hooks/useApi'
import { useTask } from '@/hooks/useTask'
import { toast } from '@/stores/toast'
import { getDesktop, type DesktopInfo, type QuantDesktop } from '@/utils/desktop'

type TabKey = 'llm' | 'notifier' | 'bot' | 'search' | 'intelligence' | 'scheduler' | 'backup' | 'security' | 'desktop'

export function SettingsPage() {
  const [params] = useSearchParams()
  const initialTab = params.get('tab')
  const desktop = getDesktop()
  const [tab, setTab] = useState<TabKey>(() =>
    (['llm', 'notifier', 'bot', 'search', 'intelligence', 'scheduler', 'backup', 'security'] as string[]).includes(initialTab ?? '') || (initialTab === 'desktop' && desktop) ? (initialTab as TabKey) : 'llm')
  const tabs: { key: TabKey; label: string }[] = [
    { key: 'llm', label: 'AI 模型' },
    { key: 'notifier', label: '推送' },
    { key: 'bot', label: '聊天机器人' },
    { key: 'search', label: '联网搜索' },
    { key: 'intelligence', label: '资讯源' },
    { key: 'scheduler', label: '定时任务' },
    { key: 'backup', label: '备份与恢复' },
    { key: 'security', label: '登录安全' },
  ]
  if (desktop) tabs.push({ key: 'desktop', label: '桌面端' })
  return (
    <div>
      <PageHeader title="设置" description="保存后立即生效；设置会写回 config/settings.yaml（文件中的注释会丢失）" actions={<Link to="/setup" className="text-sm text-accent hover:underline">配置向导</Link>} />
      <Card bodyClassName="p-3">
        <Tabs value={tab} onChange={setTab} tabs={tabs} />
        <div className="mb-2 flex justify-end"><HelpButton helpKey={tab} /></div>
        {tab === 'llm' && <LLMSettingsForm />}
        {tab === 'notifier' && <NotifierForm />}
        {tab === 'bot' && <BotForm />}
        {tab === 'search' && <SearchForm />}
        {tab === 'intelligence' && <IntelligenceForm />}
        {tab === 'scheduler' && <SchedulerPanel />}
        {tab === 'backup' && <BackupPanel />}
        {tab === 'security' && <SecurityForm />}
        {tab === 'desktop' && desktop && <DesktopPanel desktop={desktop} />}
      </Card>
    </div>
  )
}

function DesktopPanel({ desktop }: { desktop: QuantDesktop }) {
  const [info, setInfo] = useState<DesktopInfo | null>(null)
  useEffect(() => {
    desktop.info().then(setInfo).catch(() => setInfo(null))
  }, [desktop])
  const [autoCheck, setAutoCheck] = useState(true)
  const [checking, setChecking] = useState(false)
  const [updateText, setUpdateText] = useState('')
  useEffect(() => {
    desktop.getPrefs?.().then((p) => setAutoCheck(p?.autoCheckUpdates !== false)).catch(() => undefined)
  }, [desktop])
  const checkUpdate = async () => {
    if (!desktop.checkForUpdates) return
    setChecking(true)
    try {
      const r = await desktop.checkForUpdates()
      setUpdateText(r.status === 'available' ? `发现新版本 ${r.version ?? ''}` : r.message)
    } catch (e) {
      setUpdateText(e instanceof Error ? e.message : String(e))
    } finally {
      setChecking(false)
    }
  }
  const toggleAuto = (value: boolean) => {
    setAutoCheck(value)
    void desktop.setPrefs?.({ autoCheckUpdates: value })
  }
  return (
    <div className="max-w-xl space-y-3 text-sm">
      <p>桌面端版本：<span className="num">{info?.version || desktop.version || '--'}</span>{info && !info.packaged && <span className="ml-2 text-xs text-muted">（开发模式）</span>}</p>
      <p className="break-all">数据目录：<span className="num text-muted">{info?.dataDir ?? '--'}</span></p>
      <p className="text-xs text-muted">配置（config/settings.yaml）、数据库（data/）和日志（logs/）都在数据目录里；卸载桌面端不会删除它们。</p>
      <div className="flex gap-2">
        <Button onClick={() => void desktop.openDataDir()}>打开数据目录</Button>
        <Button onClick={() => void desktop.openLogDir()}>打开日志目录</Button>
      </div>
      {desktop.checkForUpdates && (
        <div className="flex items-center gap-3">
          <Button onClick={() => void checkUpdate()} disabled={checking}>{checking ? '检查中…' : '检查更新'}</Button>
          {updateText && <span className="text-xs text-muted">{updateText}</span>}
        </div>
      )}
      {desktop.setPrefs && (
        <label className="flex items-center gap-2">
          <input type="checkbox" checked={autoCheck} onChange={(e) => toggleAuto(e.target.checked)} />
          启动时自动检查更新
        </label>
      )}
    </div>
  )
}

// Key 在表单里一行一个；保存时一个发字符串、多个发列表
function keysToText(key: string | string[] | undefined): string {
  return Array.isArray(key) ? key.join('\n') : (key ?? '')
}

function textToKeys(text: string): string | string[] {
  const keys = text.split(/[\n,]+/).map((k) => k.trim()).filter(Boolean)
  return keys.length > 1 ? keys : (keys[0] ?? '')
}

function RoleEditor({ title, roleKey, role, platforms, onChange }: {
  title: string
  roleKey: 'primary' | 'backup' | 'vision'
  role: LLMRole
  platforms: LLMSettings['platforms']
  onChange: (role: LLMRole) => void
}) {
  const preset = platforms[role.provider ?? ''] ?? platforms.custom
  const listId = `models-${title}`
  const [fetched, setFetched] = useState<string[]>([])
  const [fetching, setFetching] = useState(false)
  const [keyText, setKeyText] = useState(keysToText(role.api_key))
  useEffect(() => {
    // 外部（加载/保存后）改了 Key 时同步到输入框；输入中的文本与其一致时不动
    if (JSON.stringify(textToKeys(keyText)) !== JSON.stringify(textToKeys(keysToText(role.api_key)))) setKeyText(keysToText(role.api_key))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [role.api_key])
  const fetchModels = async () => {
    setFetching(true)
    try {
      const r = await api.llmModels(roleKey, { ...role })
      setFetched(r.models)
      toast.success(`获取到 ${r.models.length} 个模型`)
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e))
    } finally {
      setFetching(false)
    }
  }
  const models = fetched.length ? fetched : (preset?.models ?? [])
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
          <datalist id={listId}>{models.map((m) => <option key={m} value={m} />)}</datalist>
          <Button className="mt-1" loading={fetching} onClick={fetchModels}>获取模型列表</Button>
        </Field>
        <Field label="API Key" hint={role.provider === 'ollama' ? '本地模型不需要 Key' : '可填多个 Key（一行一个），遇到限流或失效自动轮换；已保存的 Key 只显示后 4 位，不修改就原样保留'}>
          <Textarea
            rows={3}
            value={keyText}
            onChange={(e) => { setKeyText(e.target.value); onChange({ ...role, api_key: textToKeys(e.target.value) }) }}
            autoComplete="off"
            spellCheck={false}
          />
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

export function LLMSettingsForm() {
  const { data, error, loading, reload } = useApi(api.llmSettings)
  const [primary, setPrimary] = useState<LLMRole>({})
  const [backup, setBackup] = useState<LLMRole>({})
  const [vision, setVision] = useState<LLMRole>({})
  const [busy, setBusy] = useState('')

  useEffect(() => {
    if (data) {
      setPrimary(data.llm.primary ?? {})
      setBackup(data.llm.backup ?? {})
      setVision(data.llm.vision ?? {})
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
      <RoleEditor title="主力模型" roleKey="primary" role={primary} platforms={data.platforms} onChange={setPrimary} />
      <RoleEditor title="备用模型（主力失败时切换）" roleKey="backup" role={backup} platforms={data.platforms} onChange={setBackup} />
      <RoleEditor title="图片识别模型（可空，留空用主模型）" roleKey="vision" role={vision} platforms={data.platforms} onChange={setVision} />
      <div className="flex flex-wrap gap-2">
        <Button loading={busy === '主力模型'} onClick={() => test(primary, '主力模型')}>测试主力模型</Button>
        <Button loading={busy === '备用模型'} onClick={() => test(backup, '备用模型')}>测试备用模型</Button>
        <Button loading={busy === '图片识别模型'} disabled={!vision.model} onClick={() => test(vision, '图片识别模型')}>测试识图连接</Button>
        <Button
          variant="primary"
          loading={busy === 'save'}
          onClick={async () => {
            setBusy('save')
            try {
              await api.saveLlm({ primary, backup, vision })
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

const BOT_PLATFORMS = [
  { key: 'dingtalk', label: '钉钉', idKey: 'client_id', idLabel: 'Client ID（AppKey）', secretKey: 'client_secret', secretLabel: 'Client Secret',
    hint: '钉钉开放平台 → 企业内部应用 → 添加机器人，消息接收模式选「Stream 模式」' },
  { key: 'feishu', label: '飞书', idKey: 'app_id', idLabel: 'App ID', secretKey: 'app_secret', secretLabel: 'App Secret',
    hint: '飞书开放平台 → 企业自建应用 → 添加机器人，事件订阅选「长连接」并订阅「接收消息」' },
] as const

function BotForm() {
  const { data, error, loading, reload } = useApi<BotSettings>(api.botSettings)
  const [form, setForm] = useState<Record<string, any>>({})
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (data) setForm(structuredClone(data.bot))
  }, [data])

  if (loading && !data) return <Spinner />
  if (error || !data) return <ErrorBox message={error || '加载失败'} onRetry={reload} />

  const set = (platform: string, key: string, value: unknown) =>
    setForm((f) => ({ ...f, [platform]: { ...(f[platform] ?? {}), [key]: value } }))

  return (
    <div className="space-y-4">
      <p className="text-sm text-muted">
        在钉钉、飞书、Discord 里发「诊断 茅台」「大盘」「自选」「持仓」或直接提问（AI 问股），只读，不能下单。用长连接收消息，不需要公网 IP；
        单聊直接发，群聊需要 @机器人。
      </p>
      <div className="grid gap-3 lg:grid-cols-2">
        {BOT_PLATFORMS.map((p) => (
          <fieldset key={p.key} className="space-y-2 rounded-md border border-line p-3">
            <legend className="px-1 text-sm text-accent">
              {p.label}{data.running.includes(p.key) && <span className="ml-2 text-xs text-down">运行中</span>}
            </legend>
            <p className="text-xs text-muted">{p.hint}</p>
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={!!form[p.key]?.enabled} onChange={(e) => set(p.key, 'enabled', e.target.checked)} /> 启用
            </label>
            <Field label={p.idLabel}><Input value={form[p.key]?.[p.idKey] ?? ''} onChange={(e) => set(p.key, p.idKey, e.target.value)} /></Field>
            <Field label={p.secretLabel}>
              <Input type="password" value={form[p.key]?.[p.secretKey] ?? ''} onChange={(e) => set(p.key, p.secretKey, e.target.value)} />
            </Field>
            {p.key === 'feishu' && (
              <Field label="区域">
                <Select className="w-full" value={form.feishu?.domain ?? 'feishu'} onChange={(e) => set('feishu', 'domain', e.target.value)}>
                  <option value="feishu">飞书（国内）</option>
                  <option value="lark">Lark（海外）</option>
                </Select>
              </Field>
            )}
          </fieldset>
        ))}
        <fieldset className="space-y-2 rounded-md border border-line p-3">
          <legend className="px-1 text-sm text-accent">
            Discord{data.running.includes('discord') && <span className="ml-2 text-xs text-down">运行中</span>}
          </legend>
          <p className="text-xs text-muted">
            Discord 开发者后台 → Application → Bot，复制 Token；需要在 Discord 开发者后台开启 Message Content Intent。使用 Gateway 长连接，不需要公网地址
          </p>
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={!!form.discord?.enabled} onChange={(e) => set('discord', 'enabled', e.target.checked)} /> 启用
          </label>
          <Field label="Bot Token">
            <Input type="password" value={form.discord?.token ?? ''} onChange={(e) => set('discord', 'token', e.target.value)} />
          </Field>
          <Field label="允许的频道 ID（逗号分隔，为空不限制）">
            <Input
              value={(form.discord?.allowed_channels ?? []).join(', ')}
              onChange={(e) => set('discord', 'allowed_channels', e.target.value.split(/[,，;；\s]+/).map((x) => x.trim()).filter(Boolean))}
            />
          </Field>
          <Field label="服务器内回复方式">
            <Select className="w-full" value={form.discord?.guild_mode ?? 'mention'} onChange={(e) => set('discord', 'guild_mode', e.target.value)}>
              <option value="mention">mention（仅 @机器人 或以 / 开头）</option>
              <option value="all">all（回复所有消息）</option>
            </Select>
          </Field>
        </fieldset>
      </div>
      <Field label="允许使用的用户 ID（逗号分隔，为空不限制；无权限的用户发消息时会收到自己的 ID）">
        <Input
          value={(form.allowed_users ?? []).join(', ')}
          onChange={(e) => setForm((f) => ({ ...f, allowed_users: e.target.value.split(/[,，;；\s]+/).map((x) => x.trim()).filter(Boolean) }))}
        />
      </Field>
      <Button
        variant="primary"
        loading={busy}
        onClick={async () => {
          setBusy(true)
          try {
            const r = await api.saveBot(form)
            if (r.started.length) toast.success(`已保存并启动：${r.started.join('、')}`)
            else if (!r.background) toast.info('已保存。当前服务没有运行定时任务，机器人由 main.py 负责，重启它后生效')
            else if (r.restart_required) toast.info('已保存，重启服务后生效')
            else toast.success('已保存')
            void reload()
          } catch (e) {
            toast.error(e instanceof Error ? e.message : String(e))
          } finally {
            setBusy(false)
          }
        }}
      >
        保存
      </Button>
    </div>
  )
}

const WEBHOOK_CHANNELS = ['wechat', 'dingtalk', 'feishu'] as const

export function NotifierForm() {
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
  const image = form.image ?? {}
  const imageKinds = new Set<string>(image.kinds ?? ['daily_report', 'watchlist'])
  const imageChannels = new Set<string>(image.channels ?? [])
  const toggleImage = (field: 'channels' | 'kinds', value: string) => {
    const next = new Set<string>(field === 'channels' ? imageChannels : imageKinds)
    if (next.has(value)) next.delete(value)
    else next.add(value)
    setForm((f) => ({ ...f, image: { ...(f.image ?? {}), [field]: [...next] } }))
  }
  const setNested = (section: string, key: string, value: unknown) => setForm((f) => ({ ...f, [section]: { ...(f[section] ?? {}), [key]: value } }))
  const systemError = form.system_error ?? {}
  const extraChannels = Object.keys(data.fields ?? {})
  const renderField = (ch: string, f: NotifierField) => {
    const value = form[ch]?.[f.key] ?? f.default ?? ''
    const common = { 'aria-label': `${data.channels[ch]}-${f.label}`, placeholder: f.placeholder }
    return (
      <Field key={f.key} label={`${f.label}${f.required ? ' *' : ''}`}>
        {f.type === 'textarea' ? (
          <textarea {...common} rows={3} className="w-full rounded-md border border-line bg-transparent px-2 py-1 text-sm" value={typeof value === 'string' ? value : JSON.stringify(value)} onChange={(e) => set(ch, f.key, e.target.value)} />
        ) : f.type === 'number' ? (
          <Input {...common} type="number" value={value} onChange={(e) => set(ch, f.key, e.target.value === '' ? '' : Number(e.target.value))} />
        ) : (
          <Input {...common} type={f.secret ? 'password' : 'text'} value={value} onChange={(e) => set(ch, f.key, e.target.value)} />
        )}
      </Field>
    )
  }

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
      <div className="grid gap-3 lg:grid-cols-3">
        {extraChannels.map((ch) => (
          <fieldset key={ch} className="space-y-2 rounded-md border border-line p-3">
            <legend className="px-1 text-sm text-accent">{data.channels[ch]}</legend>
            <label className="flex items-center gap-2 text-sm"><input type="checkbox" aria-label={`${data.channels[ch]}-启用`} checked={!!form[ch]?.enabled} onChange={(e) => set(ch, 'enabled', e.target.checked)} /> 启用</label>
            {data.fields[ch].map((f) => renderField(ch, f))}
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
      <fieldset className="space-y-2 rounded-md border border-line p-3">
        <legend className="px-1 text-sm text-accent">图片推送（把报告渲染成分享图发送，失败时自动改发文字）</legend>
        <div className="flex flex-wrap items-center gap-4 text-sm">
          <span className="text-muted">渠道</span>
          {(data.image_channels ?? []).map((ch) => (
            <label key={ch} className="flex items-center gap-1">
              <input type="checkbox" aria-label={`图片渠道-${data.channels[ch] ?? ch}`} checked={imageChannels.has(ch)} onChange={() => toggleImage('channels', ch)} />
              {data.channels[ch] ?? ch}
            </label>
          ))}
        </div>
        <div className="flex flex-wrap items-center gap-4 text-sm">
          <span className="text-muted">消息类型</span>
          {Object.entries(data.kinds).map(([kind, label]) => (
            <label key={kind} className="flex items-center gap-1">
              <input type="checkbox" aria-label={`图片类型-${label}`} checked={imageKinds.has(kind)} onChange={() => toggleImage('kinds', kind)} />
              {label}
            </label>
          ))}
        </div>
        <div className="max-w-xs">
          <Field label="最大字数（超过时仍发文字）">
            <Input type="number" aria-label="图片最大字数" value={image.max_chars ?? 8000} onChange={(e) => setNested('image', 'max_chars', e.target.value === '' ? '' : Number(e.target.value))} />
          </Field>
        </div>
      </fieldset>
      <fieldset className="space-y-2 rounded-md border border-line p-3">
        <legend className="px-1 text-sm text-accent">系统错误通知（定时任务出错时推送，路由见上表「系统错误」）</legend>
        <div className="flex flex-wrap items-end gap-4">
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" aria-label="系统错误通知" checked={systemError.enabled ?? true} onChange={(e) => setNested('system_error', 'enabled', e.target.checked)} />
            启用
          </label>
          <div className="w-40">
            <Field label="同一来源冷却（分钟）">
              <Input type="number" aria-label="系统错误冷却分钟" min="0" value={systemError.cooldown_minutes ?? 60} onChange={(e) => setNested('system_error', 'cooldown_minutes', e.target.value === '' ? '' : Number(e.target.value))} />
            </Field>
          </div>
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

const SEARCH_KEY_PROVIDERS = ['bocha', 'tavily', 'serpapi', 'brave'] as const

const splitLines = (text: string) => text.split(/[\n,]/).map((s) => s.trim()).filter(Boolean)

function SearchForm() {
  const { data, error, loading, reload } = useApi(api.searchSettings)
  const [form, setForm] = useState<Record<string, any>>({})
  const [keys, setKeys] = useState<Record<string, string>>({})
  const [providers, setProviders] = useState('')
  const [query, setQuery] = useState('贵州茅台')
  const [results, setResults] = useState<SearchTestResult[] | null>(null)
  const [busy, setBusy] = useState('')

  useEffect(() => {
    if (!data) return
    const s = data.search
    setForm(s)
    setProviders((Array.isArray(s.providers) ? s.providers : Object.keys(data.providers)).join(', '))
    const next: Record<string, string> = {}
    for (const p of SEARCH_KEY_PROVIDERS) next[p] = ((s[p]?.api_keys as string[] | undefined) ?? []).join('\n')
    next.searxng = (s.searxng?.base_urls ?? []).join('\n')
    setKeys(next)
  }, [data])

  if (loading && !data) return <Spinner />
  if (error || !data) return <ErrorBox message={error || '加载失败'} onRetry={reload} />

  const build = () => {
    const search: Record<string, any> = {
      ...form,
      providers: splitLines(providers),
      max_results: Number(form.max_results) || 8,
      days: Number(form.days) || 7,
      cache_minutes: Number(form.cache_minutes) || 30,
      searxng: { ...(form.searxng ?? {}), base_urls: splitLines(keys.searxng ?? ''), timeout: Number(form.searxng?.timeout) || 10 },
    }
    for (const p of SEARCH_KEY_PROVIDERS) search[p] = { ...(form[p] ?? {}), api_keys: splitLines(keys[p] ?? '') }
    return search
  }
  const setNum = (key: string, value: string) => setForm({ ...form, [key]: value })

  return (
    <div className="max-w-3xl space-y-4">
      <p className="text-xs text-muted">
        个股诊断和 AI 问股用它联网搜索最新消息。按下面的顺序依次尝试，失败或无结果换下一个；博查对中文新闻效果较好。
        Key 可填多个（一行一个）轮换使用，已保存的 Key 显示为掩码，不修改原样保留即可。
      </p>
      <label className="flex items-center gap-2 text-sm">
        <input type="checkbox" checked={!!form.enabled} onChange={(e) => setForm({ ...form, enabled: e.target.checked })} /> 启用联网搜索
      </label>
      <Field label="服务顺序（逗号分隔）" hint={`可选：${Object.entries(data.providers).map(([k, v]) => `${k}（${v}）`).join('、')}`}>
        <Input value={providers} onChange={(e) => setProviders(e.target.value)} />
      </Field>
      <div className="grid gap-3 md:grid-cols-2">
        {SEARCH_KEY_PROVIDERS.map((p) => (
          <Field key={p} label={`${data.providers[p] ?? p} API Key（一行一个）`}>
            <Textarea rows={2} value={keys[p] ?? ''} onChange={(e) => setKeys({ ...keys, [p]: e.target.value })} />
          </Field>
        ))}
        <Field label="SearXNG 地址（一行一个）" hint="自建实例需在 settings.yml 开启 json 格式">
          <Textarea rows={2} value={keys.searxng ?? ''} onChange={(e) => setKeys({ ...keys, searxng: e.target.value })} />
        </Field>
        <Field label="SearXNG 超时（秒）">
          <Input type="number" value={form.searxng?.timeout ?? 10} onChange={(e) => setForm({ ...form, searxng: { ...(form.searxng ?? {}), timeout: e.target.value } })} />
        </Field>
      </div>
      <div className="grid gap-3 md:grid-cols-3">
        <Field label="每次最多条数"><Input type="number" value={form.max_results ?? 8} onChange={(e) => setNum('max_results', e.target.value)} /></Field>
        <Field label="只保留最近天数"><Input type="number" value={form.days ?? 7} onChange={(e) => setNum('days', e.target.value)} /></Field>
        <Field label="缓存时间（分钟）"><Input type="number" value={form.cache_minutes ?? 30} onChange={(e) => setNum('cache_minutes', e.target.value)} /></Field>
      </div>
      <div className="flex flex-wrap items-end gap-2">
        <Field label="测试查询词"><Input value={query} onChange={(e) => setQuery(e.target.value)} /></Field>
        <Button
          loading={busy === 'test'}
          onClick={async () => {
            setBusy('test')
            try {
              setResults((await api.testSearch(build(), query)).results)
            } catch (e) {
              toast.error(e instanceof Error ? e.message : String(e))
            } finally {
              setBusy('')
            }
          }}
        >
          测试
        </Button>
        <Button
          variant="primary"
          loading={busy === 'save'}
          onClick={async () => {
            setBusy('save')
            try {
              await api.saveSearch(build())
              toast.success('联网搜索设置已保存')
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
      {results && (
        <ul className="space-y-1 text-sm">
          {results.length === 0 && <li className="text-muted">没有已配置的搜索服务</li>}
          {results.map((r) => (
            <li key={r.provider}>
              <span className={r.ok ? 'text-down' : 'text-up'}>{r.ok ? '成功' : '失败'}</span> {r.label}
              {r.ok ? `：${r.count} 条，如「${r.samples.join('」「')}」` : `：${r.error}`}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

function SchedulerPanel() {
  const status = useApi<SchedulerStatus>(api.scheduler)
  const { run } = useTask()
  const [runningId, setRunningId] = useState('')
  if (status.error) return <ErrorBox message={status.error} />
  if (!status.data) return <Spinner />
  const { running, message, jobs } = status.data
  const runNow = async (job: SchedulerJob) => {
    setRunningId(job.id)
    try {
      await run(() => api.runJob(job.id), { success: `${job.name}已运行完成` })
    } catch {
      // 错误已由 useTask 提示
    } finally {
      setRunningId('')
      status.reload()
    }
  }
  return (
    <div className="space-y-3">
      <p className="text-sm text-muted">
        {running ? '本进程正在运行定时任务。' : message}非交易日会跳过行情和分析类任务。
      </p>
      <DataTable
        rows={jobs}
        rowKey={(j) => j.id}
        columns={[
          { key: 'name', title: '任务', render: (j) => j.name },
          { key: 'trigger', title: '触发规则', render: (j) => j.trigger || '-' },
          { key: 'next', title: '下次运行', render: (j) => (j.next_run_time ? j.next_run_time.replace('T', ' ').slice(0, 19) : '-') },
          { key: 'state', title: '状态', render: (j) => (!running ? '未运行' : j.paused ? '已暂停' : '正常') },
          {
            key: 'op',
            title: '操作',
            render: (j) => (
              <Button loading={runningId === j.id} disabled={!!runningId} onClick={() => runNow(j)}>
                立即运行
              </Button>
            ),
          },
        ]}
      />
    </div>
  )
}

function BackupPanel() {
  const [includeSecrets, setIncludeSecrets] = useState(false)
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<SettingsImportResult | null>(null)

  const pickFile = async (file: File | undefined) => {
    if (file) setText(await file.text())
  }
  const doImport = async () => {
    if (!text.trim()) return toast.error('请先选择文件或粘贴配置内容')
    if (!window.confirm('导入会整体覆盖当前 settings.yaml，确定继续吗？')) return
    setBusy(true)
    try {
      setResult(await api.importSettings(text))
      toast.success('配置已导入并生效')
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }
  return (
    <div className="space-y-6">
      <section className="space-y-2">
        <h3 className="text-sm font-medium">导出配置</h3>
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={includeSecrets} onChange={(e) => setIncludeSecrets(e.target.checked)} />
          包含密钥
        </label>
        {includeSecrets && <p className="text-xs text-up">风险提示：导出文件将包含 API Key、Webhook 等明文密钥，请妥善保管，不要分享或提交到仓库。</p>}
        <a className="inline-block rounded-md border border-line px-3 py-1.5 text-sm" href={api.exportSettingsUrl(includeSecrets)} download>
          导出
        </a>
        <p className="text-xs text-muted">不包含密钥时，密钥导出为 ******，导入时会沿用当前配置里的值。</p>
      </section>
      <section className="space-y-2">
        <h3 className="text-sm font-medium">导入配置</h3>
        <input type="file" accept=".yaml,.yml,text/yaml" aria-label="选择配置文件" onChange={(e) => pickFile(e.target.files?.[0])} />
        <Textarea rows={10} value={text} onChange={(e) => setText(e.target.value)} placeholder="或在此粘贴 YAML 配置" aria-label="配置内容" />
        <Button variant="primary" loading={busy} onClick={doImport}>
          导入
        </Button>
        {result && (
          <div className="space-y-1 text-sm">
            <div>已导入配置段：{result.sections.join('、') || '无'}</div>
            <div>还原密钥：{result.restored} 项</div>
            {result.warnings.map((w) => (
              <div key={w} className="text-up">
                {w}
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  )
}

function IntelligenceForm() {
  const { data, error, loading, reload } = useApi(api.intelligenceSettings)
  const [enabled, setEnabled] = useState(true)
  const [interval, setIntervalMinutes] = useState('30')
  const [maxItems, setMaxItems] = useState('30')
  const [sources, setSources] = useState<IntelligenceSource[]>([])
  const [draft, setDraft] = useState({ name: '', url: '' })
  const [tests, setTests] = useState<Record<number, IntelligenceTestResult | 'loading'>>({})
  const [saving, setSaving] = useState(false)
  const collect = useTask<CollectRssResult>()

  useEffect(() => {
    if (!data) return
    const s = data.intelligence
    setEnabled(!!s.enabled)
    setIntervalMinutes(String(s.interval_minutes ?? 30))
    setMaxItems(String(s.max_items_per_source ?? 30))
    setSources(s.sources ?? [])
    setTests({})
  }, [data])

  if (loading && !data) return <Spinner />
  if (error || !data) return <ErrorBox message={error || '加载失败'} onRetry={reload} />

  const update = (i: number, patch: Partial<IntelligenceSource>) => setSources(sources.map((s, j) => (j === i ? { ...s, ...patch } : s)))
  const test = async (i: number) => {
    setTests((t) => ({ ...t, [i]: 'loading' }))
    try {
      const r = await api.testIntelligenceSource(sources[i].url)
      setTests((t) => ({ ...t, [i]: r }))
    } catch (e) {
      setTests((t) => ({ ...t, [i]: { ok: false, title: '', count: 0, samples: [], error: e instanceof Error ? e.message : String(e) } }))
    }
  }
  const add = () => {
    const name = draft.name.trim()
    const url = draft.url.trim()
    if (!name || !url) return toast.error('请填写名称和地址')
    setSources([...sources, { name, url, enabled: true }])
    setDraft({ name: '', url: '' })
  }
  const save = async () => {
    setSaving(true)
    try {
      await api.saveIntelligence({
        enabled,
        interval_minutes: Number(interval) || 30,
        max_items_per_source: Number(maxItems) || 30,
        sources,
      })
      toast.success('已保存')
      void reload()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e))
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="max-w-3xl space-y-4">
      <p className="text-xs text-muted">
        订阅 RSS / Atom 地址，采集到的文章会进入实时资讯流和舆情分析。可用 RSSHub 等工具为财经媒体生成 RSS 地址；与交易日无关，全天按间隔采集。
      </p>
      <label className="flex items-center gap-2 text-sm">
        <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} /> 启用资讯源采集
      </label>
      <div className="grid gap-3 md:grid-cols-2">
        <Field label="采集间隔（分钟，最少 5）"><Input type="number" value={interval} onChange={(e) => setIntervalMinutes(e.target.value)} /></Field>
        <Field label="每源每次最多条数（1~200）"><Input type="number" value={maxItems} onChange={(e) => setMaxItems(e.target.value)} /></Field>
      </div>
      <div className="space-y-2">
        {sources.length === 0 && <p className="text-sm text-muted">还没有资讯源，在下面添加</p>}
        {sources.map((s, i) => {
          const t = tests[i]
          return (
            <div key={i} className="space-y-1 rounded border border-line p-2">
              <div className="flex flex-wrap items-center gap-2">
                <label className="flex items-center gap-1 text-sm">
                  <input type="checkbox" aria-label={`${s.name}-启用`} checked={s.enabled} onChange={(e) => update(i, { enabled: e.target.checked })} /> 启用
                </label>
                <Input className="w-32" aria-label="名称" value={s.name} onChange={(e) => update(i, { name: e.target.value })} />
                <Input className="min-w-0 flex-1" aria-label="地址" value={s.url} onChange={(e) => update(i, { url: e.target.value })} />
                <Button onClick={() => void test(i)} loading={t === 'loading'}>测试</Button>
                <Button variant="danger" onClick={() => { setSources(sources.filter((_, j) => j !== i)); setTests({}) }}>删除</Button>
              </div>
              {t && t !== 'loading' && (
                <p className="text-xs">
                  <span className={t.ok ? 'text-down' : 'text-up'}>{t.ok ? '成功' : '失败'}</span>
                  {t.ok ? `：${t.title || '（无标题）'}，${t.count} 条，如「${t.samples.join('」「')}」` : `：${t.error}`}
                </p>
              )}
            </div>
          )
        })}
      </div>
      <div className="flex flex-wrap items-end gap-2">
        <Field label="名称"><Input className="w-32" value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} /></Field>
        <Field label="RSS 地址"><Input className="w-72" placeholder="https://example.com/feed.xml" value={draft.url} onChange={(e) => setDraft({ ...draft, url: e.target.value })} /></Field>
        <Button onClick={add}>添加</Button>
      </div>
      <div className="flex gap-2">
        <Button variant="primary" loading={saving} onClick={() => void save()}>保存</Button>
        <Button
          loading={collect.running}
          onClick={() => void collect.run(() => api.collectRss(), { success: (r) => `采集完成，新增 ${r.inserted} 条（抓到 ${r.fetched} 条）` }).catch(() => undefined)}
        >
          立即采集
        </Button>
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
