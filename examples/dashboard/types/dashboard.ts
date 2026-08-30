// Market Type
export type Market = "KR" | "US"

export interface PortfolioSummary {
  total_stocks: number
  total_profit: number
  avg_profit_rate: number
  slot_usage: string
  slot_percentage: number
  sector_distribution: Record<string, number>
  period_distribution: Record<string, number>
}

export interface TradingSummary {
  total_trades: number
  win_count: number
  loss_count: number
  win_rate: number
  avg_profit_rate: number
  avg_holding_days: number
}

export interface AIDecisionsSummary {
  total_decisions: number
  sell_signals: number
  hold_signals: number
  adjustment_needed: number
  avg_confidence: number
}

export interface RealTradingSummary {
  total_stocks: number
  total_eval_amount: number
  total_profit_amount: number
  total_profit_rate: number
  deposit: number
  total_cash: number  // D+2 포함 총 현금
  available_amount: number
}

export interface Summary {
  portfolio: PortfolioSummary
  trading: TradingSummary
  ai_decisions: AIDecisionsSummary
  real_trading: RealTradingSummary
}

export interface Holding {
  ticker: string
  company_name?: string
  name?: string
  buy_price?: number
  buy_date?: string
  current_price: number
  last_updated?: string
  target_price?: number
  stop_loss?: number
  profit_rate: number
  holding_days?: number
  sector: string
  investment_period?: string
  quantity?: number
  avg_price?: number
  value?: number
  profit?: number
  weight?: number
  scenario?: {
    portfolio_analysis?: string
    valuation_analysis?: string
    sector_outlook?: string
    buy_score?: number
    min_score?: number
    decision?: string
    target_price?: number
    stop_loss?: number
    investment_period?: string
    rationale?: string
    sector?: string
    market_condition?: string
    max_portfolio_size?: string
    trading_scenarios?: {
      key_levels?: Record<string, any>
      sell_triggers?: string[]
      hold_conditions?: string[]
      portfolio_context?: string
    }
  }
}

export interface Trade {
  id: number
  ticker: string
  company_name: string
  buy_price: number
  buy_date: string
  sell_price: number
  sell_date: string
  profit_rate: number
  holding_days: number
  scenario?: {
    target_price?: number
    stop_loss?: number
    investment_period?: string
    sector?: string
    rationale?: string
  }
}

export interface WatchlistStock {
  id: number
  ticker: string
  company_name: string
  current_price: number
  analyzed_date: string
  buy_score: number
  min_score: number
  decision: string
  skip_reason: string
  target_price: number
  stop_loss: number
  investment_period: string
  sector: string
  portfolio_analysis?: string
  valuation_analysis?: string
  sector_outlook?: string
  market_condition?: string
  rationale?: string
  max_portfolio_size?: string
  trading_scenarios?: {
    key_levels?: Record<string, string>
    sell_triggers?: string[]
    hold_conditions?: string[]
    portfolio_context?: string
  }
  scenario?: {
    portfolio_analysis?: string
    valuation_analysis?: string
    sector_outlook?: string
    buy_score?: number
    min_score?: number
    decision?: string
    target_price?: number
    stop_loss?: number
    investment_period?: string
    rationale?: string
    sector?: string
    market_condition?: string
    max_portfolio_size?: string
    trading_scenarios?: {
      key_levels?: Record<string, string>
      sell_triggers?: string[]
      hold_conditions?: string[]
      portfolio_context?: string
    }
  }
  full_json_data?: any
}

export interface MarketCondition {
  date: string
  // Korean market indices
  kospi_index?: number
  kosdaq_index?: number
  // US market indices
  spx_index?: number
  nasdaq_index?: number
  condition: number
  volatility: number
}

export interface PrismPerformance {
  date: string
  cumulative_realized_profit: number
  prism_simulator_return: number
  holdings_unrealized_profit: number
  holdings_return: number
}

