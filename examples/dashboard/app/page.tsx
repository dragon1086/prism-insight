"use client"

import { useEffect, useMemo, useState } from "react"
import { Activity, Database, ShieldCheck, TrendingUp } from "lucide-react"
import type { DashboardData, Market, StrategyProposal } from "@/types/dashboard"

const DATA_PATH = "/dashboard_data.json"

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
    return {
      freshness: data.research.data_freshness.filter((item) => item.market === market),
      leaders: data.research.daily_leaders.filter((item) => item.market === market),
      swing: data.research.swing_v1_proposals.filter((item) => item.market === market),
      trend: data.research.trend_v1_proposals.filter((item) => item.market === market),
      evidence: data.research.scenario_evidence_falsifiers.filter((item) => item.market === market),
      feedback: data.research.shadow_feedback,
      jobs: data.ops.jobs,
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