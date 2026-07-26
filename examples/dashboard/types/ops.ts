export interface DashboardJob {
  run_id: string
  job_key: string
  status: string
  started_at: string
  finished_at: string | null
  last_heartbeat: string | null
}

export interface OpsDashboardContract {
  jobs: DashboardJob[]
}
