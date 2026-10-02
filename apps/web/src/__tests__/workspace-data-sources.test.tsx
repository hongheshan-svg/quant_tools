import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AppRoutes } from '@/App'
import { useTaskStore } from '@/stores/tasks'

const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { 'content-type': 'application/json' } })
const diagnosis = (code: string, sentence: string) => ({ code, name: code, score: 60, action_label: '观望', one_sentence: sentence, guardrails: [], data_quality: {}, created_at: '2026-10-02 08:00', trade_date: '2026-09-30' })
const workspace = { today: '2026-10-02', analyzed_today: 1, watchlist: [
  { code: '600519', name: '贵州茅台', close: 1500, change_pct: 1, trade_date: '2026-09-30', quote_source: '腾讯财经', diagnosis: null },
  { code: '000001', name: '平安银行', close: 12, change_pct: -1, trade_date: '2026-09-30', quote_source: '新浪', diagnosis: null },
], today_reports: [], recent_reports: [{ id: 42, code: '600519', name: '贵州茅台', score: 60, summary: '历史摘要', created_at: '2026-10-01 16:30' }] }

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  useTaskStore.setState({ tasks: {} })
})

function renderAt(path = '/') { return render(<MemoryRouter initialEntries={[path]}><AppRoutes authEnabled={false} /></MemoryRouter>) }

describe('首页研究工作台', () => {
  it('显示自选、报告及来源，按勾选子集分析且不推送', async () => {
    const calls: { url: string; body?: string }[] = []
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      calls.push({ url, body: typeof init?.body === 'string' ? init.body : undefined })
      if (url.includes('/watchlist/workspace')) return json(workspace)
      if (url.includes('/600519/diagnosis')) return json(diagnosis('600519', '当前报告正文'))
      if (url.includes('/watchlist/report/selected')) return json({ id: 'batch', created_at: '', status: 'done', result: { done: 1, total: 1, pushed: false } })
      return json([])
    }))
    renderAt()
    expect(await screen.findByText('当前报告正文')).toBeInTheDocument()
    expect(screen.getByText('2026-09-30 · 腾讯财经')).toBeInTheDocument()
    fireEvent.click(screen.getByLabelText('选择股票 600519'))
    fireEvent.click(screen.getByRole('button', { name: '分析选中（1）' }))
    await waitFor(() => expect(calls.find((c) => c.url.includes('/watchlist/report/selected'))?.body).toBe(JSON.stringify({ codes: ['600519'] })))
    expect(calls.some((c) => c.url.includes('push=true'))).toBe(false)
  })

  it('历史报告按记录 ID 打开，导出对应记录', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.includes('/watchlist/workspace')) return json(workspace)
      if (url.endsWith('/stocks/diagnoses/42')) return json({ id: 42, result: diagnosis('600519', '所选历史报告'), run_log: null })
      if (url.includes('/600519/diagnosis')) return json(diagnosis('600519', '最新报告'))
      return json([])
    }))
    renderAt()
    await screen.findByText('最新报告')
    fireEvent.click(await screen.findByRole('tab', { name: '近期报告' }))
    fireEvent.click(screen.getByRole('button', { name: /历史摘要/ }))
    expect(await screen.findByText('所选历史报告')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '导出 Markdown' })).toHaveAttribute('href', '/api/v1/stocks/diagnoses/42/markdown')
    expect(screen.getByRole('link', { name: '继续问股' })).toHaveAttribute('href', '/chat?code=600519')
  })

  it('切换标的后，延迟返回的上一只股票报告不会覆盖当前报告', async () => {
    let resolveOld: (response: Response) => void = () => {}
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.includes('/watchlist/workspace')) return json(workspace)
      if (url.includes('/600519/diagnosis')) return new Promise<Response>((resolve) => { resolveOld = resolve })
      if (url.includes('/000001/diagnosis')) return json(diagnosis('000001', '银行报告'))
      return json([])
    }))
    renderAt()
    fireEvent.click(await screen.findByRole('button', { name: /平安银行/ }))
    await screen.findByText('银行报告')
    resolveOld(json(diagnosis('600519', '过时报告')))
    await waitFor(() => expect(screen.queryByText('过时报告')).not.toBeInTheDocument())
    expect(screen.getByText('银行报告')).toBeInTheDocument()
  })

  it('无数据首页显示可操作空状态，并恢复服务端任务', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.includes('/watchlist/workspace')) return json({ ...workspace, watchlist: [], recent_reports: [] })
      if (url.endsWith('/tasks')) return json([{ id: 'restored', created_at: '', status: 'error', label: '重启前分析', error: '进程已中断', progress: null }])
      return json([])
    }))
    renderAt()
    expect(await screen.findByText('从一只股票开始研究')).toBeInTheDocument()
    expect(await screen.findByText('进程已中断')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '分析全部' })).toBeDisabled()
  })
})

