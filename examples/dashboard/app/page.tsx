"use client"

import { useEffect, useMemo, useState } from "react"
import { Activity, Database, ShieldCheck, TrendingUp } from "lucide-react"
import type { DashboardData, Market, StrategyProposal } from "@/types/dashboard"

const DATA_PATH = "/dashboard_data.json"

type KrDailyCandidate = {
  security_id: string
  channel: string
  trigger: string
  swing: { state: string; actionable_levels_suppressed?: boolean }
  trend: { state: string; actionable_levels_suppressed?: boolean }
  current_state: string
  top_support: string | null
  top_counter_evidence: string | null
  change: "NEW" | "MAINTAINED" | "EXITED" | "DATA_MISSING"
}

type KrDailyLayer = {
  as_of: string | null
  source_quality: Array<Record<string, unknown>>
  call_evidence: Record<string, unknown>
  market_context: Record<string, unknown>
  candidate_table: KrDailyCandidate[]
  conditional_scenarios: Array<Record<string, unknown>>
  data_gaps: Array<Record<string, unknown>>
  changes: Array<{ security_id: string; state: string }>
  next_review: string[]
  audit: Array<Record<string, unknown>>
}

type KrDailyComposition = {
  candidates?: Array<{
    security_id: string
    channels?: string[]
    triggers?: string[]
  }>
}

function statusClass(value: string): string {
  if (value === "FRESH" || value === "ACCEPT" || value === "SUCCESS") {
    return "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400"
  }
  if (value === "REJECTED" || value === "REJECT" || value === "NO_ENTRY") {
    return "bg-rose-500/10 text-rose-600 dark:text-rose-400"
  }
  return "bg-amber-500/10 text-amber-700 dark:text-amber-400"
}

function Pill({ value }: { value: string }) {
  return <span className={`rounded-full px-2.5 py-1 text-xs font-semibold ${statusClass(value)}`}>{value}</span>
}

function Card({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="rounded-2xl border border-border/60 bg-card p-5 shadow-sm">
      <h2 className="mb-4 text-lg font-semibold">{title}</h2>
      {children}
    </section>
  )
}

function displayValue(value: unknown): string {
  if (value === null || value === undefined) return "없음"
  if (typeof value === "string") return value
  return JSON.stringify(value)
}

function ScenarioField({ title, value }: { title: string; value: unknown }) {
  const empty = value === undefined || value === null || (Array.isArray(value) && value.length === 0)
  return <div><dt className="text-muted-foreground">{title}</dt><dd className="whitespace-pre-wrap break-words">{empty ? "없음" : displayValue(value)}</dd></div>
}

