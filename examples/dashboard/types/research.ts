export type Market = "KR" | "US"
export type StrategyId = "SWING_V1" | "TREND_V1"
export type DataQuality = "FRESH" | "STALE" | "PARTIAL" | "UNAVAILABLE" | "CONFLICT"

export interface DataFreshness {
  market: Market
  snapshot_id: string
  as_of: string
  quality: DataQuality
  observed_at: string | null
  available_at: string | null
  ingested_at: string | null
}

export interface DailyLeader {
  market: Market
  snapshot_id: string
  security_id: string | null
  provider: string
  provider_symbol: string | null
  observed_at: string
  available_at: string
  ingested_at: string
  quality: DataQuality
  symbol: string | null
  name: string | null
  decision_status: string | null
  strategies: StrategyId[]
  relative_strength: Record<string, string | null>
  high_52_week: Record<string, string | null>
  momentum: Record<string, string | null>
  peak: Record<string, string | null>
  evidence_refs: string[]
}

export interface ProposalScenario {
  probabilities?: Record<string, string>
  confidence?: string
  drivers?: string[]
  falsifiers?: string[]
}

export interface StrategyProposal {
  proposal_record_id: string
  proposal_id: string | null
  revision: number
  strategy_id: StrategyId
  strategy_version: string
  market: Market
  security_id: string
  snapshot_id: string
  evidence_refs: string[]
  data_quality: DataQuality
  quality_disposition: "ACCEPT" | "REPORT_ONLY" | "REJECT"
  proposed_decision: "ENTRY_CANDIDATE" | "WATCH" | "NO_ENTRY" | "REPORT_ONLY" | null
  scenario: ProposalScenario
  bull_evidence_ids: string[]
  bear_evidence_ids: string[]
  falsifiers: string[]
  missing_or_stale_data: Array<Record<string, unknown>>
  uncertainty: Record<string, unknown>
  model: {
    provider: string
    model_id: string
    model_version: string
    prompt_version: string
  }
  available_at: string
}

export interface ScenarioEvidenceFalsifiers {
  proposal_record_id: string
  strategy_id: StrategyId
  market: Market
  security_id: string
  scenario: ProposalScenario
  bull_evidence_ids: string[]
  bear_evidence_ids: string[]
  falsifiers: string[]
  uncertainty: Record<string, unknown>
}

export interface ResearchOOS {
  status: "UNAVAILABLE" | "AVAILABLE"
  reason: string
  experiments: Array<Record<string, unknown>>
}

export interface ShadowFeedback {
  lesson_id: string
  strategy_id: StrategyId
  strategy_version: string
  revision: number
  status: "CANDIDATE" | "SHADOW" | "SUSPENDED" | "RETIRED"
  candidate: Record<string, unknown>
  observed_at: string
  available_at: string
  ingested_at: string
  as_of: string
}

export interface ResearchDashboardContract {
  data_freshness: DataFreshness[]
  daily_leaders: DailyLeader[]
  swing_v1_proposals: StrategyProposal[]
  trend_v1_proposals: StrategyProposal[]
  scenario_evidence_falsifiers: ScenarioEvidenceFalsifiers[]
  research_oos: ResearchOOS
  shadow_feedback: ShadowFeedback[]
}
