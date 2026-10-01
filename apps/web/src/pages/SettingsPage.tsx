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
import { useT } from '@/i18n'
import { toast } from '@/stores/toast'
import { getDesktop, type DesktopInfo, type QuantDesktop } from '@/utils/desktop'

type TabKey = 'llm' | 'notifier' | 'bot' | 'search' | 'intelligence' | 'scheduler' | 'backup' | 'security' | 'desktop'

export function SettingsPage() {
  const t = useT()
  const [params] = useSearchParams()
  const initialTab = params.get('tab')
  const desktop = getDesktop()
  const [tab, setTab] = useState<TabKey>(() =>
    (['llm', 'notifier', 'bot', 'search', 'intelligence', 'scheduler', 'backup', 'security'] as string[]).includes(initialTab ?? '') || (initialTab === 'desktop' && desktop) ? (initialTab as TabKey) : 'llm')
  const tabs: { key: TabKey; label: string }[] = [
    { key: 'llm', label: t('AI 模型') },
    { key: 'notifier', label: t('推送') },
    { key: 'bot', label: t('聊天机器人') },
    { key: 'search', label: t('联网搜索') },
    { key: 'intelligence', label: t('资讯源') },
    { key: 'scheduler', label: t('定时任务') },
    { key: 'backup', label: t('备份与恢复') },
    { key: 'security', label: t('登录安全') },
  ]
  if (desktop) tabs.push({ key: 'desktop', label: t('桌面端') })
  return (
    <div>
      <PageHeader title={t('设置')} description={t('保存后立即生效；设置会写回 config/settings.yaml（文件中的注释会丢失）')} actions={<Link to="/setup" className="text-sm text-accent hover:underline">{t('配置向导')}</Link>} />
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
  const t = useT()
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
      setUpdateText(r.status === 'available' ? t('发现新版本 {version}', { version: r.version ?? '' }) : r.message)
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
      <p>{t('桌面端版本：')}<span className="num">{info?.version || desktop.version || '--'}</span>{info && !info.packaged && <span className="ml-2 text-xs text-muted">{t('（开发模式）')}</span>}</p>
      <p className="break-all">{t('数据目录：')}<span className="num text-muted">{info?.dataDir ?? '--'}</span></p>
      <p className="text-xs text-muted">{t('配置（config/settings.yaml）、数据库（data/）和日志（logs/）都在数据目录里；卸载桌面端不会删除它们。')}</p>
      <div className="flex gap-2">
        <Button onClick={() => void desktop.openDataDir()}>{t('打开数据目录')}</Button>
        <Button onClick={() => void desktop.openLogDir()}>{t('打开日志目录')}</Button>
      </div>
      {desktop.checkForUpdates && (
        <div className="flex items-center gap-3">
          <Button onClick={() => void checkUpdate()} disabled={checking}>{checking ? t('检查中…') : t('检查更新')}</Button>
          {updateText && <span className="text-xs text-muted">{updateText}</span>}
        </div>
      )}
      {desktop.setPrefs && (
        <label className="flex items-center gap-2">
          <input type="checkbox" checked={autoCheck} onChange={(e) => toggleAuto(e.target.checked)} />
          {t('启动时自动检查更新')}
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
  const t = useT()
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
      toast.success(t('获取到 {n} 个模型', { n: r.models.length }))
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e))
    } finally {
      setFetching(false)
    }
  }
  const models = fetched.length ? fetched : (preset?.models ?? [])
  return (
    <fieldset className="rounded-md border border-line p-3">
      <legend className="px-1 text-sm text-accent">{t(title)}</legend>
      <div className="grid gap-3 md:grid-cols-2">
        <Field label={t('平台')}>
          <Select
            className="w-full"
            value={role.provider ?? 'custom'}
            onChange={(e) => {
              const p = platforms[e.target.value]
              onChange({ ...role, provider: e.target.value, base_url: p?.base_url ?? '', model: p?.default_model || role.model })
            }}
          >
            {Object.entries(platforms).map(([key, p]) => <option key={key} value={key}>{t(p.name)}</option>)}
          </Select>
        </Field>
        <Field label={t('模型')}>
          <Input list={listId} value={role.model ?? ''} onChange={(e) => onChange({ ...role, model: e.target.value })} />
          <datalist id={listId}>{models.map((m) => <option key={m} value={m} />)}</datalist>
          <Button className="mt-1" loading={fetching} onClick={fetchModels}>{t('获取模型列表')}</Button>
        </Field>
        <Field label="API Key" hint={role.provider === 'ollama' ? t('本地模型不需要 Key') : t('可填多个 Key（一行一个），遇到限流或失效自动轮换；已保存的 Key 只显示后 4 位，不修改就原样保留')}>
          <Textarea
            rows={3}
            value={keyText}
            onChange={(e) => { setKeyText(e.target.value); onChange({ ...role, api_key: textToKeys(e.target.value) }) }}
            autoComplete="off"
            spellCheck={false}
          />
        </Field>
        <Field label="Base URL" hint={t('Claude、Gemini 留空；其他平台按 OpenAI 兼容地址')}>
          <Input value={role.base_url ?? ''} onChange={(e) => onChange({ ...role, base_url: e.target.value })} />
        </Field>
        <Field label={t('温度')}>
          <Input type="number" step="0.1" min="0" max="2" value={role.temperature ?? 0.3} onChange={(e) => onChange({ ...role, temperature: Number(e.target.value) })} />
        </Field>
        <Field label={t('最大输出 token')}>
          <Input type="number" step="256" value={role.max_tokens ?? 4096} onChange={(e) => onChange({ ...role, max_tokens: Number(e.target.value) })} />
        </Field>
      </div>
    </fieldset>
  )
}