describe('数据源设置', () => {
  it('可调整优先级并保存掩码，单源校验展示真实返回的日期', async () => {
    const settings = { data_sources: { realtime: ['tencent', 'sina'], daily_history: ['tencent', 'sina'], tushare_token: '******', tickflow_api_key: '', tushare_http_url: '', tickflow_kline_adjust: 'forward', request_timeout_seconds: 15, minimum_realtime_rows: 2500, pytdx_servers: [] }, realtime_options: ['tencent', 'sina', 'tickflow'], daily_options: ['tencent', 'sina', 'tickflow'] }
    const saves: unknown[] = []
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.includes('/settings/data-sources')) { if (init?.method === 'PUT') saves.push(JSON.parse(String(init.body))); return json(settings) }
      if (url.includes('/sources/probe')) return json({ id: 'probe', created_at: '', status: 'done', result: { ok: true, bars: 21, latest_date: '2026-09-30', source: 'tencent', code: '600519' } })
      return json([])
    }))
    renderAt('/sources')
    fireEvent.click(await screen.findByRole('tab', { name: '来源与优先级' }))
    fireEvent.click(await screen.findByRole('button', { name: '实时行情回退顺序 新浪 上移' }))
    fireEvent.click(screen.getByRole('button', { name: '保存数据源配置' }))
    await waitFor(() => expect(saves).toHaveLength(1))
    expect((saves[0] as typeof settings).data_sources.realtime).toEqual(['sina', 'tencent'])
    expect((saves[0] as typeof settings).data_sources.tushare_token).toBe('******')
    fireEvent.click(screen.getByRole('button', { name: '校验日线数据' }))
    expect(await screen.findByText('已读取 21 根日线，最新交易日 2026-09-30')).toBeInTheDocument()
  })

  it('从工作台进入问股时保留股票范围', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => json([])))
    renderAt('/chat?code=sh000300')
    expect(await screen.findByLabelText('限定股票代码')).toHaveValue('sh000300')
  })
})

describe('报告身份与策略设置回归', () => {
  it('历史研究概览使用所选产物，即使 profile 返回了最新相反结论', async () => {
    const artifact = (summary: string) => ({ schema_version: 'research-artifact-v1', artifact_id: summary, created_at: '2026-10-01', subject: { stock_code: '600519' }, thesis: { summary }, evidence: [], invalidation_conditions: [{ id: 'x', description: '旧报告的失效条件' }], next_actions: [] })
    const state = (data: unknown) => ({ status: 'available', data })
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.includes('/watchlist/workspace')) return json(workspace)
      if (url.endsWith('/stocks/diagnoses/42')) return json({ id: 42, result: { ...diagnosis('600519', '旧报告诊断'), structured_report: artifact('旧报告研究论点') } })
      if (url.includes('/600519/profile')) return json({ research: state({ structured_report: artifact('最新相反研究论点') }), portfolio: state({ held: false }), monitors: state({ alert_rules: [] }), signals: state({ active: [] }), history: state({ recent_reports: [] }), intelligence: state({ items: [] }) })
      if (url.includes('/600519/diagnosis')) return json(diagnosis('600519', '最新报告'))
      return json([])
    }))
    renderAt()
    await screen.findByText('最新报告')
    fireEvent.click(screen.getByRole('tab', { name: '近期报告' }))
    fireEvent.click(screen.getByRole('button', { name: /历史摘要/ }))
    await screen.findByText('旧报告诊断')
    fireEvent.click(screen.getByRole('tab', { name: '研究概览' }))
    expect(await screen.findByText('旧报告研究论点')).toBeInTheDocument()
    expect(screen.queryByText('最新相反研究论点')).not.toBeInTheDocument()
    expect(screen.getByText(/#42/)).toBeInTheDocument()
  })

  it('策略设置保存当前因子权重和关闭状态', async () => {
    const settings = { screening: { pipeline: { enabled: true, financial_candidates: 40, financial_timeout_seconds: 60, llm_top_k: 15, llm_timeout_seconds: 40, post_analysis_top_k: 3, risk_max_penalty: 35, risk_veto_threshold: 30, max_same_bucket: 3, concentration_penalty: 5 } }, profiles: [{ name: 'balanced_alpha', label: '均衡多因子', enabled: true, weights: { momentum: .2, value: .3 } }] }
    const saves: typeof settings[] = []
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      // 设置默认先展示 AI 模型，不能用空数组代替它的有效响应，
      // 否则是否在点击策略 tab 前崩溃会取决于异步渲染时序。
      if (String(input).includes('/settings/llm')) return json({ llm: { primary: {}, backup: {}, vision: {} }, platforms: { custom: { name: '自定义', models: [] } } })
      if (String(input).includes('/settings/screening')) {
        if (init?.method === 'PUT') saves.push(JSON.parse(String(init.body)))
        return json(settings)
      }
      return json([])
    }))
    renderAt('/settings')
    await screen.findByText('主力模型')
    fireEvent.click(await screen.findByRole('tab', { name: '选股策略' }))
    await screen.findByText('均衡多因子')
    fireEvent.change(screen.getByLabelText('动量'), { target: { value: '.7' } })
    fireEvent.click(screen.getByLabelText('启用'))
    fireEvent.click(screen.getByRole('button', { name: '保存' }))
    await waitFor(() => expect(saves).toHaveLength(1))
    expect(saves[0].profiles[0].weights.momentum).toBe(.7)
    expect(saves[0].profiles[0].enabled).toBe(false)
  })
})
