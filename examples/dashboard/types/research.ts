export type Market = "KR" | "US"
export type StrategyId = "SWING_V1" | "TREND_V1"
export type DataQuality = "FRESH" | "STALE" | "PARTIAL" | "UNAVAILABLE" | "CONFLICT"
export type ProductScenarioState = "DATA_UNAVAILABLE" | "ANALYSIS_INCOMPLETE" | "INVALID_PROPOSAL" | "POLICY_REJECTED" | "REPORT_ONLY" | "WATCH" | "NO_ENTRY" | "ENTRY_CANDIDATE"

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

export interface ScenarioBranch {
  summary: string
  conditions: string[]
  confirmations: string[]
  falsifiers: string[]
  next_event: string
  valid_until: string
  evidence_ids: string[]
}

export interface ProposalScenario {
  regime?: {
    probabilities: Record<string, string>
    confidence: string
    drivers: string[]
  }
  bull_path?: ScenarioBranch
  base_path?: ScenarioBranch
  bear_path?: ScenarioBranch
  current_action?: "ENTRY_CANDIDATE" | "WATCH" | "NO_ENTRY" | "REPORT_ONLY"
  triggers?: Array<{
    feature_name: string
    operator: string
    comparison_value: string
    upper_value: string | null
    observed_value: string
    observed_result: "true" | "false"
    valid_until: string
    evidence_ids: string[]
  }>
  failure_transition?: string[]
  falsifiers?: string[]
  uncertainty?: {
    level: string
    known_unknowns: string[]
    assumptions: string[]
  }
  next_review_at?: string
  market_judgment?: Record<string, unknown>
  sector_judgment?: Record<string, unknown>
  security_judgment?: Record<string, unknown>
  entry_triggers?: Array<Record<string, unknown>>
  avoid_triggers?: Array<Record<string, unknown>>
  stop_candidates?: Array<Record<string, unknown>>
  target_candidates?: Array<Record<string, unknown>>
  risk_multiplier_candidate?: Record<string, unknown>
  reentry_candidates?: Array<Record<string, unknown>>
  pyramiding_candidates?: Array<Record<string, unknown>>
  bull_evidence_ids?: string[]
  bear_evidence_ids?: string[]
  counter_evidence?: string[]
  field_dispositions?: Array<{
    field_path: string
    action: "ACCEPT" | "CLAMP" | "RECALCULATE" | "REJECT"
    reason: string
    proposed_value: string | null
    resolved_value: string | null
    evidence_ids: string[]
  }>
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
  status: "ACCEPTED" | "REJECTED" | string
  scenario_state: ProductScenarioState
  scenario_complete: boolean
  scenario_reasons: string[]
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
