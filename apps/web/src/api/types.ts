// 后端接口的数据结构（字段名与 Python 端一致，使用 snake_case）

export type Dict<T = unknown> = Record<string, T>

export interface Task<R = unknown> {
  id: string
  kind: string
  label: string
  status: 'pending' | 'running' | 'done' | 'error'
  progress: { done?: number; total?: number; text?: string } | null
  result: R | null
  error: string
  created_at: string
  started_at: string | null
  finished_at: string | null
}

export interface AuthStatus {
  auth_enabled: boolean
  password_set: boolean
  logged_in: boolean
}

// ---------- 首页 ----------

export interface MarketOverview {
  sh_index?: string | number
  sh_change_pct?: number
  sz_index?: string | number
  sz_change_pct?: number
  cy_index?: string | number
  cy_change_pct?: number
  up_count?: number
  down_count?: number
  flat_count?: number
  limit_up_count?: number
  limit_down_count?: number
  total_amount_yi?: number
  northbound_net_yi?: number
  market_emotion?: string
  top_sectors?: { name: string; pct: number }[]
  bottom_sectors?: { name: string; pct: number }[]
  update_time?: string
  ai_market_comment?: string
}

export interface TopStock {
  rank: number
  code: string
  name: string
  composite_score: number
  recommendation: string
  sentiment_score: number
  limit_up_score: number
  capital_score: number
  tech_score: number
  global_score: number
}

export interface LimitUpStock {
  code: string
  name: string
  close: number
  change_pct: number
  continuous_days: number
  first_limit_time: string
  last_limit_time: string
  open_count: number
  seal_amount: number
  seal_ratio: number
  circ_mv: number
  sector: string
  reason: string
  limit_up_type: string
}

export interface TradeFocusRow {
  rank: number
  code: string
  name: string
  composite_score: number
  recommendation: string
  continuous_days: number
  sector: string
  change_pct: number | null
  limit_reason: string
  reason: string
  signal_type: string
  signal_strength: number | null
  signal_reason: string
  ai_verdict: string
  ai_advice: string
}

export interface Prediction {
  rank: number
  code: string
  name: string
  confidence: number
  predict_type: string
  source: string
  target_time: string
  reason: string
  close: number | null
  change_pct: number | null
  composite_score: number | null
  ai_verdict: string
  ai_advice: string
}

export interface Dashboard {
  today: string
  score_date: string
  limit_up_date: string
  top_stocks: TopStock[]
  limit_up_count: number
  limit_up_stocks: LimitUpStock[]
  signals: Dict[]
  trade_focus: TradeFocusRow[]
  premarket_predictions: Prediction[]
  market_overview: MarketOverview
}

export interface NewsItem {
  time: string
  title: string
  source: string
  url?: string
  level?: string
  tags?: string[]
}

export interface Regime {
  trade_date: string
  regime: string
  score: number
  position_factor: number
  emotion_cycle: string
  metrics: Dict
  reasons: string[]
  summary: string
}

export interface MarketReview {
  trade_date: string
  created_at: string
  headline: string
  stance: string
  position: string
  markdown: string
  error?: string
}

export interface Theme {
  name: string
  phase: string
  heat: number
  trend: number
  cooling: number
  persistence: number
  limit_up: number
  max_height: number
  ladder: string
  leader: { code?: string; name?: string; height?: number }
  followers: { code: string; name: string; height: number }[]
  heat_history: number[]
  dimension: string
}

// ---------- 个股 ----------

export interface StockRef {
  code: string
  name: string
}

export interface DailyBar {
  code: string
  name: string
  trade_date: string
  open: number | null
  high: number | null
  low: number | null
  close: number | null
  volume: number | null
  amount: number | null
  change_pct: number | null
  turnover: number | null
  total_mv: number | null
  circ_mv: number | null
}

export interface NewsEntry {
  kind: string
  title: string
  date: string
  source: string
  url: string
  risk: string
  severe: boolean
}

export interface StockNews {
  news: NewsEntry[]
  notices: NewsEntry[]
}

export interface AgentOpinion {
  role: string
  label: string
  view?: string
  score?: number
  confidence?: string
  key_points?: string[]
  risks?: string[]
  error?: string
}

export interface DiagnosisHistoryItem {
  id: number
  code: string
  name: string
  trade_date: string
  action: string
  score: number | null
  summary: string
  created_at: string
}