function ProposalCard({ proposal }: { proposal: StrategyProposal }) {
  const scenario = proposal.scenario
  return (
    <div className="rounded-xl border border-border/50 bg-muted/20 p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <p className="font-semibold">{proposal.strategy_id}</p>
          <p className="text-xs text-muted-foreground">{proposal.security_id} · {proposal.strategy_version}</p>
        </div>
        <div className="flex gap-2"><Pill value={proposal.status} /><Pill value={proposal.scenario_state} /></div>
      </div>
      <dl className="mt-4 grid gap-2 text-sm sm:grid-cols-2">
        <div><dt className="text-muted-foreground">현재 판정</dt><dd>{proposal.proposed_decision || proposal.scenario_state}</dd></div>
        <div><dt className="text-muted-foreground">데이터 품질</dt><dd>{proposal.data_quality} / {proposal.quality_disposition}</dd></div>
        <div><dt className="text-muted-foreground">근거 기준</dt><dd>{proposal.snapshot_id} · {proposal.available_at}</dd></div>
        <div><dt className="text-muted-foreground">모델·프롬프트</dt><dd>{proposal.model.model_id} · {proposal.model.prompt_version}</dd></div>
        <div className="sm:col-span-2"><dt className="text-muted-foreground">완결 상태</dt><dd>{proposal.scenario_complete ? "완결" : "미완결"}</dd></div>
        {!proposal.scenario_complete && <div className="sm:col-span-2"><dt className="text-muted-foreground">검증 사유</dt><dd>{proposal.scenario_reasons.length ? proposal.scenario_reasons.join(" · ") : "완결 조건을 충족하지 못했습니다."}</dd></div>}
        {proposal.scenario_complete && <>
          <ScenarioField title="시장 판단" value={scenario.market_judgment} />
          <ScenarioField title="섹터 판단" value={scenario.sector_judgment} />
          <ScenarioField title="종목 판단" value={scenario.security_judgment} />
          <ScenarioField title="상승 경로" value={scenario.bull_path} />
          <ScenarioField title="기본 경로" value={scenario.base_path} />
          <ScenarioField title="하락 경로" value={scenario.bear_path} />
          <ScenarioField title="진입 조건" value={scenario.entry_triggers || scenario.triggers} />
          <ScenarioField title="회피 조건" value={scenario.avoid_triggers} />
          <ScenarioField title="무효화·실패 전환" value={scenario.failure_transition} />
          <ScenarioField title="손절 후보" value={scenario.stop_candidates} />
          <ScenarioField title="목표 후보" value={scenario.target_candidates} />
          <ScenarioField title="리스크 배수 후보" value={scenario.risk_multiplier_candidate} />
          <ScenarioField title="재진입 후보" value={scenario.reentry_candidates} />
          <ScenarioField title="피라미딩 후보" value={scenario.pyramiding_candidates} />
          <ScenarioField title="지지 근거" value={scenario.bull_evidence_ids} />
          <ScenarioField title="반대 근거" value={scenario.bear_evidence_ids} />
          <ScenarioField title="반증 조건" value={proposal.falsifiers} />
          <ScenarioField title="불확실성" value={scenario.uncertainty || proposal.uncertainty} />
          <ScenarioField title="다음 검토" value={scenario.next_review_at} />
          <ScenarioField title="필드 판정" value={scenario.field_dispositions} />
        </>}
      </dl>
    </div>
  )
}

function KrDailyOverview({ daily, composition }: { daily: KrDailyLayer; composition?: KrDailyComposition }) {
  const candidateContext = new Map(
    (composition?.candidates || []).map((item) => [item.security_id, item]),
  )
  return <div className="space-y-6">
    <Card title="1. 원천·기준시점·호출 증거와 데이터 품질">
      <p className="text-sm">기준일 {daily.as_of || "UNAVAILABLE"}</p>
      <pre className="mt-3 overflow-auto whitespace-pre-wrap text-xs text-muted-foreground">{displayValue({ source_quality: daily.source_quality, call_evidence: daily.call_evidence })}</pre>
    </Card>
    <Card title="2. KR 시장 국면·수급·주도/약세 그룹">
      <pre className="overflow-auto whitespace-pre-wrap text-xs text-muted-foreground">{displayValue(daily.market_context)}</pre>
    </Card>
    <Card title="3. 후보 요약">
      {daily.candidate_table.length === 0 ? <p className="text-sm text-muted-foreground">저장된 KR 후보가 없습니다.</p> : <div className="overflow-x-auto"><table className="w-full min-w-[900px] text-left text-sm"><thead><tr className="border-b"><th className="p-2">종목</th><th className="p-2">채널</th><th className="p-2">트리거</th><th className="p-2">SWING</th><th className="p-2">TREND</th><th className="p-2">현재</th><th className="p-2">지지/반대</th><th className="p-2">변화</th></tr></thead><tbody>{daily.candidate_table.map((candidate) => {
        const context = candidateContext.get(candidate.security_id)
        return <tr key={candidate.security_id} className="border-b border-border/40"><td className="p-2 font-medium">{candidate.security_id}</td><td className="p-2">{context?.channels?.join(", ") || candidate.channel}</td><td className="p-2">{context?.triggers?.join(", ") || candidate.trigger}</td><td className="p-2"><Pill value={candidate.swing.state} /></td><td className="p-2"><Pill value={candidate.trend.state} /></td><td className="p-2">{candidate.current_state}</td><td className="p-2">{candidate.top_support || "없음"} / {candidate.top_counter_evidence || "없음"}</td><td className="p-2"><Pill value={candidate.change} /></td></tr>
      })}</tbody></table></div>}
    </Card>
    <Card title="4. 종목별 SWING/TREND 카드">
      <p className="text-sm text-muted-foreground">아래 전략별 카드에서 같은 종목의 SWING과 TREND 판단을 각각 확인합니다.</p>
    </Card>
    <Card title="5. 조건부 진입·회피와 무효화·축소/청산">
      <pre className="overflow-auto whitespace-pre-wrap text-xs text-muted-foreground">{displayValue(daily.conditional_scenarios)}</pre>
    </Card>
    <Card title="6. 데이터 공백·충돌과 가격 수준 공개 여부">
      <p className="text-sm text-muted-foreground">{daily.candidate_table.some((item) => item.swing.actionable_levels_suppressed || item.trend.actionable_levels_suppressed) ? "데이터 품질 제한으로 정확한 가격 수준이 숨겨졌습니다." : "ACCEPT 품질의 저장 시나리오 후보만 가격 수준을 표시합니다."}</p>
      <pre className="mt-3 overflow-auto whitespace-pre-wrap text-xs text-muted-foreground">{displayValue(daily.data_gaps)}</pre>
    </Card>
    <Card title="7. 이전 실행 대비 변화">
      <div className="flex flex-wrap gap-2">{daily.changes.length ? daily.changes.map((item) => <span key={`${item.security_id}:${item.state}`} className="rounded-lg border px-3 py-2 text-sm">{item.security_id} <Pill value={item.state} /></span>) : <span className="text-sm text-muted-foreground">DATA_MISSING</span>}</div>
    </Card>
    <Card title="8. 다음 이벤트와 검토 시각">
      <p className="text-sm">{daily.next_review.length ? daily.next_review.join(" · ") : "UNAVAILABLE"}</p>
    </Card>
    <Card title="9. 감사 상세">
      <details className="rounded-xl border border-border/50 p-4"><summary className="cursor-pointer font-medium">IDs, dispositions, hashes, validator internals</summary><pre className="mt-4 overflow-auto whitespace-pre-wrap text-xs text-muted-foreground">{displayValue(daily.audit)}</pre></details>
    </Card>
  </div>
}

