"use client"

import { useEffect, useState } from "react"
import { ExternalLink, Github } from "lucide-react"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { useLanguage } from "@/components/language-provider"
import { StanceRegistrationCard } from "@/components/stance-registration-card"
import { StanceAgentConnectCard } from "@/components/stance-agent-connect-card"
import type { StanceLeaderboard, StanceBoard, StanceEntry } from "@/types/dashboard"

const SPEC_URL = "https://github.com/dragon1086/prism-insight/blob/main/stance/spec/core-spec.md"

function pct(v: number | null | undefined, digits = 2) {
  if (v === null || v === undefined) return "—"
  return `${v >= 0 ? "+" : ""}${(v * 100).toFixed(digits)}%`
}

function plainGateFailure(message: string, ko: boolean) {
  const days = message.match(/^운영 (\d+)일 \(필요 (\d+)일\)$/)
  if (days) {
    return ko
      ? `공식 순위까지: ${days[1]}/${days[2]}거래일 기록`
      : `Official rank: ${days[1]}/${days[2]} trading days recorded`
  }
  const trades = message.match(/^청산 거래 (\d+)건 \(필요 (\d+)건\)$/)
  if (trades) {
    return ko
      ? `공식 순위까지: 주요 거래 ${trades[1]}/${trades[2]}건 완료`
      : `Official rank: ${trades[1]}/${trades[2]} qualifying trades closed`
  }
  return message
}

