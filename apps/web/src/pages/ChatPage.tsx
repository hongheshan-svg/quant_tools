// AI 问股：会话列表 + 多轮对话；提问作为后台任务执行，显示正在查询的数据
import { Download, Plus, Send, Share2, Square, Trash2 } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { ApiError } from '@/api/client'
import { api } from '@/api/endpoints'
import type { ChatSession, ChatSkill, ChatTurn } from '@/api/types'
import { Markdown } from '@/components/Markdown'
import { Button, Card, PageHeader, Select, Spinner, Textarea } from '@/components/ui'
import { useApi } from '@/hooks/useApi'
import { progressText, useTask } from '@/hooks/useTask'
import { toast } from '@/stores/toast'
import { useT } from '@/i18n'
import { cn } from '@/utils/cn'

// 流式回答进行中的临时状态
interface Live {
  status: string
  tools: string[]
  answer: string
}

const EXAMPLES = ['中远海控现在能买吗？止损放哪？', '今天的主线是什么，龙头是谁？', '我的持仓风险大吗？', '用龙回头的标准看看招商轮船']

function TurnView({ turn }: { turn: ChatTurn }) {
  const t = useT()
  const labels = [...new Set(turn.tools.map((x) => x.label))]
  return (
    <div className="space-y-2">
      <div className="ml-auto w-fit max-w-[85%] rounded-lg bg-accent-strong/20 px-3 py-2 text-sm">{turn.question}</div>
      <div className="text-xs text-muted">
        {turn.asked_at} ｜ {turn.perspective}
        {labels.length > 0 && <> ｜ {t('查询：{list}', { list: labels.join('、') })}</>}
      </div>
      <div className="rounded-lg border border-line bg-panel-2 px-3 py-2">
        {turn.answer ? <Markdown text={turn.answer} /> : <p className="text-sm text-danger">{t(turn.error || '没有回答')}</p>}
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
  const t = useT()
  const { sessionId } = useParams()
  const navigate = useNavigate()
  const sessions = useApi(api.chatSessions)
  const skills = useApi(api.skills)
  const [session, setSession] = useState<ChatSession | null>(null)
  const [perspective, setPerspective] = useState('综合')
  const [question, setQuestion] = useState('')
  const [pending, setPending] = useState('')
  const [live, setLive] = useState<Live | null>(null)
  const abortRef = useRef<AbortController | null>(null)
  const ask = useTask<ChatTurn>()
  const groups = groupSkills(skills.data ?? [])
  const current = (skills.data ?? []).find((s) => s.display_name === perspective)
  const bottom = useRef<HTMLDivElement>(null)
  const createdRef = useRef('') // 本页刚新建的会话：跳转到它的地址时不再从后台加载，否则会覆盖正在进行的这一轮

  useEffect(() => {
    if (!sessionId) {
      setSession(null)
      return
    }
    if (createdRef.current === sessionId) {
      createdRef.current = ''
      return
    }
    api.chatSession(sessionId).then((s) => {
      setSession(s)
      setPerspective(s.perspective || '综合')
    }).catch(() => navigate('/chat'))
  }, [sessionId, navigate])

  // 新版 Chromium 的 scrollIntoView 返回 Promise，不能直接作为 effect 的返回值（React 会把它当清理函数调用而报错）
  useEffect(() => {
    bottom.current?.scrollIntoView?.({ behavior: 'smooth' })
  }, [session?.turns.length, pending, live?.answer])

  const send = async (text = question) => {
    const q = text.trim()
    if (!q || ask.running || pending) return
    let current = session
    if (!current) {
      current = await api.createChat(perspective)
      createdRef.current = current.id
      setSession(current)
      navigate(`/chat/${current.id}`, { replace: true })
    }
    setQuestion('')
    setPending(q)
    const sid = current.id
    const controller = new AbortController()
    abortRef.current = controller
    const state: Live = { status: t('思考中'), tools: [], answer: '' }
    setLive({ ...state })
    let received = false
    let doneTurn: ChatTurn | null = null
    let streamError = ''
    try {
      await api.askStream(sid, q, perspective, {
        signal: controller.signal,
        onEvent: (ev) => {
          received = true
          if (ev.type === 'status') state.status = ev.text
          else if (ev.type === 'tool') state.tools = [...state.tools, ev.label]
          else if (ev.type === 'delta') state.answer += ev.text
          else if (ev.type === 'error') streamError = ev.message
          else if (ev.type === 'done') doneTurn = { ...ev.turn, tools: ev.turn.tools.map((x) => ({ ...x, result: '' })) }
          setLive({ ...state })
        },
      })
      if (doneTurn) {
        const turn: ChatTurn = doneTurn
        setSession((s) => (s ? { ...s, turns: [...s.turns, turn] } : s))
        void sessions.reload()
        if (turn.error && !turn.answer) {
          // 失败的一轮不保存：提示原因并把问题放回输入框，改好设置后可以直接重发
          toast.error(turn.error)
          setQuestion(q)
        }
      } else if (streamError) {
        toast.error(streamError)
        setQuestion(q)
      }
    } catch (e) {
      if (controller.signal.aborted) {
        // 用户点了「停止」：保留已生成的部分回答
        const turn: ChatTurn = {
          question: q, answer: state.answer, perspective, tools: [], error: t('已取消'),
          asked_at: new Date().toLocaleString('sv').slice(0, 16),
        }
        setSession((s) => (s ? { ...s, turns: [...s.turns, turn] } : s))
        void sessions.reload()
      } else if (e instanceof ApiError && e.status === 401) {
        setQuestion(q)
      } else if (!received || (e instanceof ApiError && e.status === 404)) {
        // 流式不可用：回退到后台任务轮询
        try {
          const turn = await ask.run(() => api.ask(sid, q, perspective))
          setSession((s) => (s ? { ...s, turns: [...s.turns, turn] } : s))
          void sessions.reload()
        } catch {
          setQuestion(q)
        }
      } else {
        toast.error(e instanceof Error ? e.message : t('连接中断'))
        setQuestion(q)
      }
    } finally {
      abortRef.current = null
      setLive(null)
      setPending('')
    }
  }

  const stop = () => {
    const controller = abortRef.current
    if (!controller || !session) return
    void api.cancelChat(session.id).catch(() => undefined)
    controller.abort()
  }

  const remove = async (id: string) => {
    await api.deleteChat(id)
    if (id === sessionId) navigate('/chat')
    void sessions.reload()
  }

  return (
    <div>
      <PageHeader title={t('AI 问股')} description={t('多轮追问；AI 按需查询行情、技术面、资金流、筹码、业绩、新闻公告、主线、大盘、持仓和自选股后回答（只读，不会下单）')} />
      <div className="grid gap-4 lg:grid-cols-[240px_1fr]">
        <Card title={t('会话')} actions={<Button variant="ghost" aria-label={t('新会话')} onClick={() => navigate('/chat')}><Plus className="size-4" /></Button>} bodyClassName="p-2">
          <ul className="space-y-1">
            {(sessions.data ?? []).map((s) => (
              <li key={s.id} className={cn('group flex items-center justify-between rounded-md px-2 py-1.5 text-sm', s.id === sessionId ? 'bg-panel-2 text-accent' : 'hover:bg-panel-2')}>
                <button type="button" className="min-w-0 flex-1 truncate text-left" onClick={() => navigate(`/chat/${s.id}`)} title={s.title}>
                  {s.title}
                  <div className="text-xs text-muted">{s.updated_at} · {t('{n} 问', { n: s.turns })}</div>
                </button>
                <button type="button" aria-label={t('删除会话')} className="hidden text-muted group-hover:block hover:text-danger" onClick={() => remove(s.id)}>
                  <Trash2 className="size-3.5" />
                </button>
              </li>
            ))}
            {!sessions.data?.length && <li className="p-2 text-xs text-muted">{t('还没有会话')}</li>}
          </ul>
        </Card>
        <Card
          title={session?.title ?? t('新会话')}
          actions={
            session && session.turns.length > 0 ? (
              <>
                <a href={api.exportChatUrl(session.id)} download={`AI问股_${session.id.slice(0, 6)}.md`} className="inline-flex items-center gap-1 text-xs text-muted hover:text-text">
                  <Download className="size-3.5" /> {t('导出')}
                </a>
                <Button variant="ghost" onClick={() => api.pushChat(session.id).then((r) => (r.pushed ? toast.success(t('已推送')) : toast.info(r.reason ?? t('未推送')))).catch((e) => toast.error(String(e.message ?? e)))}>
                  <Share2 className="size-3.5" /> {t('推送')}
                </Button>
              </>
            ) : undefined
          }
        >
          <div className="max-h-[60vh] min-h-64 space-y-5 overflow-y-auto pr-1">
            {!session?.turns.length && !pending && (
              <div className="space-y-2 text-sm">
                <p className="text-muted">{t('可以这样问：')}</p>
                {EXAMPLES.map((e) => (
                  <button key={e} type="button" className="block text-left text-accent hover:underline" onClick={() => void send(e)}>{t(e)}</button>
                ))}
              </div>
            )}
            {session?.turns.map((turn, i) => <TurnView key={i} turn={turn} />)}
            {pending && (
              <div className="space-y-2">
                <div className="ml-auto w-fit max-w-[85%] rounded-lg bg-accent-strong/20 px-3 py-2 text-sm">{pending}</div>
                {live?.tools.length ? (
                  <div className="flex flex-wrap gap-1 text-xs text-muted">
                    {live.tools.map((label, i) => <span key={i} className="rounded border border-line px-1.5 py-0.5">{label}</span>)}
                  </div>
                ) : null}
                {live?.answer ? (
                  <div className="rounded-lg border border-line bg-panel-2 px-3 py-2"><Markdown text={live.answer} /></div>
                ) : null}
                <Spinner text={live ? live.status : progressText(ask.progress) || t('思考中…')} />
              </div>
            )}
            <div ref={bottom} />
          </div>
          <div className="mt-3 flex flex-col gap-2 sm:flex-row sm:items-end">
            <div className="flex flex-col gap-1">
              <Select value={perspective} onChange={(e) => setPerspective(e.target.value)} aria-label={t('分析视角')} title={current?.description}>
                {groups.length === 0 && <option value="综合">{t('综合')}</option>}
                {groups.map(([category, list]) => (
                  <optgroup key={category} label={t(CATEGORY_LABELS[category] ?? category)}>
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
              placeholder={t('输入问题，Enter 发送，Shift+Enter 换行')}
              onChange={(e) => setQuestion(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault()
                  void send()
                }
              }}
            />
            {live ? (
              <Button onClick={stop} aria-label={t('停止')}>
                <Square className="size-4" /> {t('停止')}
              </Button>
            ) : (
              <Button variant="primary" loading={ask.running} onClick={() => void send()} aria-label={t('发送')}>
                <Send className="size-4" /> {t('发送')}
              </Button>
            )}
          </div>
        </Card>
      </div>
    </div>
  )
}