export interface DiagnosisHistoryPage {
  total: number
  items: DiagnosisHistoryItem[]
}

export interface DiagnosisRecord {
  id: number
  code: string
  name: string
  trade_date: string
  action: string
  score: number | null
  created_at: string
  result: Diagnosis
}

export interface Diagnosis {
  code: string
  name: string
  trade_date: string
  created_at: string
  score: number
  action: string
  action_label: string
  confidence: string
  one_sentence: string
  trend_prediction: string
  position_advice: { no_position?: string; has_position?: string }
  battle_plan: { buy_price?: number | null; stop_loss?: number | null; target_price?: number | null; suggested_position?: string }
  catalysts: string[]
  risks: string[]
  checklist: { item: string; status: string; note: string }[]
  analysis: string
  guardrails: string[]
  theme_role: { theme?: string; phase?: string; role?: string }
  market_regime: string
  data_quality: { score: number; missing: string[] }
  fund_flow: string
  chips: Dict
  earnings: string
  valuation: string
  agents: AgentOpinion[]
  disagreement: string
  calibration: string
  error?: string
  cached?: boolean
}

// ---------- 策略选股 ----------

export interface ScreeningPick {
  trade_date: string
  code: string
  name: string
  score: number
  close: number
  change_pct: number
  fits_regime: boolean
  labels: string[]
  reasons: string[]
  next_change_pct: number | null
}

export interface StrategyPerformance {
  strategy: string
  label: string
  regimes: string
  picks: number
  evaluated: number
  avg_next_pct: number | null
  win_rate: number | null
  limit_up_rate: number | null
}

export interface BacktestStrategy {
  strategy: string
  label: string
  regimes: string
  picks: number
  days: number
  evaluated: number
  avg_1d: number | null
  win_1d: number | null
  avg_3d: number | null
  avg_5d: number | null
  limit_up_rate: number | null
  avg_1d_fit: number | null
  total_return: number | null
  max_drawdown: number | null
  weight: number
}

export interface BacktestReport {
  start: string
  end: string
  dates: number
  skipped_dates: number
  elapsed: number
  created_at: string
  strategies: BacktestStrategy[]
  weights: Dict<number>
  note?: string
}

export interface ScreeningLatest {
  picks: ScreeningPick[]
  performance: StrategyPerformance[]
  backtest: BacktestReport | null
}

export interface ScreenResult {
  trade_date: string
  regime: string
  picks: unknown[]
  stats: Dict<number>
  notes: string[]
}

// ---------- 绩效 ----------

export interface SignalSummaryRow {
  dimension: string
  group: string
  total: number
  evaluated: number
  limit_up_rate: number | null
  win_rate_1d: number | null
  avg_return_1d: number | null
  win_rate_3d: number | null
  avg_return_3d: number | null
  win_rate_5d: number | null
  avg_return_5d: number | null
  simulated_avg: number | null
  stop_loss_rate: number | null
  take_profit_rate: number | null
}

export interface SignalDetail {
  signal_date: string
  eval_date: string
  code: string
  name: string
  signal_type: string
  source: string
  verdict: string
  entry_price: number | null
  entry_at: string
  returns: Dict<number | null>
  hit_limit_up: boolean | null
  exit_reason: string | null
  simulated_return: number | null
  status: string
}

export interface SignalPerformance {
  as_of: string
  lookback_days: number
  summary: SignalSummaryRow[]
  details: SignalDetail[]
}

export interface DiagnosisOutcomeRow {
  dimension: string
  group: string
  total: number
  evaluated: number
  avg_1d: number | null
  avg_3d: number | null
  avg_5d: number | null
  accuracy_1d: number | null
  accuracy_3d: number | null
  accuracy_5d: number | null
  target_first_rate: number | null
}

export interface DiagnosisOutcomeDetail {
  trade_date: string
  created_at: string
  code: string
  name: string
  action_label: string
  score: number
  base_close: number | null
  r1: number | null
  r3: number | null
  r5: number | null
  hit1: boolean | null
  hit3: boolean | null
  hit5: boolean | null
  plan: string
}

export interface DiagnosisOutcomes {
  summary: DiagnosisOutcomeRow[]
  details: DiagnosisOutcomeDetail[]
}