export function StanceLeaderboardPage() {
  const { language } = useLanguage()
  const ko = language === "ko"
  const [data, setData] = useState<StanceLeaderboard | null>(null)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    fetch("/api/stance/v1/leaderboard", { cache: "no-store" })
      .then((response) => (response.ok ? response.json() : Promise.reject(response.status)))
      // 로컬 UI 개발에서는 Stance 서버가 없을 수 있으므로 생성된 스냅샷을 사용한다.
      .catch(() => fetch("/stance_leaderboard.json").then((response) => (
        response.ok ? response.json() : Promise.reject(response.status)
      )))
      .then(setData)
      .catch(() => setFailed(true))
  }, [])

  const boards: StanceBoard[] = data ? Object.values(data.boards) : []
  const isEmpty = boards.every((b) => b.entries.length === 0)

  return (
    <div className="space-y-6">
      {/* ── 처음 온 사람을 위한 소개 ────────────────────────────── */}
      <Card className="border-border/50 overflow-hidden">
        <div className="bg-gradient-to-r from-slate-900 to-slate-700 dark:from-slate-800 dark:to-slate-900 px-6 py-8 text-white">
          <div className="flex items-center gap-3 mb-3">
            <h2 className="text-2xl font-bold tracking-tight">Stance</h2>
            <Badge variant="outline" className="text-white/80 border-white/30 text-xs">
              {ko ? "베타" : "Beta"}
            </Badge>
          </div>
          <p className="max-w-3xl break-keep text-2xl font-bold leading-snug text-white sm:text-3xl">
            {ko
              ? "말로만 잘하는 투자 전략, 이제 기록으로 비교하세요."
              : "Stop taking trading claims on faith. Compare the record."}
          </p>
          <p className="mt-3 max-w-3xl break-keep text-base leading-relaxed text-white/75">
            {ko
              ? "사기 전에 종목과 투자 비중을 남기면, 그다음 성과는 Stance가 계산합니다. 계좌를 공개하지 않아도 전략의 실력을 확인할 수 있습니다."
              : "Record the stock and target weight before buying. Stance computes what happens next, so anyone can verify a strategy without seeing the account."}
          </p>
        </div>

        <CardContent className="space-y-5 pt-6 text-sm leading-relaxed">
          <p className="break-keep text-base text-foreground">
            {ko ? (
              <>
                수익 인증 화면은 편집할 수 있고, 잘된 거래만 골라 보여줄 수도 있습니다. Stance는 결과가 나온 뒤의 자랑 대신
                <strong> 결과가 나오기 전의 선택</strong>을 기록합니다.
              </>
            ) : (
              <>
                Profit screenshots can be edited, and winning trades can be cherry-picked. Stance records the
                <strong> choice before the outcome</strong>, not the claim after it.
              </>
            )}
          </p>

          <div className="grid gap-3 sm:grid-cols-3 pt-2">
            {[
              [ko ? "1. 사기 전에 계획 남기기" : "1. Record the plan first", ko ? "어떤 종목을 자산의 몇 %까지 살지 기록" : "Save the stock and intended share of the portfolio"],
              [ko ? "2. 그때 가격 자동 저장" : "2. Lock in the price", ko ? "서버가 시간과 당시 시장가격을 자동으로 보관" : "The server saves the time and market price automatically"],
              [ko ? "3. 결과 자동 계산" : "3. Let the record speak", ko ? "이후 가격을 따라 수익과 위험을 같은 기준으로 계산" : "Returns and risk are calculated later on the same rules"],
            ].map(([title, desc]) => (
              <div key={title} className="rounded-lg border border-border/50 bg-muted/30 p-4">
                <div className="font-semibold mb-1">{title}</div>
                <div className="text-xs text-muted-foreground leading-relaxed">{desc}</div>
              </div>
            ))}
          </div>

          <a
            href={SPEC_URL}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-block text-sm text-primary hover:underline pt-1"
          >
            {ko ? "개발자를 위한 계산·기록 규칙 보기 →" : "Technical recording and calculation rules →"}
          </a>
        </CardContent>
      </Card>

      {/* ── 리더보드 ────────────────────────────────────────────── */}
      {failed && (
        <Card className="border-border/50">
          <CardContent className="py-10 text-center text-muted-foreground text-sm">
            {ko ? "전략 순위표를 불러오지 못했습니다. 잠시 후 다시 시도해 주세요." : "The strategy ranking could not be loaded. Please try again shortly."}
          </CardContent>
        </Card>
      )}

      {!failed && !data && (
        <Card className="border-border/50">
          <CardContent className="py-10 text-center text-muted-foreground text-sm">
            {ko ? "불러오는 중..." : "Loading..."}
          </CardContent>
        </Card>
      )}

      {data && isEmpty && <PreparingNotice boards={boards} ko={ko} />}

      {data &&
        !isEmpty &&
        boards.map((board) => <BoardTable key={board.market} board={board} ko={ko} />)}

      {/* ── 참가 안내 ───────────────────────────────────────────── */}
      <StanceAgentConnectCard ko={ko} />

      <StanceRegistrationCard ko={ko} />

      {/* ── 채점 방식 ───────────────────────────────────────────── */}
      {data && (
        <Card className="border-border/50">
          <CardHeader>
            <CardTitle className="text-base">
              {ko ? "왜 종합점수 하나로 줄 세우지 않나요?" : "Why isn't there one overall score?"}
            </CardTitle>
          </CardHeader>
          <CardContent className="text-sm text-muted-foreground space-y-3 leading-relaxed">
            <p>
              {ko
                ? "수익률만 보면 큰 위험을 감수한 전략이 유리하고, 위험만 낮추면 현금을 오래 들고 있던 전략이 유리합니다. 어떤 종합점수도 한쪽을 편들게 됩니다."
                : "Return alone rewards risk-taking, while minimizing risk rewards strategies that sit in cash. Any combined score favors one style."}
            </p>
            <p>
              {ko ? (
                <>
                  그래서 <strong className="text-foreground">얼마나 벌었는지, 얼마나 크게 떨어졌는지, 실제로 얼마를 투자했는지</strong>를
                  함께 보여줍니다. 무엇을 중요하게 볼지는 직접 선택할 수 있습니다.
                </>
              ) : (
                <>
                  So we show several metrics side by side, always next to <strong className="text-foreground">average exposure</strong> —
                  a score only means something once you know how much was at risk.
                </>
              )}
            </p>
            <div className="grid gap-2 pt-1 sm:grid-cols-2 lg:grid-cols-3">
              {[
                [ko ? "누적 수익" : "Total return", ko ? "기록을 시작한 날부터 지금까지 번 비율" : "Gain or loss since the record began"],
                [ko ? "가장 큰 하락" : "Worst drop", ko ? "고점에서 가장 많이 떨어졌던 폭" : "The deepest fall from a previous high"],
                [ko ? "평균 투자 비율" : "Average invested", ko ? "전체 자산 중 실제 투자한 평균 비율" : "Average share of assets actually invested"],
                [ko ? "위험 대비 수익" : "Risk-adjusted return", ko ? "손실로 흔들린 정도에 비해 얼마나 벌었는지. 높을수록 좋음" : "Return compared with downside volatility; higher is better"],
                [ko ? "기록한 비율" : "Record rate", ko ? "정해둔 판단 시점에 빠짐없이 기록한 비율" : "How consistently scheduled decisions were recorded"],
                [ko ? "매매 빈도" : "Trading activity", ko ? "자산을 얼마나 자주 사고팔았는지" : "How frequently the portfolio was bought and sold"],
              ].map(([label, description]) => (
                <div key={label} className="rounded-md border border-border/50 bg-muted/20 p-3">
                  <div className="text-xs font-semibold text-foreground">{label}</div>
                  <div className="mt-1 text-xs">{description}</div>
                </div>
              ))}
            </div>
            <p className="text-xs font-mono pt-1 opacity-70">
              {ko ? "계산 규칙 버전" : "Calculation rules"}: {data.score_profile}
            </p>
          </CardContent>
        </Card>
      )}
    </div>
  )
}

