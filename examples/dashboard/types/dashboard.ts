import type { ResearchDashboardContract } from "./research"
import type { PaperDashboardContract } from "./paper"
import type { OpsDashboardContract } from "./ops"

export type { DataQuality, Market, StrategyId } from "./research"
export type {
  DailyLeader,
  DataFreshness,
  ResearchDashboardContract,
  ResearchOOS,
  ScenarioEvidenceFalsifiers,
  ShadowFeedback,
  StrategyProposal,
} from "./research"
export type {
  InternalPaperBook,
  InternalPaperNav,
  InternalPaperPosition,
  PaperDashboardContract,
} from "./paper"
export type { DashboardJob, OpsDashboardContract } from "./ops"

/**
 * Authoritative Phase 1 dashboard envelope.
 *
 * Research, internal-paper, and operations records remain separate.  This public
 * contract intentionally has no KIS account, broker portfolio, or external-paper
 * surface.
 */
export interface DashboardData {
  schema_version: "prism_dashboard_v1"
  generated_at: string
  as_of: string
  local_only: true
  bind_host: "127.0.0.1"
  research: ResearchDashboardContract
  paper: PaperDashboardContract
  ops: OpsDashboardContract
}
