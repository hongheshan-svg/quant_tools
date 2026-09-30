// 各页面用到的接口，一个函数对应一个后端路由
import { API_BASE, http } from './client'
import type * as T from './types'

export const api = {
  // 系统
  health: () => http.get<{ status: string }>('/health'),
  authStatus: () => http.get<T.AuthStatus>('/auth/status'),
  login: (password: string) => http.post<{ ok: boolean }>('/auth/login', { password }),
  logout: () => http.post<{ ok: boolean }>('/auth/logout'),
  changePassword: (current_password: string, new_password: string) =>
    http.post<{ ok: boolean }>('/settings/password', { current_password, new_password }),
  task: (id: string) => http.get<T.Task>(`/tasks/${id}`),
  tasks: () => http.get<T.Task[]>('/tasks'),
  sources: () => http.get<T.SourceStatus[]>('/system/sources'),
  capabilities: () => http.get<T.DataCapability[]>('/system/capabilities'),
  scheduler: () => http.get<T.SchedulerStatus>('/system/scheduler'),
  runJob: (id: string) => http.post<T.Task>(`/system/scheduler/${id}/run`),
  setupStatus: () => http.get<T.SetupStatus>('/system/setup'),
  usage: (days: number) => http.get<T.UsageSummary>('/usage', { days }),

  // 首页与大盘
  dashboard: () => http.get<T.Dashboard>('/dashboard'),
  news: () => http.get<T.NewsItem[]>('/news'),
  regime: () => http.get<T.Regime>('/market/regime'),
  review: () => http.get<T.MarketReview | null>('/market/review'),
  generateReview: () => http.post<T.Task<T.MarketReview>>('/market/review'),
  themes: (dimension: 'concept' | 'industry') => http.get<T.Theme[]>('/market/themes', { dimension }),
  refreshOverview: () => http.post<T.Task>('/market/overview/refresh'),
  collect: () => http.post<T.Task>('/pipeline/collect'),
  collectRss: () => http.post<T.Task<T.CollectRssResult>>('/pipeline/collect-rss'),
  predict: () => http.post<T.Task>('/pipeline/predict'),
  runFull: () => http.post<T.Task>('/pipeline/run-full'),
  pushDailyReport: () => http.post<T.Task>('/pipeline/daily-report'),
  signalPerformance: (days = 60) => http.get<T.SignalPerformance>('/performance/signals', { days }),
  diagnosisOutcomes: (days = 60) => http.get<T.DiagnosisOutcomes>('/performance/diagnosis', { days }),
  alerts: () => http.get<T.AlertRow[]>('/alerts'),
  checkAlerts: () => http.post<T.Task>('/alerts/check'),
  alertRules: () => http.get<T.AlertRules>('/alerts/rules'),
  saveAlertRules: (rules: T.AlertRule[]) => http.put<{ rules: T.AlertRule[] }>('/alerts/rules', { rules }),
  testAlertRule: (rule: T.AlertRule) => http.post<T.AlertRuleTest>('/alerts/rules/test', { rule }),
  alertSettings: () => http.get<T.AlertSettings>('/alerts/settings'),
  saveAlertSettings: (settings: Partial<T.AlertSettings>) => http.put<T.AlertSettings>('/alerts/settings', settings),

  // 个股
  searchStocks: (q: string, limit = 12) => http.get<T.StockRef[]>('/stocks/search', { q, limit }),
  daily: (code: string) => http.get<T.DailyBar[]>(`/stocks/${code}/daily`),
  ensureHistory: (code: string, name = '') => http.post<{ added: number }>(`/stocks/${code}/history?name=${encodeURIComponent(name)}`),
  stockNews: (code: string, refresh = false) => http.get<T.StockNews>(`/stocks/${code}/news`, { refresh }),
  latestDiagnosis: (code: string) => http.get<T.Diagnosis | null>(`/stocks/${code}/diagnosis`),
  diagnose: (code: string) => http.post<T.Task<T.Diagnosis>>(`/stocks/${code}/diagnosis`),
  diagnosisHistoryList: (params: { code?: string; action?: string; days?: number; limit?: number; offset?: number }) =>
    http.get<T.DiagnosisHistoryPage>('/stocks/diagnoses', params),
  diagnosisRecord: (id: number) => http.get<T.DiagnosisRecord>(`/stocks/diagnoses/${id}`),
  deleteDiagnosis: (id: number) => http.del<{ ok: boolean }>(`/stocks/diagnoses/${id}`),
  diagnosisMarkdownUrl: (id: number) => `${API_BASE}/stocks/diagnoses/${id}/markdown`,
  diagnosisImageUrl: (id: number) => `${API_BASE}/stocks/diagnoses/${id}/image`,
  diagnosisMarkdownText: async (id: number) => {
    const res = await fetch(`${API_BASE}/stocks/diagnoses/${id}/markdown`, { credentials: 'include' })
    if (!res.ok) throw new Error(`请求失败（${res.status}）`)
    return res.text()
  },

  // 决策信号
  signals: (params: { status?: string; action?: string; code?: string; days?: number; limit?: number; offset?: number }) =>
    http.get<T.DecisionSignalPage>('/signals', params),
  signalStats: (days = 90) => http.get<T.DecisionSignalStats>('/signals/stats', { days }),
  signalReview: (code: string) => http.get<T.SignalReview>(`/signals/review/${code}`),
  signal: (id: number) => http.get<T.DecisionSignal>(`/signals/${id}`),
  signalFeedback: (id: number, feedback: 'useful' | 'not_useful' | null, note = '') =>
    http.put<T.DecisionSignal>(`/signals/${id}/feedback`, { feedback, note }),
  evaluateSignals: () => http.post<T.Task<Record<string, number>>>('/signals/evaluate'),

  // 策略选股
  screening: () => http.get<T.ScreeningLatest>('/screening'),
  runScreening: () => http.post<T.Task<T.ScreenResult>>('/screening/run'),
  backtest: (days = 60) => http.post<T.Task<T.BacktestReport>>(`/screening/backtest?days=${days}`),

  // 问股
  skills: () => http.get<T.ChatSkill[]>('/chat/skills'),
  skillPerformance: (days = 90) => http.get<T.SkillPerformanceRow[]>('/chat/skills/performance', { days }),
  perspectives: () => http.get<Record<string, string>>('/chat/perspectives'),
  chatSessions: () => http.get<T.ChatSessionSummary[]>('/chat/sessions'),
  createChat: (perspective: string) => http.post<T.ChatSession>('/chat/sessions', { perspective }),
  chatSession: (id: string) => http.get<T.ChatSession>(`/chat/sessions/${id}`),
  deleteChat: (id: string) => http.del<{ ok: boolean }>(`/chat/sessions/${id}`),
  ask: (id: string, question: string, perspective?: string) =>
    http.post<T.Task<T.ChatTurn>>(`/chat/sessions/${id}/ask`, { question, perspective }),
  askStream: (
    id: string,
    question: string,
    perspective: string | undefined,
    handlers: { onEvent: (event: T.ChatStreamEvent) => void; signal?: AbortSignal },
  ) => http.stream<T.ChatStreamEvent>(`/chat/sessions/${id}/ask/stream`, { question, perspective }, handlers),
  cancelChat: (id: string) => http.post<{ ok: boolean }>(`/chat/sessions/${id}/cancel`),
  exportChatUrl: (id: string) => `/api/v1/chat/sessions/${id}/export`,
  pushChat: (id: string) => http.post<{ pushed: boolean; reason?: string }>(`/chat/sessions/${id}/push`),

  // 深度研究
  startResearch: (topic: string) => http.post<T.Task<T.ResearchReport>>('/research', { topic }),
  researchList: (limit = 50) => http.get<T.ResearchSummary[]>(`/research?limit=${limit}`),
  researchReport: (id: number) => http.get<T.ResearchReport>(`/research/${id}`),
  deleteResearch: (id: number) => http.del<{ ok: boolean }>(`/research/${id}`),
  researchMarkdownUrl: (id: number) => `/api/v1/research/${id}/markdown`,

  // 自选股
  watchlist: () => http.get<T.WatchlistRow[]>('/watchlist'),
  addWatch: (text: string) => http.post<{ ok: boolean; code?: string; name?: string; error?: string }>('/watchlist', { text }),
  removeWatch: (code: string) => http.del<{ ok: boolean }>(`/watchlist/${code}`),
  importWatch: (text: string) => http.post<T.ImportResult>('/watchlist/import', { text }),
  importWatchFile: (file: File) => http.upload<T.ImportResult>('/watchlist/import-file', file),
  importImage: (file: File) => http.upload<T.Task>('/watchlist/import-image', file),
  watchlistReport: () => http.get<T.WatchlistReport | null>('/watchlist/report'),
  runWatchlistReport: (push = true) => http.post<T.Task>(`/watchlist/report?push=${push}`),

  // 模拟盘
  trading: () => http.get<T.TradingSnapshot>('/trading'),
  tradingRisk: () => http.get<T.RiskReport>('/trading/risk'),
  prepareOrders: () => http.post<T.Task>('/trading/orders/prepare'),
  confirmOrder: (id: string) => http.post<{ ok: boolean; error?: string }>(`/trading/orders/${id}/confirm`),
  cancelOrder: (id: string) => http.post<{ ok: boolean; error?: string }>(`/trading/orders/${id}/cancel`),
  checkExits: () => http.post<T.Task>('/trading/exits/check'),

  // 实盘记账
  real: () => http.get<T.RealPortfolio>('/real'),
  addRealTrade: (trade: Record<string, unknown>) => http.post<{ ok: boolean }>('/real/trades', trade),
  deleteRealTrade: (id: number) => http.del<{ ok: boolean }>(`/real/trades/${id}`),
  importRealTrades: (file: File) => http.upload<{ added: number; duplicate: number; skipped: number; actions_added?: number; error: string }>('/real/trades/import', file),
  previewRealImport: (file: File) => http.upload<T.RealImportPreview>('/real/trades/import?preview=true', file),
  realActions: () => http.get<T.RealCorporateAction[]>('/real/actions'),
  addRealAction: (body: Record<string, unknown>) => http.post<{ ok: boolean }>('/real/actions', body),
  deleteRealAction: (id: number) => http.del<{ ok: boolean }>(`/real/actions/${id}`),
  setRealCash: (cash: number) => http.put<{ ok: boolean }>('/real/cash', { cash }),
  setRealPlan: (code: string, stop_loss: number | null, target_price: number | null) =>
    http.put<{ ok: boolean }>(`/real/plans/${code}`, { stop_loss, target_price }),

  // 设置
  exportSettingsUrl: (includeSecrets: boolean) => `/api/v1/settings/export?include_secrets=${includeSecrets}`,
  importSettings: (yaml: string) => http.post<T.SettingsImportResult>('/settings/import', { yaml }),
  setWebAuth: (auth_enabled: boolean, password = '') => http.put<{ ok: boolean }>('/settings/web-auth', { auth_enabled, password }),
  llmSettings: () => http.get<T.LLMSettings>('/settings/llm'),
  saveLlm: (llm: Record<string, unknown>) => http.put<{ ok: boolean }>('/settings/llm', { llm }),
  testLlm: (llm: Record<string, unknown>) => http.post<{ ok: boolean; reply?: string; error?: string }>('/settings/llm/test', { llm }),
  llmModels: (role: string, config: Record<string, unknown>) => http.post<{ models: string[] }>('/settings/llm/models', { role, config }),
  searchSettings: () => http.get<T.SearchSettings>('/settings/search'),
  saveSearch: (search: Record<string, unknown>) => http.put<{ search: T.SearchSettings['search'] }>('/settings/search', { search }),
  testSearch: (search: Record<string, unknown>, query: string) =>
    http.post<{ results: T.SearchTestResult[] }>('/settings/search/test', { search, query }),
  intelligenceSettings: () => http.get<T.IntelligenceSettings>('/settings/intelligence'),
  saveIntelligence: (intelligence: Record<string, unknown>) =>
    http.put<T.IntelligenceSettings>('/settings/intelligence', { intelligence }),
  testIntelligenceSource: (url: string) => http.post<T.IntelligenceTestResult>('/settings/intelligence/test', { url }),
  botSettings: () => http.get<T.BotSettings>('/settings/bot'),
  saveBot: (bot: Record<string, unknown>) => http.put<T.BotSaveResult>('/settings/bot', { bot }),
  notifierSettings: () => http.get<T.NotifierSettings>('/settings/notifier'),
  saveNotifier: (notifier: Record<string, unknown>) => http.put<{ ok: boolean }>('/settings/notifier', { notifier }),
  diagnoseNotifier: (notifier: Record<string, unknown>) => http.post<T.NotifierDiagnosis>('/settings/notifier/diagnose', { notifier }),
  testNotifier: (channel: string, notifier: Record<string, unknown>) =>
    http.post<{ ok: boolean; error: string }>(`/settings/notifier/test/${channel}`, { notifier }),
}