function PreparingNotice({ boards, ko }: { boards: StanceBoard[]; ko: boolean }) {
  const board = boards[0]
  return (
    <Card className="border-border/50">
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          {ko ? "첫 전략을 기다리고 있어요" : "Waiting for the first strategy"}
          <Badge variant="outline" className="text-xs">
            {ko ? "현재 0개" : "0 so far"}
          </Badge>
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-5 text-sm">
        <p className="text-muted-foreground leading-relaxed">
          {ko
            ? "아직 참가한 전략이 없습니다. 등록하면 즉시 ‘기록 쌓는 중’ 목록에 나타나고, 첫 판단부터 성과가 공개됩니다. 충분한 기간과 거래 기록이 쌓이면 공식 순위에 들어갑니다."
            : "No strategy has joined yet. Registration puts it in the building-record list immediately, and performance appears from the first decision. It enters the official ranking after enough time and trades."}
        </p>

        {board && (
          <div className="rounded-lg border border-border/50 bg-muted/30 p-4 space-y-2 text-xs">
            <div className="font-semibold text-sm mb-2">
              {ko ? "이 시장에서는 이렇게 계산해요" : "How this market is measured"}
            </div>
            <Row label={ko ? "시장" : "Market"} value={`${board.market} (${board.currency})`} />
            <Row label={ko ? "가격 기준" : "Price source"} value={board.price_authority} />
            <Row label={ko ? "하루 성과를 확정하는 때" : "Daily cutoff"} value={board.mark_at} />
            <Row
              label={ko ? "공식 순위에 필요한 기록" : "Record needed for official rank"}
              value={ko ? `${board.min_track_periods}거래일 + 자산 1% 이상 거래 20번 완료` : `${board.min_track_periods} trading days + 20 closed trades using at least 1% of assets`}
            />
            {board.support !== "stable" && (
              <div className="pt-2 text-amber-600 dark:text-amber-500">
                ⚠️ {ko ? "시험 운영 중인 시장입니다" : "This market is in experimental support"}
              </div>
            )}
          </div>
        )}

        <div className="rounded-lg border border-primary/20 bg-primary/5 p-4">
          <div className="font-semibold mb-2">{ko ? "자동매매 전략이 있다면 바로 참여할 수 있어요" : "Already have an automated strategy? Join now"}</div>
          <p className="text-muted-foreground text-xs mb-2 leading-relaxed">
            {ko
              ? "위의 연결 지시문을 코딩 에이전트에 붙여넣으면 등록과 코드 연결을 도와줍니다. 실계좌는 없어도 됩니다."
              : "Paste the connection instruction above into a coding agent. It can handle registration and integration. No live brokerage account is required."}
          </p>
        </div>
      </CardContent>
    </Card>
  )
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between gap-4">
      <span className="text-muted-foreground shrink-0">{label}</span>
      <span className="text-right">{value}</span>
    </div>
  )
}

function BoardTable({ board, ko }: { board: StanceBoard; ko: boolean }) {
  const qualified = board.entries.filter((e) => e.qualified)
  const provisional = board.entries.filter((e) => !e.qualified)

  return (
    <Card className="border-border/50">
      <CardHeader>
        <CardTitle className="flex flex-wrap items-center gap-2 text-base">
          {board.market}
          <Badge variant="outline" className="text-xs">
            {board.currency}
          </Badge>
          {board.support !== "stable" && (
            <Badge variant="outline" className="text-xs text-amber-600 border-amber-500/50">
              {ko ? "시험 운영" : "experimental"}
            </Badge>
          )}
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-6">
        <EntryTable entries={qualified} ko={ko} />

        {provisional.length > 0 && (
          <div>
            <div className="text-sm font-semibold mb-2 text-muted-foreground">
              {ko ? "기록 쌓는 중" : "Building a record"}
            </div>
            <p className="text-xs text-muted-foreground mb-3 leading-relaxed">
              {ko
                ? "공식 순위에 필요한 기간과 거래 수를 채우기 전입니다. 이 단계에서도 모든 기록과 성과를 그대로 볼 수 있습니다."
                : "These strategies are still completing the time and trade requirements for official ranking. Their full records remain visible."}
            </p>
            <EntryTable entries={provisional} ko={ko} provisional />
          </div>
        )}
      </CardContent>
    </Card>
  )
}

