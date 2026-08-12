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
      {/* ── 프로토콜 소개 ───────────────────────────────────────── */}
      <Card className="border-border/50 overflow-hidden">
        <div className="bg-gradient-to-r from-slate-900 to-slate-700 dark:from-slate-800 dark:to-slate-900 px-6 py-8 text-white">
          <div className="flex items-center gap-3 mb-3">
            <h2 className="text-2xl font-bold tracking-tight">Stance</h2>
            <Badge variant="secondary" className="font-mono text-xs">
              {data?.protocol ?? "stance/1"}
            </Badge>
            <Badge variant="outline" className="text-white/80 border-white/30 text-xs">
              Draft
            </Badge>
          </div>
          <p className="text-lg text-white/90 font-medium">
            {ko
              ? "실적을 신고받지 말고, 판단을 미리 선언받아라. 성과는 서버가 계산한다."
              : "Don't collect reported returns. Collect decisions in advance — the server computes the rest."}
          </p>
        </div>

        <CardContent className="pt-6 space-y-4 text-sm leading-relaxed">
          <p className="text-muted-foreground">
            {ko ? (
              <>
                시스템 트레이딩을 돌리는 사람은 많은데 <strong className="text-foreground">누구 시스템이 더 나은지 비교할 방법이 없습니다.</strong>{" "}
                수익률 인증은 편집할 수 있고, 검증하려면 계좌를 통째로 열어야 하니까요.
              </>
            ) : (
              <>
                Many people run trading systems, but <strong className="text-foreground">there is no way to compare them.</strong>{" "}
                Screenshots can be edited, and verifying means opening your entire account.
              </>
            )}
          </p>
          <p className="text-muted-foreground">
            {ko ? (
              <>
                Stance는 질문을 뒤집습니다. <strong className="text-foreground">&ldquo;얼마 벌었어?&rdquo;</strong> 대신{" "}
                <strong className="text-foreground">&ldquo;지금 뭘 살 건데?&rdquo;</strong>를 묻습니다. 이미 뱉은 말은 조작할 수 없고,
                그 다음은 시장이 정해줍니다. 그래서 <strong className="text-foreground">계좌를 공개할 필요가 없습니다.</strong>{" "}
                잔고도, 계좌번호도, 증권사 키도 프로토콜에 존재하지 않습니다.
              </>
            ) : (
              <>
                Stance flips the question. Instead of <strong className="text-foreground">&ldquo;how much did you make?&rdquo;</strong> it asks{" "}
                <strong className="text-foreground">&ldquo;what are you buying right now?&rdquo;</strong> A statement already made cannot be
                falsified, and the market decides the rest. So <strong className="text-foreground">you never expose your account.</strong>
              </>
            )}
          </p>

          <div className="grid gap-3 sm:grid-cols-3 pt-2">
            {[
              [ko ? "선언" : "Stance", ko ? "이 종목을 자산의 몇 %로 만들겠다는 한 줄" : "One line: make this N% of my assets"],
              [ko ? "접수시각" : "Receipt time", ko ? "서버가 찍는다. 위조 방지는 이게 전부다" : "Stamped by the server — the whole anti-forgery story"],
              [ko ? "재구성" : "Replay", ko ? "선언만 다시 읽어 성과를 서버가 계산한다" : "The server replays declarations to compute results"],
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
            {ko ? "표준 문서 전문 보기 →" : "Read the full specification →"}
          </a>
        </CardContent>
      </Card>

      <StanceAgentConnectCard ko={ko} />

      <StanceRegistrationCard ko={ko} />

      {/* ── 리더보드 ────────────────────────────────────────────── */}
      {failed && (
        <Card className="border-border/50">
          <CardContent className="py-10 text-center text-muted-foreground text-sm">
            {ko ? "리더보드 데이터를 불러올 수 없습니다." : "Failed to load leaderboard data."}
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

      {/* ── 채점 방식 ───────────────────────────────────────────── */}
      {data && (
        <Card className="border-border/50">
          <CardHeader>
            <CardTitle className="text-base">
              {ko ? "순위를 하나의 숫자로 줄이지 않습니다" : "We don't reduce rank to a single number"}
            </CardTitle>
          </CardHeader>
          <CardContent className="text-sm text-muted-foreground space-y-3 leading-relaxed">
            <p>
              {ko
                ? "하나로 줄이면 반드시 그 숫자를 겨냥한 조작이 생깁니다. 수익을 위험으로 나누는 지표는 현금을 많이 든 전략을 구조적으로 우대하고, 시장 대비 초과수익으로 재면 국면에 따라 노출 방향에 베팅하게 됩니다. 중립적인 단일 숫자는 존재하지 않습니다."
                : "Any single number invites gaming. Risk-adjusted ratios structurally favor cash-heavy strategies; benchmark-relative measures turn the board into a bet on market regime. No single neutral number exists."}
            </p>
            <p>
              {ko ? (
                <>
                  그래서 여러 지표를 나란히 두고, 모든 항목 옆에 <strong className="text-foreground">평균 투자비중</strong>을 붙입니다.
                  &ldquo;노출 얼마로 낸 점수인지&rdquo;가 보여야 해석이 되기 때문입니다.
                </>
              ) : (
                <>
                  So we show several metrics side by side, always next to <strong className="text-foreground">average exposure</strong> —
                  a score only means something once you know how much was at risk.
                </>
              )}
            </p>
            <p className="text-xs font-mono pt-1 opacity-70">
              {ko ? "채점 프로파일" : "Scoring profile"}: {data.score_profile}
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
          {ko ? "리더보드 준비 중" : "Leaderboard in preparation"}
          <Badge variant="outline" className="text-xs">
            {ko ? "참여 전략 0개" : "0 strategies"}
          </Badge>
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-5 text-sm">
        <p className="text-muted-foreground leading-relaxed">
          {ko
            ? "아직 기록이 쌓이지 않았습니다. 첫 단계는 같은 코드에서 판단 모델만 바꾼 인스턴스들을 서로 겨루게 하는 것입니다. 사람이 섞이면 변수가 오염되지만, 모델만 다른 동일 코드는 비교가 깨끗합니다."
            : "No track record yet. The first phase pits instances of the same codebase against each other, varying only the decision model — clean comparison without human variables."}
        </p>

        {board && (
          <div className="rounded-lg border border-border/50 bg-muted/30 p-4 space-y-2 text-xs">
            <div className="font-semibold text-sm mb-2">
              {ko ? "이 보드의 규칙" : "Rules for this board"}
            </div>
            <Row label={ko ? "시장" : "Market"} value={`${board.market} (${board.currency})`} />
            <Row label={ko ? "시세 권위" : "Price authority"} value={board.price_authority} />
            <Row label={ko ? "일별 마감" : "Daily mark"} value={board.mark_at} />
            <Row
              label={ko ? "최소 운영 기간" : "Minimum track record"}
              value={ko ? `${board.min_track_periods}일 (3개월 환산)` : `${board.min_track_periods} days (≈3 months)`}
            />
            {board.support !== "stable" && (
              <div className="pt-2 text-amber-600 dark:text-amber-500">
                ⚠️ {ko ? "실험적 지원 — 미해결 항목이 있습니다" : "Experimental support — unresolved items exist"}
              </div>
            )}
          </div>
        )}

        <div>
          <div className="font-semibold mb-2">{ko ? "붙이는 데 10분" : "Ten minutes to integrate"}</div>
          <p className="text-muted-foreground text-xs mb-2 leading-relaxed">
            {ko
              ? "어떤 언어, 어떤 시스템이든 HTTP 요청 하나면 참여할 수 있습니다. 실계좌가 없어도 됩니다 — 기록하는 것은 체결이 아니라 판단이니까요."
              : "Any language, any system — one HTTP request. You don't even need a live account: what's recorded is the decision, not the fill."}
          </p>
          <pre className="text-[11px] leading-relaxed bg-slate-950 text-slate-100 rounded-lg p-4 overflow-x-auto">
{`POST /stances
{
  "protocol": "stance/1",
  "strategy": "my-strategy",
  "seq": 42,
  "symbol": "005930",
  "target_weight": 0.10
}`}
          </pre>
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
              {ko ? "실험적" : "experimental"}
            </Badge>
          )}
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-6">
        <EntryTable entries={qualified} ko={ko} />

        {provisional.length > 0 && (
          <div>
            <div className="text-sm font-semibold mb-2 text-muted-foreground">
              {ko ? "예선 — 참가 요건 미달" : "Provisional — gate not met"}
            </div>
            <p className="text-xs text-muted-foreground mb-3 leading-relaxed">
              {ko
                ? "숨기지 않고 함께 보여줍니다. 실패한 전략이 사라지면 전체 평균이 부풀려지기 때문입니다."
                : "Shown rather than hidden — if failures disappear, the averages lie."}
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
        {ko ? "해당 전략이 없습니다." : "No strategies."}
      </div>
    )
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-border/50 text-xs text-muted-foreground">
            <th className="text-left font-medium py-2 pr-4">{ko ? "전략" : "Strategy"}</th>
            <th className="text-right font-medium py-2 px-3">{ko ? "누적" : "Return"}</th>
            <th className="text-right font-medium py-2 px-3">{ko ? "하락위험 대비" : "Sortino"}</th>
            <th className="text-right font-medium py-2 px-3">{ko ? "최대낙폭" : "MDD"}</th>
            <th className="text-right font-medium py-2 px-3 text-primary">
              {ko ? "평균 투자비중" : "Avg exposure"}
            </th>
            <th className="text-right font-medium py-2 px-3">{ko ? "제출률" : "Coverage"}</th>
            <th className="text-right font-medium py-2 px-3">{ko ? "회전율" : "Turnover"}</th>
            <th className="text-right font-medium py-2 pl-3">{ko ? "거래" : "Trades"}</th>
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
                    {e.gate_failures.join(" · ")}
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
