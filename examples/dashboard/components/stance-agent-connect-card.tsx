"use client"

import { useEffect, useMemo, useState } from "react"
import { Bot, Check, Copy, ExternalLink, FolderCode, MessageSquareCode, ShieldCheck, Trophy, WandSparkles } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"

const DISCOVERY_PATH = "/.well-known/stance.json"

function agentPrompt(ko: boolean, origin: string) {
  const discovery = `${origin}${DISCOVERY_PATH}`
  if (ko) {
    return `지금 열려 있는 이 프로젝트를 Stance에 자동 연결해줘. 이 지시문은 저장소 파일과 터미널에 접근할 수 있는 코딩 에이전트용이야.

먼저 머신용 명세 ${discovery} 를 읽고, 현재 저장소와 실행 환경을 분석해.

1. 저장소, 설정, 실행 진입점, 스케줄, 포트폴리오 경계를 조사해 독립 전략을 모두 식별해. 별도 포트폴리오나 의사결정 파이프라인이면 별도 전략으로 취급하되, 하나의 포트폴리오가 여러 거래소를 다룬다는 이유만으로 쪼개지는 마.
2. 각 전략의 ID, 표시 이름, 공개 핸들, 시장, 판단 주기와 선택 프로필(운영자·팀 이름, 한 줄 소개, 상세 소개, 대표 링크, 소스 링크)을 제안해. 코드에서 확정할 수 없는 공개 정보만 사용자에게 짧게 질문해.
3. 실제 등록 전에 감지 근거와 전략별 등록 계획을 표로 보여줘. 한 전략이 Stance의 여러 시장 코드에 걸치면 임의로 고르거나 쪼개지 말고 지원 제약으로 명시해. 등록은 되돌릴 수 없으므로 사용자가 이 계획을 명시적으로 승인하기 전에는 POST /strategies를 호출하지 마.
4. 승인 후 각 전략을 따로 등록해. 각 전략마다 별도 API 키, seq, writer, 포트폴리오 복구 상태를 사용하고 시장 간에 공유하지 마.
5. 발급된 API 키는 채팅·로그·명령 출력·커밋·패치에 절대 노출하지 마. 저장소 밖 비밀 환경 파일(권한 600)이나 기존 secret manager에 즉시 저장하고 Git 추적 제외를 확인해.
6. 과거 거래는 이관하거나 소급 입력하지 마. 기존 포지션이 있다면 연결 시점의 현재 목표 비중만 초기 선언하고, 지금부터 발생하는 판단을 기록해.
7. 실제 주문과 Stance 보고를 분리해. Stance 장애가 주문을 막거나 지연시키지 않도록 fail-open으로 연결해.
8. 시작할 때 각 전략의 GET /portfolio last_seq + 1을 사용하고, 타임아웃에는 같은 seq와 같은 본문을 그대로 재전송해.
9. 기존 프로젝트의 패턴과 유틸을 재사용하고 새 의존성은 추가하지 마. 관련 테스트를 추가하고 실행해.
10. health, 인증된 portfolio 조회, 연동 테스트를 검증한 뒤 결과만 요약해. API 키 값은 어떤 결과에도 쓰지 마.

조사와 계획 작성은 묻지 말고 진행해. 필요한 공개 정보와 최종 등록 승인만 질문해.`
  }
  return `Connect the currently open project to Stance automatically. This instruction is for a coding agent with repository and terminal access.

First read the machine-readable contract at ${discovery}, then inspect the repository and runtime.

1. Inspect the repository, configuration, entry points, schedules, and portfolio boundaries to identify all multiple independent strategies. Separate distinct portfolios or decision pipelines, but do not split one portfolio merely because it trades on multiple exchanges.
2. Propose each strategy's ID, display name, public handle, market, cadence, and optional profile (operator or team, tagline, description, website, and source URL). Ask the user only for public information that cannot be established from the project.
3. Before any registration, show the evidence and a per-strategy registration plan. If one strategy spans multiple Stance market codes, report that support constraint instead of guessing or splitting it. Registration is irreversible: do not call POST /strategies until the user explicitly approves that plan.
4. After approval, register every strategy separately. Give each strategy its own API key, seq, writer, and portfolio recovery state; never share them across markets.
5. Never expose the issued API key in chat, logs, command output, commits, patches, or screenshots. Store it immediately outside the repository in a mode-0600 secret file or the existing secret manager, and verify it is not tracked by Git.
6. Do not migrate or backfill historical trades. If positions already exist, declare only their current target weights from the connection time onward, then record new decisions.
7. Keep live order execution separate from Stance reporting. Stance failures must not block or delay orders; integrate fail-open.
8. Recover each strategy with GET /portfolio last_seq + 1. On timeout, retry the identical seq and body.
9. Reuse existing project patterns and utilities. Add no dependency. Add and run relevant tests.
10. Verify health, authenticated portfolio recovery, and integration tests, then report only the results. Never include an API key value.

Proceed with inspection and planning without asking. Ask only for missing public profile information and final registration approval.`
}