export interface AccountSummary {
  total_eval_amount: number
  total_profit_amount: number
  total_profit_rate: number
  available_amount: number
}

export interface OperatingCosts {
  server_hosting: number
  openai_api: number
  anthropic_api: number
  firecrawl_api: number
  perplexity_api: number
  month: string
}

export interface DashboardData {
  generated_at: string
  trading_mode: string
  market?: Market  // Market identifier (KR or US)
  currency?: string  // Currency (KRW or USD)
  summary: Summary
  holdings: Holding[]
  real_portfolio: Holding[]
  account_summary: AccountSummary
  operating_costs?: OperatingCosts
  trading_history: Trade[]
  watchlist: WatchlistStock[]
  market_condition: MarketCondition[]
  prism_performance?: PrismPerformance[]
  holding_decisions?: HoldingDecision[]
  trading_insights?: TradingInsightsData
}

export interface HoldingDecision {
  id: number
  ticker: string
  company_name?: string
  decision_date: string
  decision_time: string
  current_price: number
  should_sell: number
  sell_reason: string
  confidence: number
  technical_trend: string
  volume_analysis: string
  market_condition_impact: string
  time_factor: string
  portfolio_adjustment_needed: number
  adjustment_reason: string
  new_target_price: number
  new_stop_loss: number
  adjustment_urgency: string
  full_json_data?: any
  created_at: string
}

// Trading Insights Types
export interface TradingLesson {
  condition?: string  // optional for L2 compressed entries
  action: string
  reason?: string
  priority?: 'high' | 'medium' | 'low'  // optional for L2 compressed entries (defaults to 'medium')
}

export interface SituationAnalysis {
  buy_context_summary?: string
  sell_context_summary?: string
  market_at_buy?: string
  market_at_sell?: string
  key_changes?: string[]
}

export interface JudgmentEvaluation {
  buy_quality?: string
  buy_quality_reason?: string
  sell_quality?: string
  sell_quality_reason?: string
  missed_signals?: string[]
  overreacted_signals?: string[]
}

export interface TradingJournal {
  id: number
  ticker: string
  company_name: string
  trade_date: string
  trade_type: string
  buy_price: number
  sell_price: number
  profit_rate: number
  holding_days: number
  one_line_summary: string
  situation_analysis: string
  judgment_evaluation: string
  lessons: TradingLesson[]
  pattern_tags: string[]
  compression_layer: number
  market?: Market  // KR or US
}

export interface TradingPrinciple {
  id: number
  scope: 'universal' | 'sector' | 'market'
  scope_context: string | null
  condition: string
  action: string
  reason: string | null
  priority: 'high' | 'medium' | 'low'
  confidence: number
  supporting_trades: number
  is_active: boolean
  created_at: string
  last_validated_at: string | null
  market?: Market  // KR or US
}

export interface TradingIntuition {
  id: number
  category: string
  condition: string
  insight: string
  confidence: number
  success_rate: number
  supporting_trades: number
  is_active: boolean
  subcategory?: string
  market?: Market  // KR or US
}

export interface InsightsSummary {
  total_principles: number
  active_principles: number
  high_priority_count: number
  total_journal_entries: number
  avg_profit_rate: number
  total_intuitions: number
  avg_confidence: number
}

// Performance Analysis Types
export interface TriggerPerformanceByDecision {
  count: number
  avg_7d_return: number | null
  avg_14d_return: number | null
  avg_30d_return: number | null
  win_rate_30d: number | null
}

export interface TriggerPerformance {
  trigger_type: string
  count: number
  avg_7d_return: number | null
  avg_14d_return: number | null
  avg_30d_return: number | null
  win_rate_30d: number | null
}

export interface TradedVsWatchedData {
  count: number
  avg_7d: number | null
  avg_14d: number | null
  avg_30d: number | null
  win_rate: number | null
  // 상세 수익/손실 분석 필드
  win_count?: number
  loss_count?: number
  avg_profit?: number | null
  avg_loss?: number | null
  max_profit?: number | null
  max_loss?: number | null
  profit_factor?: number | null
}

