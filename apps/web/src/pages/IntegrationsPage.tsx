import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '@/api/endpoints'
import type { IntegrationSettings, RuntimeAnswer } from '@/api/types'
import { Button, Card, ErrorBox, Field, Input, PageHeader, Select, Spinner } from '@/components/ui'
import { SettingsDraftBoundary, SettingsNavigationGuard } from '@/components/SettingsDraftBoundary'
import { useApi } from '@/hooks/useApi'
import { useTask } from '@/hooks/useTask'
import { toast } from '@/stores/toast'

export function IntegrationsPage() {
  return <div className="space-y-4"><SettingsNavigationGuard /><PageHeader title="扩展接入" description="宏观数据、本地模型与海外服务" />
    <MacroTools /><RuntimePanel />
    <SettingsDraftBoundary id="integrations" paths={['/settings/integrations']}><IntegrationForm /></SettingsDraftBoundary>
    <Card><h2 className="font-semibold">外部助手</h2><p className="mt-2 text-sm text-muted">OpenClaw 和 Grok Bot 通过同一套 API 读取报告、发起分析和查询有效信号。部署与调用示例见仓库 docs/integrations.md；使用应用访问令牌认证。</p><Link to="/settings" className="text-sm text-accent">打开模型与访问设置</Link></Card>
  </div>
}

function IntegrationForm() {
  const { data, error, reload } = useApi(api.integrationSettings)
  const [form, setForm] = useState<IntegrationSettings['settings']>({})
  const [busy, setBusy] = useState(false)
  useEffect(() => { if (data) setForm(data.settings) }, [data])
  if (error) return <ErrorBox message={error} />
  if (!data) return <Spinner />
  return <Card><h2 className="mb-3 font-semibold">连接配置</h2><p className="mb-3 text-sm text-muted">公开宏观数据无需密钥。本地 CLI 使用已安装程序的登录配置；“AI 模型”中选择对应平台后用于报告生成。</p>
    <div className="grid gap-4 lg:grid-cols-2">{data.groups.map((group) => <fieldset key={group.key} className="space-y-2 rounded border border-line p-3"><legend>{group.label}</legend>{group.fields.map((field) => <Field key={field.key} label={field.label}><Input aria-label={`${group.label}-${field.label}`} type={field.secret ? 'password' : field.type} value={form[group.key]?.[field.key] ?? ''} onChange={(e) => setForm((old) => ({ ...old, [group.key]: { ...old[group.key], [field.key]: field.type === 'number' ? Number(e.target.value) : e.target.value } }))} /></Field>)}</fieldset>)}</div>
    <Button className="mt-3" loading={busy} onClick={async () => { setBusy(true); try { await api.saveIntegrationSettings(form); await reload(); toast.success('接入配置已保存') } catch (e) { toast.error(String(e)) } finally { setBusy(false) } }}>保存接入配置</Button>
  </Card>
}

function MacroTools() {
  const catalogue = useApi(api.macroOperations)
  const [selected, setSelected] = useState('data_catalogue')
  const [values, setValues] = useState<Record<string, string>>({ currency: 'USD' })
  const [result, setResult] = useState<Record<string, unknown> | null>(null)
  const [busy, setBusy] = useState(false)
  const operation = catalogue.data?.find((o) => o.name === selected)
  async function execute() {
    if (!operation) return
    setBusy(true)
    try {
      const arguments_: Record<string, unknown> = {}
      for (const [key, value] of Object.entries(values)) {
        if (!value || !operation.parameters.properties?.[key]) continue
        const type = operation.parameters.properties[key].type
        arguments_[key] = type === 'integer' || type === 'number' ? Number(value) : ['object', 'array', 'boolean'].includes(type ?? '') ? JSON.parse(value) : value
      }
      setResult(await api.executeMacro(selected, arguments_))
    } catch (e) { toast.error(String(e)) } finally { setBusy(false) }
  }
  return <Card><h2 className="mb-3 font-semibold">宏观数据工具 <span className="text-sm text-muted">{catalogue.data?.length ?? 0} 项</span></h2>
    {catalogue.error && <ErrorBox message={catalogue.error} />}
    <Field label="查询内容"><Select aria-label="宏观查询内容" className="w-full" value={selected} onChange={(e) => { setSelected(e.target.value); setValues({ currency: 'USD' }); setResult(null) }}>{catalogue.data?.map((o) => <option key={o.name} value={o.name}>{o.name} · {o.description}</option>)}</Select></Field>
    <div className="my-3 grid gap-3 md:grid-cols-3">{Object.entries(operation?.parameters.properties ?? {}).map(([key, spec]) => <Field key={key} label={`${key}${operation?.parameters.required?.includes(key) ? ' *' : ''}`} hint={spec.description}>{spec.enum ? <Select value={values[key] ?? ''} onChange={(e) => setValues((old) => ({ ...old, [key]: e.target.value }))}><option value="">默认</option>{spec.enum.map((option) => <option key={option}>{option}</option>)}</Select> : <Input aria-label={`宏观参数 ${key}`} value={values[key] ?? ''} placeholder={['object', 'array'].includes(spec.type ?? '') ? 'JSON' : ''} onChange={(e) => setValues((old) => ({ ...old, [key]: e.target.value }))} />}</Field>)}</div>
    <Button disabled={!operation} loading={busy} onClick={() => void execute()}>查询宏观数据</Button>
    {result && <details open className="mt-3"><summary>查询结果、时间与来源</summary><pre className="max-h-96 overflow-auto whitespace-pre-wrap p-2 text-xs">{JSON.stringify(result, null, 2)}</pre></details>}
  </Card>
}