export function StanceAgentConnectCard({ ko }: { ko: boolean }) {
  const [origin, setOrigin] = useState("https://analysis.stocksimulation.kr")
  const [copied, setCopied] = useState(false)

  useEffect(() => setOrigin(window.location.origin), [])
  const prompt = useMemo(() => agentPrompt(ko, origin), [ko, origin])

  async function copyPrompt() {
    await navigator.clipboard.writeText(prompt)
    setCopied(true)
    window.setTimeout(() => setCopied(false), 2_000)
  }

  return (
    <Card className="overflow-hidden border-violet-500/40 bg-gradient-to-br from-violet-500/[0.08] via-background to-cyan-500/[0.06] shadow-sm">
      <CardHeader className="pb-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="space-y-2">
            <div className="flex flex-wrap items-center gap-2">
              <Badge className="border-0 bg-violet-600 text-white hover:bg-violet-600">
                <WandSparkles className="mr-1 h-3 w-3" />
                {ko ? "추천" : "Recommended"}
              </Badge>
              <Badge variant="outline" className="border-violet-500/30 text-violet-700 dark:text-violet-300">
                {ko ? "등록 즉시" : "Instant registration"}
              </Badge>
            </div>
            <CardTitle className="flex items-center gap-2 text-xl">
              <Bot className="h-6 w-6 text-violet-600" />
              {ko ? "코딩 에이전트로 자동 연결" : "Connect with a coding agent"}
            </CardTitle>
            <p className="max-w-2xl text-sm leading-relaxed text-muted-foreground">
              {ko
                ? "프로젝트 코드를 읽고 수정할 수 있는 AI에게 아래 지시문을 건네면, 등록부터 연동 테스트까지 처리합니다."
                : "Give the instruction below to an AI that can read and edit your project. It handles registration through integration testing."}
            </p>
          </div>
          <Button onClick={copyPrompt} size="lg" className="min-w-48 bg-violet-600 text-white hover:bg-violet-700">
            {copied ? <Check className="mr-2 h-4 w-4" /> : <Copy className="mr-2 h-4 w-4" />}
            {copied
              ? (ko ? "복사 완료" : "Copied")
              : (ko ? "연결 지시문 복사" : "Copy connection instruction")}
          </Button>
        </div>
      </CardHeader>

      <CardContent className="space-y-5">
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {[
            [FolderCode, ko ? "1. 전략 프로젝트 열기" : "1. Open your strategy project", ko ? "Codex CLI · Claude Code · Cursor Agent 등" : "Codex CLI · Claude Code · Cursor Agent, etc."],
            [MessageSquareCode, ko ? "2. 에이전트 채팅에 붙여넣기" : "2. Paste into the agent chat", ko ? "일반 채팅이 아닌 파일·터미널 접근 모드" : "Use a mode with file and terminal access"],
            [Bot, ko ? "3. 등록 계획 승인" : "3. Approve the plan", ko ? "감지된 전략 · 공개 프로필 · 시장 확인" : "Review strategies · profiles · markets"],
            [ShieldCheck, ko ? "4. 완료 보고 확인" : "4. Review completion", ko ? "전략별 등록 · 키 보관 · 코드 연동 · 테스트" : "Per-strategy registration · secrets · code · tests"],
          ].map(([Icon, title, description]) => (
            <div key={String(title)} className="rounded-xl border border-border/60 bg-background/70 p-4 backdrop-blur-sm">
              <Icon className="mb-3 h-5 w-5 text-violet-600" />
              <div className="text-sm font-semibold">{String(title)}</div>
              <div className="mt-1 text-xs leading-relaxed text-muted-foreground">{String(description)}</div>
            </div>
          ))}
        </div>

        <div className="grid overflow-hidden rounded-xl border border-border/70 bg-background/70 sm:grid-cols-2">
          <div className="flex gap-3 p-4 sm:border-r sm:border-border/70">
            <Bot className="mt-0.5 h-5 w-5 shrink-0 text-emerald-500" />
            <div>
              <div className="text-sm font-semibold">
                {ko ? "등록 직후" : "Immediately after registration"}
              </div>
              <div className="mt-1 text-xs leading-relaxed text-muted-foreground">
                {ko ? "예선 명단에 바로 표시되고, 그 시점부터 판단 기록이 쌓입니다." : "Your strategy appears in the provisional field immediately, and decisions start accumulating from that point."}
              </div>
            </div>
          </div>
          <div className="flex gap-3 border-t border-border/70 p-4 sm:border-t-0">
            <Trophy className="mt-0.5 h-5 w-5 shrink-0 text-amber-500" />
            <div>
              <div className="text-sm font-semibold">
                {ko ? "공식 순위 자격" : "Official ranking eligibility"}
              </div>
              <div className="mt-1 text-xs leading-relaxed text-muted-foreground">
                {ko ? "주식 기준 63거래일과 자산의 1% 이상을 투자했던 포지션 청산 20건을 모두 채우면 예선을 통과합니다. 그전에도 기록과 성과는 보입니다." : "For stocks, qualify after both 63 trading days and 20 closed positions that each carried at least 1% of assets. Records and performance remain visible before then."}
              </div>
            </div>
          </div>
        </div>

        <div className="flex flex-wrap items-center justify-between gap-3 border-t border-border/50 pt-4 text-xs text-muted-foreground">
          <span>{ko ? "과거 거래 소급 없음 · 연결 시점부터 기록" : "No backfill · records begin at connection time"}</span>
          <a
            href={DISCOVERY_PATH}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1 font-medium text-violet-700 hover:underline dark:text-violet-300"
          >
            {ko ? "AI용 연결 명세 보기" : "View agent discovery contract"}
            <ExternalLink className="h-3 w-3" />
          </a>
        </div>
      </CardContent>
    </Card>
  )
}
