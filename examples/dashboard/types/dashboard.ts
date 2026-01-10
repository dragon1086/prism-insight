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
  kospi_index: number
  kosdaq_index: number
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
  jeoningu_lab?: JeoninguLabData
  trading_insights?: TradingInsightsData
}

export interface JeoninguLabData {
  enabled: boolean
  message?: string
  error?: string
  summary: {
    total_trades: number
    winning_trades: number
    losing_trades: number
    win_rate: number
    cumulative_return: number
    avg_return_per_trade: number
    initial_capital: number
    current_balance: number
  }
  current_position: {
    stock_code: string
    stock_name: string
    quantity: number
    buy_price: number
    buy_amount: number
    buy_date: string
    video_id: string
    video_title: string
  } | null
  timeline: Array<{
    video_id: string
    video_title: string
    video_date: string
    video_url: string
    analyzed_date: string
    jeon_sentiment: string
    jeon_reasoning: string
    contrarian_action: string
    trade_type: string
    stock_code: string | null
    stock_name: string | null
    notes: string
    profit_loss: number | null
    profit_loss_pct: number | null
  }>
  cumulative_chart: Array<{
    date: string
    cumulative_return: number
    balance: number
  }>
  trade_history: any[]
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
  content: string
  priority: 'high' | 'medium' | 'low'
  category?: string
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

export interface TradingInsightsData {
  summary: InsightsSummary
  principles: TradingPrinciple[]
  journal_entries: TradingJournal[]
  intuitions: TradingIntuition[]
}