// ---------- 问股 ----------

export interface ChatSkill {
  name: string
  display_name: string
  description: string
  category: string
  aliases: string[]
  market_regimes: string[]
  source: string
  instructions: string
}

export interface ChatTurn {
  question: string
  answer: string
  perspective: string
  tools: { name: string; label: string; args: Dict; result: string }[]
  error: string
  asked_at: string
}

export type ChatStreamEvent =
  | { type: 'status'; text: string }
  | { type: 'tool'; name: string; label: string; args: Dict }
  | { type: 'tool_result'; name: string; label: string; summary: string }
  | { type: 'delta'; text: string }
  | { type: 'error'; message: string }
  | { type: 'done'; turn: Omit<ChatTurn, 'tools'> & { tools: { name: string; label: string; args: Dict }[] } }

export interface ChatSessionSummary {
  id: string
  title: string
  perspective: string
  turns: number
  updated_at: string
}

export interface ChatSession {
  id: string
  title: string
  perspective: string
  turns: ChatTurn[]
  updated_at: string
}

// ---------- 自选股 ----------

export interface WatchlistRow {
  code: string
  name: string
  note: string
  added_at: string
  trade_date: string
  close: number | null
  change_pct: number | null
  diagnosis: { action: string; action_label: string; score: number; created_at: string; one_sentence: string } | null
}

export interface WatchlistReport {
  trade_date: string
  markdown: string
  created_at: string
  items: Dict[]
  failed: { code: string; name: string; error: string }[]
}

export interface ImportResult {
  added: string[]
  existing: string[]
  unknown: string[]
  over_limit: string[]
}

export interface ImageImportResult {
  candidates: { code: string; name: string; raw: string }[]
  unresolved: string[]
}

// ---------- 交易 ----------

export interface Position {
  account?: string
  code: string
  name: string
  quantity: number
  available_quantity: number
  avg_cost: number
  market_price: number
  market_value: number
  unrealized_pnl: number
  stop_loss: number
  target_price: number
  first_date?: string
}

export interface Order {
  id: string
  created_at: string
  signal_date: string
  code: string
  name: string
  side: string
  price: number
  quantity: number
  amount: number
  status: string
  error_msg: string | null
  risk_note: string | null
}

export interface TradingSnapshot {
  account: { broker: string; cash: number; market_value: number; total_assets: number; unrealized_pnl: number }
  positions: Position[]
  orders: Order[]
}

export interface RiskReport {
  as_of: string
  account: string
  cash_known: boolean
  realized_pnl: number | null
  total_assets: number
  cash: number
  exposure: number
  regime: string
  suggested_exposure: number | null
  positions: { code: string; name: string; sector: string; weight: number; market_value: number; pnl_pct: number | null; stop_loss: number | null; stop_gap: number | null; status: string }[]
  sectors: { sector: string; weight: number }[]
  drawdown: { max_drawdown: number; max_drawdown_date: string; current_drawdown: number; days: number }
  warnings: string[]
}

export interface RealTrade {
  id: number
  trade_date: string
  trade_time: string
  code: string
  name: string
  side: string
  price: number
  quantity: number
  fee: number
  source: string
  note: string
}

export interface RealCorporateAction {
  id: number
  code: string
  name: string
  ex_date: string
  action: 'dividend' | 'bonus' | 'tax' | string
  action_label: string
  cash: number
  shares: number
  note: string
  source: string
}

export interface RealImportPreview {
  format: string
  trades: { trade_date: string; trade_time?: string; code: string; name: string; side: string; price: number; quantity: number; fee: number; duplicate?: boolean }[]
  actions: { ex_date: string; code: string; name: string; action: string; action_label?: string; cash: number; shares: number; duplicate?: boolean }[]
  new_trades: number
  new_actions: number
  duplicates: number
  skipped: number
  warnings: string[]
  error?: string
}

export interface RealPortfolio {
  snapshot: {
    account: { cash: number; market_value: number; total_assets: number; unrealized_pnl: number; realized_pnl: number; cash_known: boolean }
    positions: Position[]
    warnings: string[]
  }
  trades: RealTrade[]
  risk: RiskReport
}

// ---------- 提醒、数据源、用量 ----------

export interface AlertRow {
  time: string
  code: string
  name: string
  type: string
  severity: string
  message: string
  notified: boolean
  reason: string
}