export default function DashboardPage() {
  const [data, setData] = useState<DashboardData | null>(null)
  const [market, setMarket] = useState<Market>("KR")
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let active = true
    fetch(DATA_PATH, { cache: "no-store" })
      .then((response) => {
        if (!response.ok) throw new Error(`HTTP ${response.status}`)
        return response.json() as Promise<DashboardData>
      })
      .then((payload) => { if (active) setData(payload) })
      .catch(() => { if (active) setError("대시보드 데이터를 불러올 수 없습니다.") })
    return () => { active = false }
  }, [])

  const view = useMemo(() => {
    if (!data) return null
    const daily = (
      data.research as typeof data.research & { kr_daily?: KrDailyLayer }
    ).kr_daily
    const composition = (
      data as DashboardData & { kr_daily_composition?: KrDailyComposition }
    ).kr_daily_composition
    return {
      freshness: data.research.data_freshness.filter((item) => item.market === market),
      leaders: data.research.daily_leaders.filter((item) => item.market === market),
      swing: data.research.swing_v1_proposals.filter((item) => item.market === market),
      trend: data.research.trend_v1_proposals.filter((item) => item.market === market),
      evidence: data.research.scenario_evidence_falsifiers.filter((item) => item.market === market),
      feedback: data.research.shadow_feedback,
      jobs: data.ops.jobs,
      daily,
      composition,
    }
  }, [data, market])

  return (
    <div className="min-h-screen bg-background">
      <header className="border-b border-border/60 bg-card/90 backdrop-blur">
        <div className="container mx-auto flex max-w-[1500px] flex-wrap items-center justify-between gap-4 px-4 py-5">
          <div className="flex items-center gap-3">
            <div className="rounded-xl bg-gradient-to-br from-primary to-blue-600 p-2.5"><TrendingUp className="h-6 w-6 text-white" /></div>
            <div><h1 className="text-xl font-bold">Prism Insight</h1><p className="text-xs text-muted-foreground">읽기 전용 투자 리서치 · Phase 1 SHADOW</p></div>
          </div>
          <div className="flex rounded-xl bg-muted p-1">
            {(["KR", "US"] as Market[]).map((item) => (
              <button key={item} onClick={() => setMarket(item)} className={`rounded-lg px-4 py-2 text-sm font-semibold ${market === item ? "bg-background shadow-sm" : "text-muted-foreground"}`}>{item === "KR" ? "한국" : "미국"}</button>
            ))}
          </div>
        </div>
      </header>

      <main className="container mx-auto max-w-[1500px] space-y-6 px-4 py-6">
        {error && <div className="rounded-xl border border-rose-500/30 bg-rose-500/10 p-4 text-rose-700">{error}</div>}
        {!data || !view ? <p className="py-20 text-center text-muted-foreground">데이터를 불러오는 중입니다.</p> : <>
          {market === "KR" && view.daily && <KrDailyOverview daily={view.daily} composition={view.composition} />}
          <div className="grid gap-4 md:grid-cols-3">
            <Card title="데이터 기준 시점"><div className="flex items-start gap-3"><Database className="mt-1 h-5 w-5 text-primary" /><div><p className="text-sm">{data.as_of}</p><p className="mt-1 text-xs text-muted-foreground">생성 {data.generated_at}</p></div></div></Card>
            <Card title="품질 판정"><div className="flex items-center gap-3"><ShieldCheck className="h-5 w-5 text-primary" />{view.freshness.length ? view.freshness.map((item) => <Pill key={item.snapshot_id} value={item.quality} />) : <Pill value="UNAVAILABLE" />}</div></Card>
            <Card title="최근 작업"><div className="flex items-center gap-3"><Activity className="h-5 w-5 text-primary" /><span className="text-2xl font-bold">{view.jobs.length}</span><span className="text-sm text-muted-foreground">persisted jobs</span></div></Card>
          </div>

          <Card title="주도 종목">
            {view.leaders.length === 0 ? <p className="text-sm text-muted-foreground">저장된 종목별 리더십 증거가 없습니다. 값을 추정하지 않습니다.</p> : <div className="grid gap-3 md:grid-cols-2">{view.leaders.map((leader) => <div key={`${leader.snapshot_id}:${leader.security_id}`} className="rounded-xl border p-4"><div className="flex justify-between"><strong>{leader.name || leader.symbol || leader.provider_symbol}</strong><Pill value={leader.quality} /></div><p className="mt-2 text-sm text-muted-foreground">{leader.decision_status || "REPORT_ONLY"}</p></div>)}</div>}
          </Card>


          <div className="grid gap-6 lg:grid-cols-2">
            <Card title="SWING_V1 제안"><div className="space-y-3">{view.swing.length ? view.swing.map((item) => <ProposalCard key={item.proposal_record_id} proposal={item} />) : <p className="text-sm text-muted-foreground">ANALYSIS_INCOMPLETE · 저장된 완결 시나리오가 없습니다.</p>}</div></Card>
            <Card title="TREND_V1 제안"><div className="space-y-3">{view.trend.length ? view.trend.map((item) => <ProposalCard key={item.proposal_record_id} proposal={item} />) : <p className="text-sm text-muted-foreground">ANALYSIS_INCOMPLETE · 저장된 완결 시나리오가 없습니다.</p>}</div></Card>
          </div>

          <Card title="시나리오·근거·반증"><p className="text-sm text-muted-foreground">{view.evidence.length ? `${view.evidence.length}개의 저장된 완결 시나리오 증거 묶음` : "검증을 통과한 완결 시나리오가 없습니다. 투자 판정을 생성하지 않습니다."}</p></Card>
          <Card title="SHADOW 피드백"><p className="text-sm text-muted-foreground">{view.feedback.length ? `${view.feedback.length}개의 전략별 SHADOW 교훈 후보` : "활성화된 교훈이 없습니다."}</p></Card>
          <div className="rounded-xl border border-blue-500/20 bg-blue-500/5 p-4 text-sm text-muted-foreground">본 화면은 연구용 읽기 전용 결과입니다. 수량·계좌·주문·실행 승인을 포함하지 않습니다.</div>
        </>}
      </main>
    </div>
  )
}