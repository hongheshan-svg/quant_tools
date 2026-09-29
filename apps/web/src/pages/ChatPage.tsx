// AI 问股：会话列表 + 多轮对话；提问作为后台任务执行，显示正在查询的数据
import { Download, Plus, Send, Share2, Trash2 } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { api } from '@/api/endpoints'
import type { ChatSession, ChatSkill, ChatTurn } from '@/api/types'
import { Markdown } from '@/components/Markdown'
import { Button, Card, PageHeader, Select, Spinner, Textarea } from '@/components/ui'
import { useApi } from '@/hooks/useApi'
import { progressText, useTask } from '@/hooks/useTask'
import { toast } from '@/stores/toast'
import { cn } from '@/utils/cn'

const EXAMPLES = ['中远海控现在能买吗？止损放哪？', '今天的主线是什么，龙头是谁？', '我的持仓风险大吗？', '用龙回头的标准看看招商轮船']

function TurnView({ turn }: { turn: ChatTurn }) {
  const labels = [...new Set(turn.tools.map((t) => t.label))]
  return (
    <div className="space-y-2">
      <div className="ml-auto w-fit max-w-[85%] rounded-lg bg-accent-strong/20 px-3 py-2 text-sm">{turn.question}</div>
      <div className="text-xs text-muted">
        {turn.asked_at} ｜ {turn.perspective}
        {labels.length > 0 && <> ｜ 查询：{labels.join('、')}</>}
      </div>
      <div className="rounded-lg border border-line bg-panel-2 px-3 py-2">
        {turn.answer ? <Markdown text={turn.answer} /> : <p className="text-sm text-danger">{turn.error || '没有回答'}</p>}
      </div>
    </div>
  )
}

const CATEGORY_LABELS: Record<string, string> = {
  trend: '趋势', pattern: '形态', reversal: '反转', emotion: '情绪', framework: '框架', fundamental: '基本面',
}

// 按分类分组，分组顺序固定，组内保持接口返回的优先级顺序
function groupSkills(list: ChatSkill[]): [string, ChatSkill[]][] {
  const order = Object.keys(CATEGORY_LABELS)
  const map = new Map<string, ChatSkill[]>()
  for (const s of list) map.set(s.category, [...(map.get(s.category) ?? []), s])
  return [...map.entries()].sort(([a], [b]) => (order.indexOf(a) + 99) % 99 - (order.indexOf(b) + 99) % 99)
}

