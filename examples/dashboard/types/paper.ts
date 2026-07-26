import type { Market, StrategyId } from "./research"

export interface InternalPaperPosition {
  security_id: string
  quantity: string
  average_cost: string
  as_of: string
}

export interface InternalPaperNav {
  value: string
  cash: string
  as_of: string
}

export interface InternalPaperBook {
  book_id: string
  strategy_id: StrategyId
  market: Market
  currency: string
  created_at: string
  positions: InternalPaperPosition[]
  nav: InternalPaperNav | null
  order_state_counts: Record<string, number>
}

export interface PaperDashboardContract {
  environment: "INTERNAL_PAPER"
  external_broker: false
  readiness: "FOUNDATION_ONLY" | "PERSISTED_LEDGER_READ_ONLY"
  books: InternalPaperBook[]
}