export function LLMSettingsForm() {
  const t = useT()
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
  if (error || !data) return <ErrorBox message={error || t('加载失败')} onRetry={reload} />

  const test = async (role: LLMRole, label: string) => {
    setBusy(label)
    try {
      const r = await api.testLlm({ primary: role })
      if (r.ok) {
        toast.success(t('{label}连接正常：{reply}', { label: t(label), reply: r.reply ?? '' }))
        if (r.note) toast.info(t(r.note))
      }
      else toast.error(t('{label}连接失败：{error}', { label: t(label), error: r.error ? t(r.error) : t('没有返回内容') }))
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
        <Button loading={busy === '主力模型'} onClick={() => test(primary, '主力模型')}>{t('测试主力模型')}</Button>
        <Button loading={busy === '备用模型'} onClick={() => test(backup, '备用模型')}>{t('测试备用模型')}</Button>
        <Button loading={busy === '图片识别模型'} disabled={!vision.model} onClick={() => test(vision, '图片识别模型')}>{t('测试识图连接')}</Button>
        <Button
          variant="primary"
          loading={busy === 'save'}
          onClick={async () => {
            setBusy('save')
            try {
              await api.saveLlm({ primary, backup, vision })
              toast.success(t('AI 设置已保存'))
              void reload()
            } catch (e) {
              toast.error(e instanceof Error ? e.message : String(e))
            } finally {
              setBusy('')
            }
          }}
        >
          {t('保存')}
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
  const t = useT()
  const { data, error, loading, reload } = useApi<BotSettings>(api.botSettings)
  const [form, setForm] = useState<Record<string, any>>({})
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (data) setForm(structuredClone(data.bot))
  }, [data])

  if (loading && !data) return <Spinner />
  if (error || !data) return <ErrorBox message={error || t('加载失败')} onRetry={reload} />

  const set = (platform: string, key: string, value: unknown) =>
    setForm((f) => ({ ...f, [platform]: { ...(f[platform] ?? {}), [key]: value } }))

  return (
    <div className="space-y-4">
      <p className="text-sm text-muted">
        {t('在钉钉、飞书、Discord 里发「诊断 茅台」「大盘」「自选」「持仓」或直接提问（AI 问股），只读，不能下单。用长连接收消息，不需要公网 IP；单聊直接发，群聊需要 @机器人。')}
      </p>
      <div className="grid gap-3 lg:grid-cols-2">
        {BOT_PLATFORMS.map((p) => (
          <fieldset key={p.key} className="space-y-2 rounded-md border border-line p-3">
            <legend className="px-1 text-sm text-accent">
              {t(p.label)}{data.running.includes(p.key) && <span className="ml-2 text-xs text-down">{t('运行中')}</span>}
            </legend>
            <p className="text-xs text-muted">{t(p.hint)}</p>
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={!!form[p.key]?.enabled} onChange={(e) => set(p.key, 'enabled', e.target.checked)} /> {t('启用')}
            </label>
            <Field label={t(p.idLabel)}><Input value={form[p.key]?.[p.idKey] ?? ''} onChange={(e) => set(p.key, p.idKey, e.target.value)} /></Field>
            <Field label={t(p.secretLabel)}>
              <Input type="password" value={form[p.key]?.[p.secretKey] ?? ''} onChange={(e) => set(p.key, p.secretKey, e.target.value)} />
            </Field>
            {p.key === 'feishu' && (
              <Field label={t('区域')}>
                <Select className="w-full" value={form.feishu?.domain ?? 'feishu'} onChange={(e) => set('feishu', 'domain', e.target.value)}>
                  <option value="feishu">{t('飞书（国内）')}</option>
                  <option value="lark">{t('Lark（海外）')}</option>
                </Select>
              </Field>
            )}
          </fieldset>
        ))}
        <fieldset className="space-y-2 rounded-md border border-line p-3">
          <legend className="px-1 text-sm text-accent">
            Discord{data.running.includes('discord') && <span className="ml-2 text-xs text-down">{t('运行中')}</span>}
          </legend>
          <p className="text-xs text-muted">
            {t('Discord 开发者后台 → Application → Bot，复制 Token；需要在 Discord 开发者后台开启 Message Content Intent。使用 Gateway 长连接，不需要公网地址')}
          </p>
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={!!form.discord?.enabled} onChange={(e) => set('discord', 'enabled', e.target.checked)} /> {t('启用')}
          </label>
          <Field label="Bot Token">
            <Input type="password" value={form.discord?.token ?? ''} onChange={(e) => set('discord', 'token', e.target.value)} />
          </Field>
          <Field label={t('允许的频道 ID（逗号分隔，为空不限制）')}>
            <Input
              value={(form.discord?.allowed_channels ?? []).join(', ')}
              onChange={(e) => set('discord', 'allowed_channels', e.target.value.split(/[,，;；\s]+/).map((x) => x.trim()).filter(Boolean))}
            />
          </Field>
          <Field label={t('服务器内回复方式')}>
            <Select className="w-full" value={form.discord?.guild_mode ?? 'mention'} onChange={(e) => set('discord', 'guild_mode', e.target.value)}>
              <option value="mention">{t('mention（仅 @机器人 或以 / 开头）')}</option>
              <option value="all">{t('all（回复所有消息）')}</option>
            </Select>
          </Field>
        </fieldset>
      </div>
      <Field label={t('允许使用的用户 ID（逗号分隔，为空不限制；无权限的用户发消息时会收到自己的 ID）')}>
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
            if (r.started.length) toast.success(t('已保存并启动：{names}', { names: r.started.join(t('、')) }))
            else if (!r.background) toast.info(t('已保存。当前服务没有运行定时任务，机器人由 main.py 负责，重启它后生效'))
            else if (r.restart_required) toast.info(t('已保存，重启服务后生效'))
            else toast.success(t('已保存'))
            void reload()
          } catch (e) {
            toast.error(e instanceof Error ? e.message : String(e))
          } finally {
            setBusy(false)
          }
        }}
      >
        {t('保存')}
      </Button>
    </div>
  )
}