export function ChatPage() {
  const { sessionId } = useParams()
  const navigate = useNavigate()
  const sessions = useApi(api.chatSessions)
  const skills = useApi(api.skills)
  const [session, setSession] = useState<ChatSession | null>(null)
  const [perspective, setPerspective] = useState('综合')
  const [question, setQuestion] = useState('')
  const [pending, setPending] = useState('')
  const ask = useTask<ChatTurn>()
  const groups = groupSkills(skills.data ?? [])
  const current = (skills.data ?? []).find((s) => s.display_name === perspective)
  const bottom = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!sessionId) {
      setSession(null)
      return
    }
    api.chatSession(sessionId).then((s) => {
      setSession(s)
      setPerspective(s.perspective || '综合')
    }).catch(() => navigate('/chat'))
  }, [sessionId, navigate])

  useEffect(() => bottom.current?.scrollIntoView?.({ behavior: 'smooth' }), [session?.turns.length, pending])

  const send = async (text = question) => {
    const q = text.trim()
    if (!q || ask.running) return
    let current = session
    if (!current) {
      current = await api.createChat(perspective)
      setSession(current)
      navigate(`/chat/${current.id}`, { replace: true })
    }
    setQuestion('')
    setPending(q)
    try {
      const turn = await ask.run(() => api.ask(current!.id, q, perspective))
      setSession((s) => (s ? { ...s, turns: [...s.turns, turn] } : s))
      void sessions.reload()
    } catch {
      setQuestion(q)
    } finally {
      setPending('')
    }
  }

  const remove = async (id: string) => {
    await api.deleteChat(id)
    if (id === sessionId) navigate('/chat')
    void sessions.reload()
  }

  return (
    <div>
      <PageHeader title="AI 问股" description="多轮追问；AI 按需查询行情、技术面、资金流、筹码、业绩、新闻公告、主线、大盘、持仓和自选股后回答（只读，不会下单）" />
      <div className="grid gap-4 lg:grid-cols-[240px_1fr]">
        <Card title="会话" actions={<Button variant="ghost" aria-label="新会话" onClick={() => navigate('/chat')}><Plus className="size-4" /></Button>} bodyClassName="p-2">
          <ul className="space-y-1">
            {(sessions.data ?? []).map((s) => (
              <li key={s.id} className={cn('group flex items-center justify-between rounded-md px-2 py-1.5 text-sm', s.id === sessionId ? 'bg-panel-2 text-accent' : 'hover:bg-panel-2')}>
                <button type="button" className="min-w-0 flex-1 truncate text-left" onClick={() => navigate(`/chat/${s.id}`)} title={s.title}>
                  {s.title}
                  <div className="text-xs text-muted">{s.updated_at} · {s.turns} 问</div>
                </button>
                <button type="button" aria-label="删除会话" className="hidden text-muted group-hover:block hover:text-danger" onClick={() => remove(s.id)}>
                  <Trash2 className="size-3.5" />
                </button>
              </li>
            ))}
            {!sessions.data?.length && <li className="p-2 text-xs text-muted">还没有会话</li>}
          </ul>
        </Card>
        <Card
          title={session?.title ?? '新会话'}
          actions={
            session && session.turns.length > 0 ? (
              <>
                <a href={api.exportChatUrl(session.id)} download={`AI问股_${session.id.slice(0, 6)}.md`} className="inline-flex items-center gap-1 text-xs text-muted hover:text-text">
                  <Download className="size-3.5" /> 导出
                </a>
                <Button variant="ghost" onClick={() => api.pushChat(session.id).then((r) => (r.pushed ? toast.success('已推送') : toast.info(r.reason ?? '未推送'))).catch((e) => toast.error(String(e.message ?? e)))}>
                  <Share2 className="size-3.5" /> 推送
                </Button>
              </>
            ) : undefined
          }
        >
          <div className="max-h-[60vh] min-h-64 space-y-5 overflow-y-auto pr-1">
            {!session?.turns.length && !pending && (
              <div className="space-y-2 text-sm">
                <p className="text-muted">可以这样问：</p>
                {EXAMPLES.map((e) => (
                  <button key={e} type="button" className="block text-left text-accent hover:underline" onClick={() => void send(e)}>{e}</button>
                ))}
              </div>
            )}
            {session?.turns.map((t, i) => <TurnView key={i} turn={t} />)}
            {pending && (
              <div className="space-y-2">
                <div className="ml-auto w-fit max-w-[85%] rounded-lg bg-accent-strong/20 px-3 py-2 text-sm">{pending}</div>
                <Spinner text={progressText(ask.progress) || '思考中…'} />
              </div>
            )}
            <div ref={bottom} />
          </div>
          <div className="mt-3 flex flex-col gap-2 sm:flex-row sm:items-end">
            <div className="flex flex-col gap-1">
              <Select value={perspective} onChange={(e) => setPerspective(e.target.value)} aria-label="分析视角" title={current?.description}>
                {groups.length === 0 && <option value="综合">综合</option>}
                {groups.map(([category, list]) => (
                  <optgroup key={category} label={CATEGORY_LABELS[category] ?? category}>
                    {list.map((s) => <option key={s.name} value={s.display_name} title={s.description}>{s.display_name}</option>)}
                  </optgroup>
                ))}
              </Select>
              {current?.description && <p className="max-w-56 text-xs text-muted">{current.description}</p>}
            </div>
            <Textarea
              className="min-h-10 flex-1"
              rows={2}
              value={question}
              placeholder="输入问题，Enter 发送，Shift+Enter 换行"
              onChange={(e) => setQuestion(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault()
                  void send()
                }
              }}
            />
            <Button variant="primary" loading={ask.running} onClick={() => void send()} aria-label="发送">
              <Send className="size-4" /> 发送
            </Button>
          </div>
        </Card>
      </div>
    </div>
  )
}