export interface AlertRuleField {
  key: string
  label: string
  type: 'number' | 'select'
  options?: [string, string][]
  default?: string | number | null
}

export interface AlertRuleType {
  label: string
  fields: AlertRuleField[]
}

export interface AlertRule {
  code: string
  type: string
  enabled?: boolean
  note?: string
  [field: string]: string | number | boolean | undefined
}

export interface AlertRules {
  rules: AlertRule[]
  types: Record<string, AlertRuleType>
}

export interface AlertRuleTest {
  triggered: boolean
  message: string
  quote: { price: number; change_pct: number } | null
}

export interface AlertSettings {
  enabled: boolean
  cooldown_minutes: number
  big_drop_pct: number
  near_stop_pct: number
  market_regime: boolean
  regime_score_drop: number
  watchlist: string[]
  min_severity?: 'info' | 'warning' | 'critical'
  daily_digest?: boolean
  digest_time?: string
}

export interface SourceStatus {
  dataset: string
  source: string
  status: string
  last_success: string | null
  last_failure: string | null
  consecutive_failures: number
  total_success: number
  total_failure: number
  last_error: string
  last_elapsed: number | null
}

export interface CapabilitySource {
  name: string
  label: string
  configured: boolean
  note: string
  health: {
    status: 'ok' | 'failing' | 'open' | 'unknown'
    last_success: string | null
    last_failure: string | null
    consecutive_failures: number
    last_error: string
  }
}

export interface DataCapability {
  dataset: string
  label: string
  sources: CapabilitySource[]
}

export interface UsageRow {
  key: string
  calls: number
  cached: number
  failed: number
  tokens: number
  cost_usd: number
  prompt_tokens: number
  completion_tokens: number
}

export interface UsageSummary {
  days: number
  total: Omit<UsageRow, 'key'>
  by_day: UsageRow[]
  by_feature: UsageRow[]
  by_model: UsageRow[]
}

// ---------- 设置 ----------

export interface SchedulerJob {
  id: string
  name: string
  trigger: string
  next_run_time: string | null
  paused: boolean
}

export interface SchedulerStatus {
  running: boolean
  message: string
  jobs: SchedulerJob[]
}

export interface SettingsImportResult {
  sections: string[]
  restored: number
  warnings: string[]
}

export interface LLMRole {
  provider?: string
  api_key?: string | string[]
  base_url?: string
  model?: string
  temperature?: number
  max_tokens?: number
}

export interface LLMSettings {
  llm: { primary?: LLMRole; backup?: LLMRole; vision?: LLMRole } & Dict
  platforms: Dict<{ name: string; base_url: string; models: string[]; default_model: string }>
}

export interface NotifierField {
  key: string
  label: string
  required: boolean
  secret: boolean
  placeholder: string
  type: 'text' | 'number' | 'textarea'
  default?: string | number
}

export interface NotifierSettings {
  notifier: Dict<any>
  channels: Dict<string>
  kinds: Dict<string>
  fields: Dict<NotifierField[]>
  image_channels?: string[]
}

export interface SearchSettings {
  search: {
    enabled?: boolean
    providers?: string[] | string
    max_results?: number
    days?: number
    cache_minutes?: number
    searxng?: { base_urls?: string[]; timeout?: number }
  } & Dict<any>
  providers: Dict<string>
}

export interface SearchTestResult {
  provider: string
  label: string
  ok: boolean
  count: number
  error: string
  samples: string[]
}

export interface IntelligenceSource {
  name: string
  url: string
  enabled: boolean
}

export interface IntelligenceSettings {
  intelligence: {
    enabled: boolean
    interval_minutes: number
    max_items_per_source: number
    keep_days?: number
    sources: IntelligenceSource[]
  }
}

export interface IntelligenceTestResult {
  ok: boolean
  title: string
  count: number
  samples: string[]
  error: string
}

export interface CollectRssResult {
  fetched: number
  inserted: number
}

export interface BotSettings {
  bot: Dict<any>
  running: string[]
}

export interface BotSaveResult {
  ok: boolean
  started: string[]
  restart_required: boolean
  background: boolean
}

export interface NotifierDiagnosis {
  channels: { channel: string; label: string; enabled: boolean; configured: boolean; issues: string[] }[]
  routes: string[]
}
