"use client"

import {
  Activity,
  ArrowDownRight,
  ArrowUpRight,
  Clock3,
  Database,
  GitCommitHorizontal,
  Radar,
  ShieldCheck,
} from "lucide-react"
import { Badge } from "@/components/ui/badge"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { useLanguage } from "@/components/language-provider"
import { cn } from "@/lib/utils"
import type {
  Market,
  ObservabilityInsightsSnapshot,
  ObservabilityTradeMetrics,
} from "@/types/dashboard"

interface Props {
  data: ObservabilityInsightsSnapshot
  market: Market
}

const pct = (value: number | null, digits = 1) =>
  value === null || value === undefined
    ? "—"
    : (value * 100).toFixed(digits) + "%"

const pctDirect = (value: number | null, digits = 1) =>
  value === null || value === undefined
    ? "—"
    : (value >= 0 ? "+" : "") + value.toFixed(digits) + "%"

const number = (value: number | null, digits = 2) =>
  value === null || value === undefined ? "—" : value.toFixed(digits)

function SampleBadge({
  sufficient,
  language,
}: {
  sufficient: boolean
  language: string
}) {
  return (
    <Badge
      variant="outline"
      className={cn(
        sufficient
          ? "border-emerald-500/30 text-emerald-600"
          : "border-amber-500/30 text-amber-600",
      )}
    >
      {sufficient
        ? language === "ko" ? "표본 확보" : "Sample ready"
        : language === "ko" ? "표본 부족" : "Low sample"}
    </Badge>
  )
}

function metricDelta(
  pre: ObservabilityTradeMetrics,
  post: ObservabilityTradeMetrics,
) {
  if (pre.avg_return_pct === null || post.avg_return_pct === null) return null
  return post.avg_return_pct - pre.avg_return_pct
}