export interface ActualTradingData {
  count: number
  avg_profit_rate: number | null
  win_rate: number | null
  win_count: number
  loss_count: number
  avg_profit: number | null
  avg_loss: number | null
  max_profit: number | null
  max_loss: number | null
  profit_factor: number | null
}

export interface ActualTradingByTrigger {
  trigger_type: string
  count: number
  avg_profit_rate: number | null
  win_rate: number | null
  profit_factor: number | null
  win_count?: number
  loss_count?: number
  avg_profit?: number | null
  avg_loss?: number | null
}

export interface TradedVsWatched {
  traded: TradedVsWatchedData
  watched: TradedVsWatchedData
  actual_trading?: ActualTradingData
  t_test?: {
    p_value: number
    significant: boolean
  }
}

export interface RRThresholdAnalysis {
  range: string
  total_count: number
  traded_count: number
  watched_count: number
  avg_all_return: number | null
  avg_watched_return: number | null
}

export interface MissedOpportunity {
  ticker: string
  company_name: string
  trigger_type: string
  analyzed_price: number
  tracked_30d_price: number
  tracked_30d_return: number
  skip_reason: string
  analyzed_date?: string
  decision?: string
}

export interface PerformanceAnalysisOverview {
  total: number
  pending: number
  in_progress: number
  completed: number
  traded_count: number
  watched_count: number
}

export interface PerformanceAnalysis {
  overview: PerformanceAnalysisOverview
  trigger_performance: TriggerPerformance[]
  actual_trading: ActualTradingData
  actual_trading_by_trigger?: ActualTradingByTrigger[]
  rr_threshold_analysis: RRThresholdAnalysis[]
  missed_opportunities: MissedOpportunity[]
  avoided_losses: MissedOpportunity[]
  recommendations: string[]
}

export interface TriggerReliabilityAnalysis {
  total_tracked: number
  completed: number
  avg_30d_return: number | null
  win_rate_30d: number | null
}

export interface TriggerReliabilityTrading {
  count: number
  win_rate: number | null
  avg_profit_rate: number | null
  profit_factor: number | null
}

export interface TriggerReliabilityPrinciple {
  condition: string
  action: string
  confidence: number
  supporting_trades: number
}

export interface TriggerReliabilityItem {
  trigger_type: string
  grade: "A" | "B" | "C" | "D"
  analysis_accuracy: TriggerReliabilityAnalysis
  actual_trading: TriggerReliabilityTrading
  related_principles: TriggerReliabilityPrinciple[]
  recommendation: string
}

export interface TriggerReliabilityData {
  trigger_reliability: TriggerReliabilityItem[]
  best_trigger: string | null
  last_updated: string
}

export interface TradingInsightsData {
  summary: InsightsSummary
  principles: TradingPrinciple[]
  journal_entries: TradingJournal[]
  intuitions: TradingIntuition[]
  performance_analysis?: PerformanceAnalysis
  trigger_reliability?: TriggerReliabilityData
}

export interface ObservabilityTradeMetrics {
  count: number
  win_rate: number | null
  avg_return_pct: number | null
  median_return_pct: number | null
  avg_win_pct: number | null
  avg_loss_pct: number | null
  profit_factor: number | null
  stop_rate: number | null
  sample_sufficient: boolean
}

export interface ObservabilityCandidateMetrics {
  count: number
  positive_rate_30d: number | null
  avg_7d_pct: number | null
  median_7d_pct: number | null
  avg_14d_pct: number | null
  median_14d_pct: number | null
  avg_30d_pct: number | null
  median_30d_pct: number | null
  sample_sufficient: boolean
}

export interface ObservabilityTriggerRow {
  trigger_type: string
  actual: ObservabilityTradeMetrics
  candidate: ObservabilityCandidateMetrics
}

