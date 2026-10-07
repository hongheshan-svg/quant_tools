// 后端接口的数据结构（字段名与 Python 端一致，使用 snake_case）

export type Dict<T = unknown> = Record<string, T>

export interface Task<R = unknown> {
  revision?: number
  trace_id?: string
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

export interface SetupItem {
  key: string
  label: string
  done: boolean
  required: boolean
  hint: string
  link: string
}

export interface SetupStatus {
  items: SetupItem[]
  done: number
  total: number
  required_missing: number
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
  trade_date?: string // 涨跌家数、成交额统计所属的交易日（节假日、开盘前为最近一个交易日）
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
  important?: boolean // 置顶标红：财联社红色/重要、头部企业财报、重大国际新闻
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
  kind?: 'stock' | 'etf' | 'index'
}

export interface DailyBar {
  source?: string | null
  price_adjustment?: string | null
  updated_at?: string | null
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

export interface RunLogStep {
  metadata?: { source?: string; attempt?: number; cache_hit?: boolean; stale_seconds?: number; record_count?: number }
  name: string
  kind: 'data' | 'llm' | 'note' | 'provider' | 'save' | 'notify'
  ok: boolean
  ms: number
  detail: string
  source?: string
  attempt?: number
  cache_hit?: boolean
  stale_seconds?: number
  record_count?: number
}

export interface RunLog {
  steps: RunLogStep[]
  total_ms: number
  model: string
}

export interface DiagnosisTrendPoint {
  id: number
  created_at: string
  trade_date: string
  score: number | null
  action: string
  close: number | null
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
  run_log: RunLog | null
}

export interface PhaseDecision {
  phase?: string
  phase_label?: string
  trading_window?: string
  immediate_action?: string
  watch_conditions?: string[]
  next_check_time?: string
  data_limitations?: string[]
}

export interface SignalAttribution {
  technical?: number | null
  news?: number | null
  fundamentals?: number | null
  market?: number | null
  strongest_bullish?: string
  strongest_bearish?: string
}

export interface MarketPhase {
  phase?: string
  label?: string
  now?: string
  effective_daily_bar_date?: string | null
}

export interface Diagnosis {
  context_pack?: ContextPack | null
  structured_report?: ResearchArtifact
  phase_decision?: PhaseDecision
  signal_attribution?: SignalAttribution
  market_phase?: MarketPhase
  code: string
  name: string
  trade_date: string
  created_at: string
  run_log?: RunLog | null
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
  shareholders?: string
  valuation: string
  agents: AgentOpinion[]
  disagreement: string
  calibration: string
  skill_opinions?: SkillOpinion[]
  skill_consensus?: SkillConsensus
  kind?: 'etf' | 'index'
  error?: string
  cached?: boolean
  id?: number
  diagnosis_id?: number | null
  decision_profile?: DecisionProfile
}

export interface ContextPack {
  pack_version: '1.0'
  subject: Record<string, string>
  blocks: Record<string, { status: string; source?: string | null; as_of?: string | null; provider_timestamp?: string | null; fetched_at?: string | null; timestamp?: string | null; limitations: string[]; items: Record<string, { status: string; value: unknown; source?: string | null; as_of?: string | null; provider_timestamp?: string | null; fetched_at?: string | null; timestamp?: string | null }> }>
}

export interface ResearchArtifact {
  created_at?: string | null
  schema_version: 'research-artifact-v1'
  subject: { stock_code: string; stock_name?: string; market?: string }
  thesis: { summary: string; direction: string; score?: number | null; confidence?: number | null; reasons: string[]; risks: string[] }
  evidence: { id: string; source_type: string; title: string; summary?: string | null; source?: string | null; as_of?: string | null; provider_timestamp?: string | null; fetched_at?: string | null; timestamp?: string | null; freshness: string; quality_level: string }[]
  invalidation_conditions: { id: string; category: string; description: string }[]
  next_actions: { action: string; label: string; reason?: string; due_at?: string | null }[]
}

export interface ProfileBlock<T> {
  status: 'fresh' | 'partial' | 'unavailable'
  limitations: string[]
  data?: T
}

export interface StockProfile {
  code: string
  kind: 'stock' | 'etf' | 'index'
  name: string
  quote: ProfileBlock<{ trade_date: string; close: number; change_pct: number; source?: string | null; price_adjustment?: string | null; updated_at?: string | null }>
  research: ProfileBlock<{ action: string; score: number; one_sentence: string; structured_report: ResearchArtifact; context_pack?: ContextPack }>
  history: ProfileBlock<{ total: number; history_days: number; recent_reports: DiagnosisHistoryItem[] }>
  intelligence: ProfileBlock<{ items: ScopedIntelligenceItem[] }>
  portfolio: ProfileBlock<{ held: boolean; holdings: { source: string; label: string; quantity: number; avg_cost: number; market_price: number; unrealized_pnl_pct: number | null }[] }>
  signals: ProfileBlock<{ active: { id: number; action_label: string; stop_loss: number | null; target_price: number | null }[] }>
  monitors: ProfileBlock<{ in_watchlist: boolean; alert_rules: { type: string; text: string; note: string }[] }>
}

export interface ScopedIntelligenceItem {
  id: number | string
  source_id?: number | null
  source: string
  title: string
  summary?: string
  url?: string
  symbol?: string | null
  market?: string
  sector?: string | null
  published_at?: string | null
  collected_at: string
}

export interface ScopedIntelligenceSource {
  id: number
  name: string
  url: string
  enabled: boolean
  symbol?: string | null
  market?: string
  sector?: string | null
  managed_by_config?: boolean
  last_fetched_at?: string | null
  last_error?: string
}

export type DecisionProfile = 'conservative' | 'balanced' | 'aggressive'

export interface ReassessResult {
  diagnosis_id: number
  code: string
  name: string
  profile: DecisionProfile
  profile_label: string
  action: string
  action_label: string
  confidence: string
  guardrails: string[]
  original: { profile: DecisionProfile; action: string; action_label: string; confidence: string }
  changed: boolean
  /** persist=true 时附带：created 新建、existing 已存在、skipped 不是方向性建议 */
  status?: 'created' | 'existing' | 'skipped' | 'error'
  signal?: DecisionSignal
  reason?: string
}

export interface DiagnosisSettings {
  decision_profile: DecisionProfile
  mode: 'single' | 'standard' | 'full'
  shareholders: boolean
  calibration: boolean
  signal_review: boolean
  skill_consult: { enabled: boolean; max_skills: number }
}

export interface SkillOpinion {
  skill: string
  display_name: string
  stance: string
  score: number
  confidence: string
  reason: string
  weight: number
}

export interface SkillConsensus {
  status?: 'ready' | 'insufficient'
  stance?: string
  score?: number | null
  agreement?: string
  valid_count?: number
}

// ---------- 策略选股 ----------

export interface ScreeningPick {
  why_selected?: ScreeningExplanation[]
  why_now?: ScreeningExplanation[]
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
  screen_score?: number
  factor_scores?: Record<string, number | null>
  factor_coverage?: number
  data_quality?: { score: number; flags: string[]; source?: string; price_adjustment?: string; price_revision?: string }
  financial_status?: string
  industry?: string
  themes?: string[]
  risk_penalty?: number
  portfolio_penalty?: number
  risk_flags?: string[]
  risk_level?: string
  llm_score?: number | null
  llm_reason?: string
  post_analysis?: { status: string; report_id?: number; action?: string; score?: number; reason?: string }
}

export interface ScreeningSettings {
  screening: { profiles_file?: string; pipeline?: Record<string, number | boolean>; max_total?: number; [key: string]: unknown }
  profiles: { name: string; label?: string; enabled?: boolean; weights: Record<string, number>; conditions?: unknown[]; [key: string]: unknown }[]
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
  evaluated_days?: number
  unavailable?: number
  entry_blocked?: number
  pending?: number
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
  benchmark?: { name: string; status: string; samples: number; missing: number; avg_1d: number | null }
  start: string
  end: string
  dates: number
  skipped_dates: number
  elapsed: number
  created_at: string
  strategies: BacktestStrategy[]
  weights: Dict<number>
  status?: 'success' | 'partial'
  engine_version?: string
  methodology?: string
  attempted_dates?: number
  failed_dates?: number
  note?: string
}

export interface ScreeningLatest {
  last_run?: { id: number; status: string; trade_date: string; created_at: string; notes: string[]; stats: Dict<number> } | null
  picks: ScreeningPick[]
  performance: StrategyPerformance[]
  backtest: BacktestReport | null
}

export interface ScreeningDate {
  trade_date: string
  picks: number
  strategies: Dict<number>
  evaluated: number
  avg_next_pct: number | null
  win_rate: number | null
}

export interface ScreeningDates {
  dates: ScreeningDate[]
  strategies: { name: string; label: string }[]
}

export interface ScreeningPicks {
  trade_date: string | null
  picks: ScreeningPick[]
}

export interface ScreenResult {
  status?: 'success' | 'partial'
  trade_date: string
  regime: string
  picks: unknown[]
  stats: Dict<number>
  notes: string[]
}

export interface ScreeningExplanation {
  text: string
  kind: 'observed' | 'inferred' | 'unknown'
  source_type: string
  source?: string | null
  status: string
  data_date?: string | null
  provider_timestamp?: string | null
  fetched_at?: string | null
  age_days?: number | null
  url?: string | null
}
export interface SnapshotCheck {
  mode: string
  network_used: boolean
  strategies: { strategy: string; label: string; matched: boolean | null; score: number | null; checks: { condition: unknown; status: string; inputs: Record<string, unknown>; missing: string[] }[] }[]
}

export interface ETFRotationSettings {
  risk_assets: string[]
  safe_asset: string
  start: string
  end: string
  lookback_days: number
  top_n: number
  rebalance: 'weekly' | 'monthly'
  switch_buffer_pct: number
  cost_bps: number
  refresh: boolean
}
export interface ETFRotationResult {
  status: string
  as_of: string
  limitations: string[]
  metrics: Record<string, number | null>
  benchmark_metrics: Record<string, number | null>
  annual_returns: Record<string, number | null>
  parameter_sweep: { lookback_days: number; total_return: number | null; max_drawdown: number | null }[]
  curve: { date: string; equity: number; benchmark: number }[]
  trades: { signal_date: string; execution_date: string; to_weights: Record<string, number>; turnover: number }[]
  ranking: { code: string; momentum: number | null; eligible: boolean }[]
  current_weights: Record<string, number>
  next_action: { signal_date: string; execution_date: string | null; weights: Record<string, number> }
  price_snapshot_hash: string
  note: string
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

// ---------- 决策信号 ----------
export interface DecisionSignal {
  id: number
  diagnosis_id: number | null
  code: string
  name: string
  action: string
  action_label?: string
  score: number | null
  confidence: string | null
  entry_low: number | null
  entry_high: number | null
  stop_loss: number | null
  target_price: number | null
  horizon_days: number
  invalidation: string | null
  trade_date: string
  status: string
  status_label?: string
  status_reason: string | null
  expires_on: string | null
  ret_1d: number | null
  ret_3d: number | null
  ret_5d: number | null
  max_adverse_pct: number | null
  max_favorable_pct: number | null
  evaluated_at?: string | null
  feedback: string | null
  feedback_note: string | null
  profile?: DecisionProfile | null
  profile_label?: string
  created_at?: string
  hit?: boolean | null
}

export interface DecisionSignalPage {
  total: number
  items: DecisionSignal[]
}

export interface DecisionSignalStats {
  total: number
  by_status: Record<string, number>
  by_action: Record<string, { count: number; hits: number; hit_rate: number | null; avg_ret_5d: number | null; avg_adverse: number | null }>
  hit_rate: number | null
  avg_ret_5d?: number | null
  avg_adverse?: number | null
}

export interface SignalReview {
  samples: number
  hits: number
  hit_rate: number | null
  avg_ret: number | null
  avg_adverse: number | null
  bias: string
  text: string
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

export interface SkillPerformanceRow {
  skill: string
  display_name: string
  samples: number
  hits: number
  hit_rate: number
  avg_ret: number | null
  weight: number
}

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

export interface ChatContext {
  stock_context?: { code: string }
  skills?: string[]
}

export interface ChatTurn {
  question: string
  answer: string
  perspective: string
  tools: { name: string; label: string; args: Dict; result: string }[]
  error: string
  asked_at: string
  stock_context?: { code: string } | null
  skills?: string[]
  run_log?: RunLog
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
  quote_source?: string | null
  code: string
  name: string
  kind?: 'stock' | 'etf' | 'index'
  note: string
  added_at: string
  trade_date: string
  close: number | null
  change_pct: number | null
  diagnosis: { diagnosis_id?: number; action: string; action_label: string; score: number; created_at: string; one_sentence: string } | null
}

export interface StockWorkspace {
  today: string
  watchlist: WatchlistRow[]
  today_reports: DiagnosisHistoryItem[]
  recent_reports: DiagnosisHistoryItem[]
  analyzed_today: number
}

export interface DataSourceSettings {
  realtime: string[]
  daily_history: string[]
  tushare_token: string
  tushare_http_url: string
  tickflow_api_key: string
  miaoxiang_api_key?: string
  tickflow_kline_adjust: string
  request_timeout_seconds: number
  stage_timeout_seconds?: number
  collect_timeout_seconds?: number
  isolate_collection?: boolean
  require_auxiliary_sources?: boolean
  minimum_realtime_rows: number
  pytdx_servers: string[]
}

export interface DataSourceSettingsResponse {
  data_sources: DataSourceSettings
  realtime_options: string[]
  daily_options: string[]
}

export interface DataSourceProbe {
  ok: boolean
  source: string
  code: string
  bars: number
  latest_date: string
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
  /** 实盘持仓所在的账户（汇总视图可能有多个） */
  accounts?: string[]
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
  exposure: number | null
  regime: string
  suggested_exposure: number | null
  positions: { code: string; name: string; sector: string; weight: number; market_value: number; pnl_pct: number | null; stop_loss: number | null; stop_gap: number | null; status: string }[]
  sectors: { sector: string; weight: number }[]
  drawdown: { max_drawdown: number | null; max_drawdown_date: string; current_drawdown: number | null; days: number; quality?: { status: string; limitations: string[]; valuation_points?: number; method?: string } }
  quality?: { valuation: string; priced: number; positions: number; classification: string; classified: number; classification_source: string; currency: string }
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
  account?: string
}

export interface RealAccount {
  name: string
  broker: string
  note: string
  trades: number
  positions: number
  market_value: number
  cash: number | null
}

export interface RealCashFlow {
  id: number
  flow_date: string
  direction: 'in' | 'out'
  direction_label: string
  amount: number
  note: string
  account?: string
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
  account?: string
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
    account: {
      cash: number; market_value: number; total_assets: number; unrealized_pnl: number; realized_pnl: number; cash_known: boolean
      net_deposit?: number
      ledger_mode?: boolean        // 没设置可用资金、按出入金和成交重放
      total_return?: number | null // 出入金模式下：总资产 − 净入金
    }
    positions: Position[]
    warnings: string[]
  }
  trades: RealTrade[]
  risk: RiskReport
}

// ---------- 提醒、数据源、用量 ----------

export interface AlertRow {
  id?: number
  channels?: Record<string, boolean>
  rule_id?: string
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

export interface DataCenter {
  as_of: string
  read_only: boolean
  matrix: { provider: string; provider_label: string; dataset: string; markets: string[]; asset_kinds: string[]; scenarios: string[]; priority: number; configured: boolean; configuration_origin: string; limitations: string; fetched_at: string | null; observation_timestamp: string | null }[]
  snapshots: { dataset: string; status: string; trade_date: string | null; expected_date: string | null; fetched_at: string | null; rows_on_date: number; note: string }[]
  unsupported: string[]
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

export interface ConfigIssue {
  level: 'error' | 'warning'
  path: string
  message: string
}

export interface ConfigCheckResult {
  ok: boolean
  errors: number
  warnings: number
  issues: ConfigIssue[]
}

export interface SettingsImportResult {
  sections: string[]
  restored: number
  warnings: string[]
  check?: ConfigCheckResult
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

export type ReportLanguage = 'zh' | 'en'

export interface ReportSettings {
  language: ReportLanguage
}

export interface ReportTemplateInfo {
  name: string
  label: string
  custom: boolean
  path: string
}

export interface ReportTemplate {
  name: string
  label: string
  custom: boolean
  text: string
}

export interface ReportTemplatePreview {
  ok: boolean
  markdown: string
  error?: string
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

export interface IntelligenceTemplate {
  id: string
  name: string
  url: string
  description: string
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

export interface ResearchEvidence {
  id: string
  source: string
  title: string
  content: string
  url: string
}

export interface ResearchSummary {
  id: number
  topic: string
  created_at: string
  summary: string
}

export interface ResearchReport {
  id: number
  topic: string
  markdown: string
  questions: string[]
  evidence: ResearchEvidence[]
  stocks: { code: string; name: string }[]
  created_at: string
}

export interface EmailGroup {
  name: string
  stocks: string[]
  to: string[]
}

export interface WatchlistSettings {
  daily_report: boolean
  max_stocks: number
  workers: number
  single_notify: boolean
  timeout_minutes: number
}


export interface SchedulerSettings {
  hot_search_interval: number
  cailianshe_interval: number
  stock_data_interval: number
  daily_analysis_time: string
  daily_signal_time: string
  daily_report_time: string
  watchlist_report_time: string
  self_learning_time: string
  signal_lifecycle_time: string
}