const WEBHOOK_CHANNELS = ['wechat', 'dingtalk', 'feishu'] as const

export function NotifierForm() {
  const t = useT()
  const { data, error, loading, reload } = useApi<NotifierSettings>(api.notifierSettings)
  const [form, setForm] = useState<Record<string, any>>({})
  const [check, setCheck] = useState<NotifierDiagnosis | null>(null)
  const [busy, setBusy] = useState('')

  useEffect(() => {
    if (data) setForm(structuredClone(data.notifier))
  }, [data])

  if (loading && !data) return <Spinner />
  if (error || !data) return <ErrorBox message={error || t('加载失败')} onRetry={reload} />

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
    const common = { 'aria-label': `${t(data.channels[ch])}-${t(f.label)}`, placeholder: f.placeholder ? t(f.placeholder) : f.placeholder }
    return (
      <Field key={f.key} label={`${t(f.label)}${f.required ? ' *' : ''}`}>
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
            <legend className="px-1 text-sm text-accent">{t('{name}机器人', { name: t(data.channels[ch]) })}</legend>
            <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={!!form[ch]?.enabled} onChange={(e) => set(ch, 'enabled', e.target.checked)} /> {t('启用')}</label>
            <Field label="Webhook"><Input value={form[ch]?.webhook_url ?? ''} onChange={(e) => set(ch, 'webhook_url', e.target.value)} /></Field>
            {ch !== 'wechat' && <Field label={t('加签密钥（可空）')}><Input value={form[ch]?.secret ?? ''} onChange={(e) => set(ch, 'secret', e.target.value)} /></Field>}
            <Button loading={busy === ch} onClick={async () => {
              setBusy(ch)
              const r = await api.testNotifier(ch, payload()).finally(() => setBusy(''))
              if (r.ok) toast.success(t('{name}测试消息已发送', { name: t(data.channels[ch]) }))
              else toast.error(t('{name}发送失败：{error}', { name: t(data.channels[ch]), error: r.error ?? '' }))
            }}>{t('发送测试消息')}</Button>
          </fieldset>
        ))}
      </div>
      <fieldset className="rounded-md border border-line p-3">
        <legend className="px-1 text-sm text-accent">{t('邮件（SMTP）')}</legend>
        <div className="grid gap-3 md:grid-cols-3">
          <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={!!email.enabled} onChange={(e) => set('email', 'enabled', e.target.checked)} /> {t('启用')}</label>
          <Field label={t('SMTP 服务器')}><Input value={email.smtp_host ?? ''} onChange={(e) => set('email', 'smtp_host', e.target.value)} /></Field>
          <Field label={t('端口')}><Input type="number" value={email.smtp_port ?? 465} onChange={(e) => set('email', 'smtp_port', Number(e.target.value))} /></Field>
          <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={email.use_ssl ?? true} onChange={(e) => set('email', 'use_ssl', e.target.checked)} /> {t('SSL（465）；不勾选用 STARTTLS（587）')}</label>
          <Field label={t('账号')}><Input value={email.username ?? ''} onChange={(e) => set('email', 'username', e.target.value)} /></Field>
          <Field label={t('密码/授权码')}><Input type="password" value={email.password ?? ''} onChange={(e) => set('email', 'password', e.target.value)} /></Field>
          <Field label={t('发件人（可空）')}><Input value={email.sender ?? ''} onChange={(e) => set('email', 'sender', e.target.value)} /></Field>
          <Field label={t('收件人（逗号分隔）')}>
            <Input value={(email.to ?? []).join(', ')} onChange={(e) => set('email', 'to', e.target.value.split(/[,，;；]/).map((x) => x.trim()).filter(Boolean))} />
          </Field>
          <div className="flex items-end">
            <Button loading={busy === 'email'} onClick={async () => {
              setBusy('email')
              const r = await api.testNotifier('email', payload()).finally(() => setBusy(''))
              if (r.ok) toast.success(t('测试邮件已发送'))
              else toast.error(t('邮件发送失败：{error}', { error: r.error ?? '' }))
            }}>{t('发送测试邮件')}</Button>
          </div>
        </div>
      </fieldset>
      <div className="grid gap-3 lg:grid-cols-3">
        {extraChannels.map((ch) => (
          <fieldset key={ch} className="space-y-2 rounded-md border border-line p-3">
            <legend className="px-1 text-sm text-accent">{t(data.channels[ch])}</legend>
            <label className="flex items-center gap-2 text-sm"><input type="checkbox" aria-label={`${t(data.channels[ch])}-${t('启用')}`} checked={!!form[ch]?.enabled} onChange={(e) => set(ch, 'enabled', e.target.checked)} /> {t('启用')}</label>
            {data.fields[ch].map((f) => renderField(ch, f))}
            <Button loading={busy === ch} onClick={async () => {
              setBusy(ch)
              const r = await api.testNotifier(ch, payload()).finally(() => setBusy(''))
              if (r.ok) toast.success(t('{name}测试消息已发送', { name: t(data.channels[ch]) }))
              else toast.error(t('{name}发送失败：{error}', { name: t(data.channels[ch]), error: r.error ?? '' }))
            }}>{t('发送测试消息')}</Button>
          </fieldset>
        ))}
      </div>
      <fieldset className="rounded-md border border-line p-3">
        <legend className="px-1 text-sm text-accent">{t('推送路由（都不勾选 = 推送到全部已启用渠道）')}</legend>
        <table className="text-sm">
          <thead>
            <tr><th /> {Object.entries(data.channels).map(([ch, label]) => <th key={ch} className="px-3 font-normal text-muted">{t(label)}</th>)}</tr>
          </thead>
          <tbody>
            {Object.entries(data.kinds).map(([kind, label]) => (
              <tr key={kind}>
                <td className="pr-3 py-1">{t(label)}</td>
                {Object.keys(data.channels).map((ch) => (
                  <td key={ch} className="px-3 text-center">
                    <input type="checkbox" aria-label={`${t(label)}-${ch}`} checked={route(kind).has(ch)} onChange={() => toggleRoute(kind, ch)} />
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
        <div className="mt-3 max-w-xs">
          <Field label={t('免打扰时段（如 22:00-08:00，期间只推送紧急提醒）')}>
            <Input value={form.quiet_hours_text ?? (form.quiet_hours ?? []).join('-')} onChange={(e) => setForm((f) => ({ ...f, quiet_hours_text: e.target.value }))} />
          </Field>
        </div>
      </fieldset>
      <fieldset className="space-y-2 rounded-md border border-line p-3">
        <legend className="px-1 text-sm text-accent">{t('图片推送（把报告渲染成分享图发送，失败时自动改发文字）')}</legend>
        <div className="flex flex-wrap items-center gap-4 text-sm">
          <span className="text-muted">{t('渠道')}</span>
          {(data.image_channels ?? []).map((ch) => (
            <label key={ch} className="flex items-center gap-1">
              <input type="checkbox" aria-label={`${t('图片渠道')}-${t(data.channels[ch] ?? ch)}`} checked={imageChannels.has(ch)} onChange={() => toggleImage('channels', ch)} />
              {t(data.channels[ch] ?? ch)}
            </label>
          ))}
        </div>
        <div className="flex flex-wrap items-center gap-4 text-sm">
          <span className="text-muted">{t('消息类型')}</span>
          {Object.entries(data.kinds).map(([kind, label]) => (
            <label key={kind} className="flex items-center gap-1">
              <input type="checkbox" aria-label={`${t('图片类型')}-${t(label)}`} checked={imageKinds.has(kind)} onChange={() => toggleImage('kinds', kind)} />
              {t(label)}
            </label>
          ))}
        </div>
        <div className="max-w-xs">
          <Field label={t('最大字数（超过时仍发文字）')}>
            <Input type="number" aria-label={t('图片最大字数')} value={image.max_chars ?? 8000} onChange={(e) => setNested('image', 'max_chars', e.target.value === '' ? '' : Number(e.target.value))} />
          </Field>
        </div>
      </fieldset>
      <fieldset className="space-y-2 rounded-md border border-line p-3">
        <legend className="px-1 text-sm text-accent">{t('系统错误通知（定时任务出错时推送，路由见上表「系统错误」）')}</legend>
        <div className="flex flex-wrap items-end gap-4">
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" aria-label={t('系统错误通知')} checked={systemError.enabled ?? true} onChange={(e) => setNested('system_error', 'enabled', e.target.checked)} />
            {t('启用')}
          </label>
          <div className="w-40">
            <Field label={t('同一来源冷却（分钟）')}>
              <Input type="number" aria-label={t('系统错误冷却分钟')} min="0" value={systemError.cooldown_minutes ?? 60} onChange={(e) => setNested('system_error', 'cooldown_minutes', e.target.value === '' ? '' : Number(e.target.value))} />
            </Field>
          </div>
        </div>
      </fieldset>
      {check && (
        <div className="rounded-md border border-line p-3 text-sm">
          {check.channels.filter((c) => c.enabled || (c.configured && c.issues.length)).map((c) => (
            <div key={c.channel}>{t('{label}（{state}）：', { label: t(c.label), state: c.enabled ? t('已启用') : t('未启用') })}{c.issues.length ? <span className="text-warn">{c.issues.join(t('；'))}</span> : <span className="text-down">{t('配置正常')}</span>}</div>
          ))}
          {check.routes.map((r) => <div key={r} className="text-warn">{r}</div>)}
          {!check.channels.some((c) => c.enabled) && <div className="text-muted">{t('还没有启用任何推送渠道')}</div>}
        </div>
      )}
      <div className="flex gap-2">
        <Button onClick={async () => setCheck(await api.diagnoseNotifier(payload()))}>{t('检查配置')}</Button>
        <Button variant="primary" loading={busy === 'save'} onClick={async () => {
          setBusy('save')
          try {
            await api.saveNotifier(payload())
            toast.success(t('推送设置已保存'))
            void reload()
          } catch (e) {
            toast.error(e instanceof Error ? e.message : String(e))
          } finally {
            setBusy('')
          }
        }}>{t('保存')}</Button>
      </div>
    </div>
  )
}

const SEARCH_KEY_PROVIDERS = ['bocha', 'tavily', 'serpapi', 'brave'] as const

const splitLines = (text: string) => text.split(/[\n,]/).map((s) => s.trim()).filter(Boolean)

function SearchForm() {
  const t = useT()
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
  if (error || !data) return <ErrorBox message={error || t('加载失败')} onRetry={reload} />

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
        {t('个股诊断和 AI 问股用它联网搜索最新消息。按下面的顺序依次尝试，失败或无结果换下一个；博查对中文新闻效果较好。Key 可填多个（一行一个）轮换使用，已保存的 Key 显示为掩码，不修改原样保留即可。')}
      </p>
      <label className="flex items-center gap-2 text-sm">
        <input type="checkbox" checked={!!form.enabled} onChange={(e) => setForm({ ...form, enabled: e.target.checked })} /> {t('启用联网搜索')}
      </label>
      <Field label={t('服务顺序（逗号分隔）')} hint={t('可选：{list}', { list: Object.entries(data.providers).map(([k, v]) => `${k}（${t(v)}）`).join(t('、')) })}>
        <Input value={providers} onChange={(e) => setProviders(e.target.value)} />
      </Field>
      <div className="grid gap-3 md:grid-cols-2">
        {SEARCH_KEY_PROVIDERS.map((p) => (
          <Field key={p} label={t('{name} API Key（一行一个）', { name: t(data.providers[p] ?? p) })}>
            <Textarea rows={2} value={keys[p] ?? ''} onChange={(e) => setKeys({ ...keys, [p]: e.target.value })} />
          </Field>
        ))}
        <Field label={t('SearXNG 地址（一行一个）')} hint={t('自建实例需在 settings.yml 开启 json 格式')}>
          <Textarea rows={2} value={keys.searxng ?? ''} onChange={(e) => setKeys({ ...keys, searxng: e.target.value })} />
        </Field>
        <Field label={t('SearXNG 超时（秒）')}>
          <Input type="number" value={form.searxng?.timeout ?? 10} onChange={(e) => setForm({ ...form, searxng: { ...(form.searxng ?? {}), timeout: e.target.value } })} />
        </Field>
      </div>
      <div className="grid gap-3 md:grid-cols-3">
        <Field label={t('每次最多条数')}><Input type="number" value={form.max_results ?? 8} onChange={(e) => setNum('max_results', e.target.value)} /></Field>
        <Field label={t('只保留最近天数')}><Input type="number" value={form.days ?? 7} onChange={(e) => setNum('days', e.target.value)} /></Field>
        <Field label={t('缓存时间（分钟）')}><Input type="number" value={form.cache_minutes ?? 30} onChange={(e) => setNum('cache_minutes', e.target.value)} /></Field>
      </div>
      <div className="flex flex-wrap items-end gap-2">
        <Field label={t('测试查询词')}><Input value={query} onChange={(e) => setQuery(e.target.value)} /></Field>
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
          {t('测试')}
        </Button>
        <Button
          variant="primary"
          loading={busy === 'save'}
          onClick={async () => {
            setBusy('save')
            try {
              await api.saveSearch(build())
              toast.success(t('联网搜索设置已保存'))
              void reload()
            } catch (e) {
              toast.error(e instanceof Error ? e.message : String(e))
            } finally {
              setBusy('')
            }
          }}
        >
          {t('保存')}
        </Button>
      </div>
      {results && (
        <ul className="space-y-1 text-sm">
          {results.length === 0 && <li className="text-muted">{t('没有已配置的搜索服务')}</li>}
          {results.map((r) => (
            <li key={r.provider}>
              <span className={r.ok ? 'text-down' : 'text-up'}>{r.ok ? t('成功') : t('失败')}</span> {t(r.label)}
              {r.ok ? t('：{count} 条，如「{samples}」', { count: r.count, samples: r.samples.join(t('」「')) }) : `：${r.error}`}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

// 后端返回的触发规则（如「每 30 分钟」「工作日 15:30」）按模式翻译，未识别的原样显示
function useTriggerText() {
  const t = useT()
  return (text: string): string => {
    let m = /^每 (\d+) (小时|分钟|秒)$/.exec(text)
    if (m) return t(`每 {n} ${m[2]}`, { n: m[1] })
    m = /^(工作日|每天) (\d{2}:\d{2})$/.exec(text)
    if (m) return t(`${m[1]} {time}`, { time: m[2] })
    return t(text)
  }
}

function SchedulerPanel() {
  const t = useT()
  const triggerText = useTriggerText()
  const status = useApi<SchedulerStatus>(api.scheduler)
  const { run } = useTask()
  const [runningId, setRunningId] = useState('')
  if (status.error) return <ErrorBox message={status.error} />
  if (!status.data) return <Spinner />
  const { running, message, jobs } = status.data
  const runNow = async (job: SchedulerJob) => {
    setRunningId(job.id)
    try {
      await run(() => api.runJob(job.id), { success: t('{name}已运行完成', { name: t(job.name) }) })
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
        {running ? t('本进程正在运行定时任务。') : t(message)}{t('非交易日会跳过行情和分析类任务。')}
      </p>
      <DataTable
        rows={jobs}
        rowKey={(j) => j.id}
        columns={[
          { key: 'name', title: t('任务'), render: (j) => t(j.name) },
          { key: 'trigger', title: t('触发规则'), render: (j) => (j.trigger ? triggerText(j.trigger) : '-') },
          { key: 'next', title: t('下次运行'), render: (j) => (j.next_run_time ? j.next_run_time.replace('T', ' ').slice(0, 19) : '-') },
          { key: 'state', title: t('状态'), render: (j) => (!running ? t('未运行') : j.paused ? t('已暂停') : t('正常')) },
          {
            key: 'op',
            title: t('操作'),
            render: (j) => (
              <Button loading={runningId === j.id} disabled={!!runningId} onClick={() => runNow(j)}>
                {t('立即运行')}
              </Button>
            ),
          },
        ]}
      />
    </div>
  )
}

function BackupPanel() {
  const t = useT()
  const [includeSecrets, setIncludeSecrets] = useState(false)
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<SettingsImportResult | null>(null)

  const pickFile = async (file: File | undefined) => {
    if (file) setText(await file.text())
  }
  const doImport = async () => {
    if (!text.trim()) return toast.error(t('请先选择文件或粘贴配置内容'))
    if (!window.confirm(t('导入会整体覆盖当前 settings.yaml，确定继续吗？'))) return
    setBusy(true)
    try {
      setResult(await api.importSettings(text))
      toast.success(t('配置已导入并生效'))
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }
  return (
    <div className="space-y-6">
      <section className="space-y-2">
        <h3 className="text-sm font-medium">{t('导出配置')}</h3>
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={includeSecrets} onChange={(e) => setIncludeSecrets(e.target.checked)} />
          {t('包含密钥')}
        </label>
        {includeSecrets && <p className="text-xs text-up">{t('风险提示：导出文件将包含 API Key、Webhook 等明文密钥，请妥善保管，不要分享或提交到仓库。')}</p>}
        <a className="inline-block rounded-md border border-line px-3 py-1.5 text-sm" href={api.exportSettingsUrl(includeSecrets)} download>
          {t('导出')}
        </a>
        <p className="text-xs text-muted">{t('不包含密钥时，密钥导出为 ******，导入时会沿用当前配置里的值。')}</p>
      </section>
      <section className="space-y-2">
        <h3 className="text-sm font-medium">{t('导入配置')}</h3>
        <input type="file" accept=".yaml,.yml,text/yaml" aria-label={t('选择配置文件')} onChange={(e) => pickFile(e.target.files?.[0])} />
        <Textarea rows={10} value={text} onChange={(e) => setText(e.target.value)} placeholder={t('或在此粘贴 YAML 配置')} aria-label={t('配置内容')} />
        <Button variant="primary" loading={busy} onClick={doImport}>
          {t('导入')}
        </Button>
        {result && (
          <div className="space-y-1 text-sm">
            <div>{t('已导入配置段：')}{result.sections.join(t('、')) || t('无')}</div>
            <div>{t('还原密钥：{n} 项', { n: result.restored })}</div>
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
  const t = useT()
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
  if (error || !data) return <ErrorBox message={error || t('加载失败')} onRetry={reload} />

  const update = (i: number, patch: Partial<IntelligenceSource>) => setSources(sources.map((s, j) => (j === i ? { ...s, ...patch } : s)))
  const test = async (i: number) => {
    setTests((prev) => ({ ...prev, [i]: 'loading' }))
    try {
      const r = await api.testIntelligenceSource(sources[i].url)
      setTests((prev) => ({ ...prev, [i]: r }))
    } catch (e) {
      setTests((prev) => ({ ...prev, [i]: { ok: false, title: '', count: 0, samples: [], error: e instanceof Error ? e.message : String(e) } }))
    }
  }
  const add = () => {
    const name = draft.name.trim()
    const url = draft.url.trim()
    if (!name || !url) return toast.error(t('请填写名称和地址'))
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
      toast.success(t('已保存'))
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
        {t('订阅 RSS / Atom 地址，采集到的文章会进入实时资讯流和舆情分析。可用 RSSHub 等工具为财经媒体生成 RSS 地址；与交易日无关，全天按间隔采集。')}
      </p>
      <label className="flex items-center gap-2 text-sm">
        <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} /> {t('启用资讯源采集')}
      </label>
      <div className="grid gap-3 md:grid-cols-2">
        <Field label={t('采集间隔（分钟，最少 5）')}><Input type="number" value={interval} onChange={(e) => setIntervalMinutes(e.target.value)} /></Field>
        <Field label={t('每源每次最多条数（1~200）')}><Input type="number" value={maxItems} onChange={(e) => setMaxItems(e.target.value)} /></Field>
      </div>
      <div className="space-y-2">
        {sources.length === 0 && <p className="text-sm text-muted">{t('还没有资讯源，在下面添加')}</p>}
        {sources.map((s, i) => {
          const tr = tests[i]
          return (
            <div key={i} className="space-y-1 rounded border border-line p-2">
              <div className="flex flex-wrap items-center gap-2">
                <label className="flex items-center gap-1 text-sm">
                  <input type="checkbox" aria-label={`${s.name}-${t('启用')}`} checked={s.enabled} onChange={(e) => update(i, { enabled: e.target.checked })} /> {t('启用')}
                </label>
                <Input className="w-32" aria-label={t('名称')} value={s.name} onChange={(e) => update(i, { name: e.target.value })} />
                <Input className="min-w-0 flex-1" aria-label={t('地址')} value={s.url} onChange={(e) => update(i, { url: e.target.value })} />
                <Button onClick={() => void test(i)} loading={tr === 'loading'}>{t('测试')}</Button>
                <Button variant="danger" onClick={() => { setSources(sources.filter((_, j) => j !== i)); setTests({}) }}>{t('删除')}</Button>
              </div>
              {tr && tr !== 'loading' && (
                <p className="text-xs">
                  <span className={tr.ok ? 'text-down' : 'text-up'}>{tr.ok ? t('成功') : t('失败')}</span>
                  {tr.ok ? t('：{title}，{count} 条，如「{samples}」', { title: tr.title || t('（无标题）'), count: tr.count, samples: tr.samples.join(t('」「')) }) : `：${tr.error}`}
                </p>
              )}
            </div>
          )
        })}
      </div>
      <div className="flex flex-wrap items-end gap-2">
        <Field label={t('名称')}><Input className="w-32" value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} /></Field>
        <Field label={t('RSS 地址')}><Input className="w-72" placeholder="https://example.com/feed.xml" value={draft.url} onChange={(e) => setDraft({ ...draft, url: e.target.value })} /></Field>
        <Button onClick={add}>{t('添加')}</Button>
      </div>
      <div className="flex gap-2">
        <Button variant="primary" loading={saving} onClick={() => void save()}>{t('保存')}</Button>
        <Button
          loading={collect.running}
          onClick={() => void collect.run(() => api.collectRss(), { success: (r) => t('采集完成，新增 {inserted} 条（抓到 {fetched} 条）', { inserted: r.inserted, fetched: r.fetched }) }).catch(() => undefined)}
        >
          {t('立即采集')}
        </Button>
      </div>
    </div>
  )
}

function SecurityForm() {
  const t = useT()
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
          {t('Web 登录：')}<b className={s.auth_enabled ? 'text-down' : 'text-warn'}>{s.auth_enabled ? t('已开启') : t('未开启（只允许本机访问）')}</b>
        </p>
        <p className="mb-3 text-xs text-muted">{t('开启登录后，可以把 web.host 改成 0.0.0.0，让手机等局域网设备访问。')}</p>
        {!s.auth_enabled && !s.password_set && (
          <Field label={t('设置访问密码（至少 6 位）')}><Input type="password" value={password} onChange={(e) => setPassword(e.target.value)} /></Field>
        )}
        <Button
          className="mt-2"
          variant={s.auth_enabled ? 'danger' : 'primary'}
          onClick={async () => {
            try {
              await api.setWebAuth(!s.auth_enabled, password)
              toast.success(s.auth_enabled ? t('已关闭登录') : t('已开启登录'))
              setPassword('')
              void status.reload()
            } catch (e) {
              toast.error(e instanceof Error ? e.message : String(e))
            }
          }}
        >
          {s.auth_enabled ? t('关闭登录') : t('开启登录')}
        </Button>
      </div>
      {s.password_set && (
        <div className="space-y-2">
          <p className="font-medium">{t('修改密码')}</p>
          <Field label={t('当前密码')}><Input type="password" value={current} onChange={(e) => setCurrent(e.target.value)} /></Field>
          <Field label={t('新密码（至少 6 位）')}><Input type="password" value={next} onChange={(e) => setNext(e.target.value)} /></Field>
          <Button onClick={async () => {
            try {
              await api.changePassword(current, next)
              toast.success(t('密码已修改'))
              setCurrent('')
              setNext('')
            } catch (e) {
              toast.error(e instanceof Error ? e.message : String(e))
            }
          }}>{t('修改密码')}</Button>
        </div>
      )}
    </div>
  )
}