function RuntimePanel() {
  const runtime = useApi(api.runtimeStatus)
  const sessions = useApi(api.runtimeSessions)
  const [question, setQuestion] = useState('检查我的自选股已有研究和行情质量。')
  const [session, setSession] = useState('')
  const [answer, setAnswer] = useState<RuntimeAnswer | null>(null)
  const task = useTask<RuntimeAnswer>()
  async function sendQuestion() {
    try {
      const result = await task.run(async () => {
        const started = await api.runtimeAsk(question, session || undefined)
        setSession(started.session_id)
        return started
      })
      if (result) { setSession(result.session_id); setAnswer(result) }
    } catch { /* useTask 已显示失败或取消原因 */ }
    finally { await sessions.reload() }
  }
  return <Card><h2 className="mb-3 font-semibold">本地模型状态</h2>{runtime.error && <ErrorBox message={runtime.error} />}
    <div className="grid gap-3 md:grid-cols-2">{runtime.data?.map((row) => <div className="rounded border border-line p-3 text-sm" key={row.provider}><strong>{row.provider}</strong><p className="mt-1 text-muted">{row.reason}</p></div>)}</div>
    <Button className="mt-3" onClick={() => void runtime.reload()}>重新检查安装</Button>
    <h3 className="mb-2 mt-5 font-semibold">Codex App Server 只读问股</h3><p className="mb-2 text-sm text-muted">仅使用本地研究工具，支持继续会话；不开放文件修改、命令执行或交易。</p>
    <Field label="继续已有会话"><Select disabled={task.running} value={session} onChange={async (e) => { const id = e.target.value; setSession(id); if (!id) { setAnswer(null); return } try { const row = await api.runtimeSession(id); const message = [...row.messages].reverse().find((m) => m.role === 'assistant'); setAnswer(message ? { answer: message.content, session_id: id, tools: row.last_run?.tools ?? [] } : null) } catch (error) { toast.error(String(error)) } }}><option value="">新会话</option>{sessions.data?.map((row) => <option key={row.session_id} value={row.session_id}>{row.label} · {row.status}</option>)}</Select></Field>
    <Input aria-label="本地模型问题" className="w-full" value={question} onChange={(e) => setQuestion(e.target.value)} />
    <div className="my-2 flex gap-2"><Button loading={task.running} onClick={() => void sendQuestion()}>发送</Button><Button disabled={!task.running || !session} onClick={async () => { try { const result = await api.runtimeCancel(session); if (!result.ok) toast.info("任务尚未开始或已经结束") } catch (error) { toast.error(String(error)) } }}>取消</Button><Button disabled={task.running || !session} onClick={() => { setSession(''); setAnswer(null) }}>新会话</Button></div>
    {answer && <div className="whitespace-pre-wrap rounded bg-panel-2 p-3 text-sm">{answer.answer}<p className="mt-3 text-xs text-muted">已使用工具：{answer.tools.map((t) => t.name).join('、') || '无'} · 下一条消息继续此会话</p></div>}
  </Card>
}