export interface ObservabilityMarketSnapshot {
  actual: ObservabilityTradeMetrics
  candidate: ObservabilityCandidateMetrics
  triggers: ObservabilityTriggerRow[]
  latest_regime: {
    regime: string | null
    confidence: number | null
    observed_at: string
  } | null
  regime_distribution: Array<{ regime: string; count: number }>
  context_ledger?: {
    total: number
    candidates: number
    entries: number
    exits: number
    with_decision_id: number
    with_position_id: number
    complete_position_chains: number
    latest_at: string | null
  }
  entry_quality_capture?: {
    coverage_start_at: string | null
    legacy_candidate_count: number
    candidate_count: number
    captured_count: number
    coverage_rate: number | null
    status_distribution: Record<string, number>
    component_status: Record<string, Record<string, number>>
    fill_reconciliation_count: number
    fill_status_distribution: Record<string, number>
    confirmed_fill_count: number
  }
  journal_influence_capture?: {
    candidate_count: number
    captured_count: number
    coverage_rate: number | null
    enabled_count: number
    input_present_count: number
    llm_referenced_count: number
    deterministic_adjustment_count: number
    status_distribution: Record<string, number>
    threshold_crossing_distribution: Record<string, number>
    component_item_counts: Record<string, number>
    causal_interpretation: string
  }
}

export interface ObservabilityDeployment {
  timestamp: string
  git_sha: string
  target: string
  prs: number[]
  subject?: string | null
  ingestion_mode: "live" | "backfill"
  verified_actual_deployment: boolean
}

export interface ObservabilityDeploymentImpact extends ObservabilityDeployment {
  window_days: number
  post_window_complete: boolean
  markets: Record<Market, {
    pre: ObservabilityTradeMetrics
    post: ObservabilityTradeMetrics
  }>
}

export interface ObservabilityInsightsSnapshot {
  schema_version: number
  generated_at: string
  retention_days: number
  data_quality: {
    total_events: number
    backfill_events: number
    live_events: number
    coverage_start: string | null
    last_event_at: string | null
  }
  markets: Record<Market, ObservabilityMarketSnapshot>
  deployments: ObservabilityDeployment[]
  deployment_impacts: ObservabilityDeploymentImpact[]
}

// ── Stance 프로토콜 리더보드 ──────────────────────────────────────
// 대시보드 데이터와 별도 파일(/stance_leaderboard.json)로 제공된다.
// 원장을 재생해 만든 계산 결과이므로 언제든 다시 만들 수 있다.

export interface StanceMetrics {
  trading_days: number
  cumulative_return: number
  sortino: number
  max_drawdown: number
  calmar: number
  avg_exposure: number      // 다른 지표는 이 값과 함께 읽어야 한다
  coverage: number
  cadence: string
  turnover: number
  closed_trades: number
  win_rate: number | null
  excess_return: number | null
  paused_days: number
  pending: number
}

export interface StanceEntry {
  strategy: string
  display_name: string
  handle: string
  market: string
  owner_name: string | null
  tagline: string | null
  description: string | null
  website_url: string | null
  source_url: string | null
  latest_decision: {
    seq: number
    kind: "set" | "hold" | "pause" | "resume"
    symbol: string | null
    target_weight: number | null
    received_at: string
    admit: "accepted" | "clamped" | "rejected" | "pending" | null
  } | null
  qualified: boolean
  gate_failures: string[]
  experimental: boolean
  metrics: StanceMetrics
}

export interface StanceBoard {
  market: string
  currency: string
  support: string           // stable | experimental
  price_authority: string
  mark_at: string
  min_track_periods: number
  notes: string[]
  entries: StanceEntry[]
}

export interface StanceLeaderboard {
  schema: string
  protocol: string
  score_profile: string
  generated_at: string
  status: "live" | "preparing"
  boards: Record<string, StanceBoard>
}