function EntryTable({
  entries,
  ko,
  provisional = false,
}: {
  entries: StanceEntry[]
  ko: boolean
  provisional?: boolean
}) {
  if (entries.length === 0) {
    return (
      <div className="text-sm text-muted-foreground py-6 text-center">
        {ko ? "아직 공식 순위에 오른 전략이 없습니다." : "No strategy has reached the official ranking yet."}
      </div>
    )
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-border/50 text-xs text-muted-foreground">
            <th className="text-left font-medium py-2 pr-4">{ko ? "전략" : "Strategy"}</th>
            <th className="text-right font-medium py-2 px-3">{ko ? "누적 수익" : "Total return"}</th>
            <th className="text-right font-medium py-2 px-3">{ko ? "위험 대비 수익" : "Risk-adjusted"}</th>
            <th className="text-right font-medium py-2 px-3">{ko ? "가장 큰 하락" : "Worst drop"}</th>
            <th className="text-right font-medium py-2 px-3 text-primary">
              {ko ? "평균 투자 비율" : "Avg invested"}
            </th>
            <th className="text-right font-medium py-2 px-3">{ko ? "기록한 비율" : "Record rate"}</th>
            <th className="text-right font-medium py-2 px-3">{ko ? "매매 빈도" : "Trading activity"}</th>
            <th className="text-right font-medium py-2 pl-3">{ko ? "완료 거래" : "Closed trades"}</th>
          </tr>
        </thead>
        <tbody>
          {entries.map((e) => (
            <tr key={e.strategy} className="border-b border-border/30 last:border-0">
              <td className="py-3 pr-4">
                <div className="font-medium">{e.display_name}</div>
                <div className="text-xs text-muted-foreground">
                  <span className="font-mono">{e.handle}</span>
                  {e.owner_name && <span> · {e.owner_name}</span>}
                </div>
                {e.tagline && <div className="mt-1 max-w-sm text-xs text-muted-foreground">{e.tagline}</div>}
                {(e.description || e.website_url || e.source_url) && (
                  <details className="mt-1.5 max-w-sm text-xs">
                    <summary className="cursor-pointer text-primary hover:underline">
                      {ko ? "전략 소개" : "Strategy profile"}
                    </summary>
                    <div className="mt-2 space-y-2 rounded-md border border-border/50 bg-muted/20 p-3 text-muted-foreground">
                      {e.description && <p className="whitespace-pre-wrap leading-relaxed">{e.description}</p>}
                      <div className="flex flex-wrap gap-3">
                        {e.website_url && (
                          <a href={e.website_url} target="_blank" rel="nofollow ugc noopener noreferrer" className="inline-flex items-center gap-1 text-primary hover:underline">
                            {ko ? "대표 링크" : "Website"}<ExternalLink className="h-3 w-3" />
                          </a>
                        )}
                        {e.source_url && (
                          <a href={e.source_url} target="_blank" rel="nofollow ugc noopener noreferrer" className="inline-flex items-center gap-1 text-primary hover:underline">
                            {ko ? "소스" : "Source"}<Github className="h-3 w-3" />
                          </a>
                        )}
                      </div>
                      <p className="text-[10px] opacity-70">{ko ? "참여자가 직접 작성한 공개 정보" : "Public information supplied by the participant"}</p>
                    </div>
                  </details>
                )}
                {provisional && e.gate_failures.length > 0 && (
                  <div className="text-[11px] text-amber-600 dark:text-amber-500 mt-1">
                    {e.gate_failures.map((failure) => plainGateFailure(failure, ko)).join(" · ")}
                  </div>
                )}
              </td>
              <td
                className={`text-right px-3 tabular-nums ${
                  e.metrics.cumulative_return >= 0 ? "text-emerald-600" : "text-rose-600"
                }`}
              >
                {pct(e.metrics.cumulative_return)}
              </td>
              <td className="text-right px-3 tabular-nums">{e.metrics.sortino.toFixed(2)}</td>
              <td className="text-right px-3 tabular-nums text-muted-foreground">
                {(e.metrics.max_drawdown * 100).toFixed(1)}%
              </td>
              <td className="text-right px-3 tabular-nums font-medium">
                {(e.metrics.avg_exposure * 100).toFixed(0)}%
              </td>
              <td className="text-right px-3 tabular-nums text-muted-foreground">
                {(e.metrics.coverage * 100).toFixed(0)}%
              </td>
              <td className="text-right px-3 tabular-nums text-muted-foreground">
                {e.metrics.turnover.toFixed(1)}
              </td>
              <td className="text-right pl-3 tabular-nums text-muted-foreground">
                {e.metrics.closed_trades}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