export function ObservabilityInsightsPanel({ data, market }: Props) {
  const { language } = useLanguage()
  const current = data.markets[market]
  const latestEvent = data.generated_at ? new Date(data.generated_at) : null
  const staleMinutes = latestEvent
    ? Math.max(0, (Date.now() - latestEvent.getTime()) / 60_000)
    : Number.POSITIVE_INFINITY
  const impacts = data.deployment_impacts.slice().reverse().slice(0, 8)
  const contextLedger = current.context_ledger
  const entryQualityCapture = current.entry_quality_capture
  const journalCapture = current.journal_influence_capture

  return (
    <section className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <Radar className="h-5 w-5 text-cyan-500" />
          <div>
            <h3 className="font-semibold">
              {language === "ko" ? "PRISM 관측 센터" : "PRISM Observatory"}
            </h3>
            <p className="text-xs text-muted-foreground">
              {language === "ko"
                ? "ClickHouse 원본 이벤트를 정제한 매매 성과와 배포 영향"
                : "Curated trading performance and deployment impact from ClickHouse events"}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <Badge
            variant="outline"
            className={cn(
              staleMinutes <= 30
                ? "border-emerald-500/30 text-emerald-600"
                : "border-red-500/30 text-red-600",
            )}
          >
            <Activity className="mr-1 h-3 w-3" />
            {staleMinutes <= 30
              ? language === "ko" ? "수집 정상" : "Fresh"
              : language === "ko" ? "갱신 지연" : "Stale"}
          </Badge>
          <Badge variant="secondary">{market}</Badge>
        </div>
      </div>

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
        <Card>
          <CardHeader className="pb-2">
            <CardDescription>
              {language === "ko" ? "실제 매매" : "Actual trades"}
            </CardDescription>
            <CardTitle className="text-2xl">{current.actual.count}</CardTitle>
          </CardHeader>
          <CardContent className="space-y-1 text-sm">
            <div className="flex justify-between">
              <span>{language === "ko" ? "승률" : "Win rate"}</span>
              <strong>{pct(current.actual.win_rate)}</strong>
            </div>
            <div className="flex justify-between">
              <span>PF</span>
              <strong>{number(current.actual.profit_factor)}</strong>
            </div>
            <div className="flex justify-between">
              <span>{language === "ko" ? "평균" : "Average"}</span>
              <strong>{pctDirect(current.actual.avg_return_pct)}</strong>
            </div>
            <SampleBadge
              sufficient={current.actual.sample_sufficient}
              language={language}
            />
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardDescription>
              {language === "ko" ? "매매 컨텍스트 원장" : "Trade context ledger"}
            </CardDescription>
            <CardTitle className="text-2xl">
              {(contextLedger?.total || 0).toLocaleString()}
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-1 text-sm">
            <div className="flex justify-between">
              <span>{language === "ko" ? "후보/진입/청산" : "Candidate/Entry/Exit"}</span>
              <strong>
                {contextLedger?.candidates || 0}/{contextLedger?.entries || 0}/{contextLedger?.exits || 0}
              </strong>
            </div>
            <div className="flex justify-between">
              <span>{language === "ko" ? "완결 포지션" : "Complete chains"}</span>
              <strong>{contextLedger?.complete_position_chains || 0}</strong>
            </div>
            <div className="flex justify-between">
              <span>{language === "ko" ? "진입품질 수집률" : "Entry-quality capture"}</span>
              <strong>{pct(entryQualityCapture?.coverage_rate ?? null, 0)}</strong>
            </div>
            <div className="flex justify-between">
              <span>{language === "ko" ? "매매일지 영향 수집률" : "Journal influence capture"}</span>
              <strong>{pct(journalCapture?.coverage_rate ?? null, 0)}</strong>
            </div>
            <div className="flex justify-between text-xs">
              <span>{language === "ko" ? "LLM 참고/입력 있음" : "LLM referenced/input present"}</span>
              <strong>
                {journalCapture?.llm_referenced_count || 0}/
                {journalCapture?.input_present_count || 0}
              </strong>
            </div>
            <div className="flex justify-between text-xs">
              <span>{language === "ko" ? "점수조정/threshold 변경" : "Score adjust/threshold flip"}</span>
              <strong>
                {journalCapture?.deterministic_adjustment_count || 0}/
                {(journalCapture?.threshold_crossing_distribution?.ALLOW_TO_BLOCK || 0) +
                  (journalCapture?.threshold_crossing_distribution?.BLOCK_TO_ALLOW || 0)}
              </strong>
            </div>
            <div className="flex justify-between text-xs">
              <span>{language === "ko" ? "체결 확인/주문 관측" : "Confirmed/observed fills"}</span>
              <strong>
                {entryQualityCapture?.confirmed_fill_count || 0}/
                {entryQualityCapture?.fill_reconciliation_count || 0}
              </strong>
            </div>
            <div className="text-xs text-muted-foreground">
              {contextLedger?.latest_at
                ? new Date(contextLedger.latest_at).toLocaleString(
                    language === "ko" ? "ko-KR" : "en-US",
                  )
                : language === "ko" ? "수집 대기" : "Awaiting events"}
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardDescription>
              {language === "ko" ? "관찰 후보" : "Watched candidates"}
            </CardDescription>
            <CardTitle className="text-2xl">{current.candidate.count}</CardTitle>
          </CardHeader>
          <CardContent className="space-y-1 text-sm">
            <div className="flex justify-between">
              <span>{language === "ko" ? "30일 상승" : "30d positive"}</span>
              <strong>{pct(current.candidate.positive_rate_30d)}</strong>
            </div>
            <div className="flex justify-between">
              <span>{language === "ko" ? "30일 평균" : "30d average"}</span>
              <strong>{pctDirect(current.candidate.avg_30d_pct)}</strong>
            </div>
            <div className="flex justify-between">
              <span>{language === "ko" ? "30일 중앙값" : "30d median"}</span>
              <strong>{pctDirect(current.candidate.median_30d_pct)}</strong>
            </div>
            <SampleBadge
              sufficient={current.candidate.sample_sufficient}
              language={language}
            />
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardDescription>
              {language === "ko" ? "현재 시장 국면" : "Latest regime"}
            </CardDescription>
            <CardTitle className="text-xl capitalize">
              {current.latest_regime?.regime || "—"}
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-2 text-sm">
            <div className="flex justify-between">
              <span>{language === "ko" ? "신뢰도" : "Confidence"}</span>
              <strong>{pct(current.latest_regime?.confidence ?? null)}</strong>
            </div>
            <div className="text-xs text-muted-foreground">
              {current.latest_regime?.observed_at
                ? new Date(current.latest_regime.observed_at).toLocaleString(
                    language === "ko" ? "ko-KR" : "en-US",
                  )
                : "—"}
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardDescription>
              {language === "ko" ? "관측 데이터" : "Telemetry"}
            </CardDescription>
            <CardTitle className="text-2xl">
              {data.data_quality.total_events.toLocaleString()}
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-1 text-sm">
            <div className="flex justify-between">
              <span>Live</span>
              <strong>{data.data_quality.live_events}</strong>
            </div>
            <div className="flex justify-between">
              <span>Backfill</span>
              <strong>{data.data_quality.backfill_events}</strong>
            </div>
            <div className="flex items-center gap-1 text-xs text-muted-foreground">
              <Database className="h-3 w-3" />
              {language === "ko"
                ? data.retention_days + "일 보존"
                : data.retention_days + "d retention"}
            </div>
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <ShieldCheck className="h-4 w-4 text-emerald-500" />
            {language === "ko"
              ? "트리거별 Candidate vs Actual"
              : "Candidate vs Actual by trigger"}
          </CardTitle>
          <CardDescription>
            {language === "ko"
              ? "관찰 후보의 30일 성과와 실제 청산 성과는 서로 다른 지표로 표시합니다."
              : "Watched 30-day outcomes and realized trade outcomes remain separate."}
          </CardDescription>
        </CardHeader>
        <CardContent className="overflow-x-auto">
          <table className="w-full min-w-[760px] text-sm">
            <thead>
              <tr className="border-b text-left text-muted-foreground">
                <th className="p-2">{language === "ko" ? "트리거" : "Trigger"}</th>
                <th className="p-2 text-right">Actual n</th>
                <th className="p-2 text-right">
                  {language === "ko" ? "실제 승률" : "Actual WR"}
                </th>
                <th className="p-2 text-right">PF</th>
                <th className="p-2 text-right">
                  {language === "ko" ? "실제 평균" : "Actual avg"}
                </th>
                <th className="p-2 text-right">Candidate n</th>
                <th className="p-2 text-right">
                  {language === "ko" ? "30일 상승" : "30d positive"}
                </th>
                <th className="p-2 text-right">
                  {language === "ko" ? "후보 평균" : "Candidate avg"}
                </th>
              </tr>
            </thead>
            <tbody>
              {current.triggers.slice(0, 12).map((row) => (
                <tr key={row.trigger_type} className="border-b border-border/40">
                  <td className="p-2 font-medium">{row.trigger_type}</td>
                  <td className="p-2 text-right">{row.actual.count}</td>
                  <td className="p-2 text-right">{pct(row.actual.win_rate, 0)}</td>
                  <td className="p-2 text-right">{number(row.actual.profit_factor)}</td>
                  <td className="p-2 text-right">{pctDirect(row.actual.avg_return_pct)}</td>
                  <td className="p-2 text-right">{row.candidate.count}</td>
                  <td className="p-2 text-right">
                    {pct(row.candidate.positive_rate_30d, 0)}
                  </td>
                  <td className="p-2 text-right">
                    {pctDirect(row.candidate.avg_30d_pct)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <GitCommitHorizontal className="h-4 w-4 text-violet-500" />
            {language === "ko" ? "배포 전후 14일 영향" : "14-day deployment impact"}
          </CardTitle>
          <CardDescription>
            {language === "ko"
              ? "매수일 기준 전후 코호트 비교입니다. 인과관계가 아니라 관측된 변화입니다."
              : "Buy-date cohort comparison. This is observed association, not proof of causality."}
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-2">
          {impacts.length === 0 && (
            <p className="text-sm text-muted-foreground">
              {language === "ko" ? "배포 기록이 없습니다." : "No deployment records."}
            </p>
          )}
          {impacts.map((impact) => {
            const currentImpact = impact.markets[market]
            const delta = metricDelta(currentImpact.pre, currentImpact.post)
            return (
              <div
                key={impact.git_sha + "-" + impact.target}
                className="grid gap-2 rounded-lg border p-3 md:grid-cols-[1.5fr_1fr_1fr_1fr] md:items-center"
              >
                <div>
                  <div className="flex flex-wrap items-center gap-2">
                    <code className="text-xs">{impact.git_sha.slice(0, 8)}</code>
                    <Badge variant="outline">{impact.target}</Badge>
                    {!impact.post_window_complete && (
                      <Badge variant="secondary">
                        {language === "ko" ? "관측 중" : "In progress"}
                      </Badge>
                    )}
                  </div>
                  <div className="mt-1 flex items-center gap-1 text-xs text-muted-foreground">
                    <Clock3 className="h-3 w-3" />
                    {new Date(impact.timestamp).toLocaleString(
                      language === "ko" ? "ko-KR" : "en-US",
                    )}
                  </div>
                  {impact.subject && (
                    <p className="mt-1 line-clamp-1 text-xs text-muted-foreground">
                      {impact.subject}
                    </p>
                  )}
                </div>
                <div className="text-sm">
                  <span className="text-muted-foreground">
                    {language === "ko" ? "이전 " : "Pre "}
                  </span>
                  <strong>
                    {currentImpact.pre.count} / {pctDirect(currentImpact.pre.avg_return_pct)}
                  </strong>
                </div>
                <div className="text-sm">
                  <span className="text-muted-foreground">
                    {language === "ko" ? "이후 " : "Post "}
                  </span>
                  <strong>
                    {currentImpact.post.count} / {pctDirect(currentImpact.post.avg_return_pct)}
                  </strong>
                </div>
                <div
                  className={cn(
                    "flex items-center justify-end gap-1 font-semibold",
                    delta === null
                      ? "text-muted-foreground"
                      : delta >= 0
                        ? "text-emerald-600"
                        : "text-red-600",
                  )}
                >
                  {delta === null ? null : delta >= 0
                    ? <ArrowUpRight className="h-4 w-4" />
                    : <ArrowDownRight className="h-4 w-4" />}
                  {delta === null ? "—" : pctDirect(delta)}
                </div>
              </div>
            )
          })}
        </CardContent>
      </Card>
    </section>
  )
}
