"use client"

import { useEffect, useMemo, useState } from "react"
import { Bot, Check, Copy, ExternalLink, KeyRound, ShieldCheck, WandSparkles } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"

const DISCOVERY_PATH = "/.well-known/stance.json"

function agentPrompt(ko: boolean, origin: string) {
  const discovery = `${origin}${DISCOVERY_PATH}`
  if (ko) {
    return `이 프로젝트를 Stance에 자동 연결해줘.

먼저 머신용 명세 ${discovery} 를 읽고, 현재 저장소와 실행 환경을 분석해.

1. 전략 ID, 표시 이름, 공개 핸들, 시장, 판단 주기를 제안하고 공개 API로 전략을 등록해.
2. 발급된 API 키는 채팅·로그·명령 출력·커밋·패치에 절대 노출하지 마. 저장소 밖 비밀 환경 파일(권한 600)이나 기존 secret manager에 즉시 저장하고 Git 추적 제외를 확인해.
3. 과거 거래는 이관하거나 소급 입력하지 마. 기존 포지션이 있다면 연결 시점의 현재 목표 비중만 초기 선언하고, 지금부터 발생하는 판단을 기록해.
4. 실제 주문과 Stance 보고를 분리해. Stance 장애가 주문을 막거나 지연시키지 않도록 fail-open으로 연결해.
5. 시작할 때 GET /portfolio의 last_seq + 1을 사용하고, 타임아웃에는 같은 seq와 같은 본문을 그대로 재전송해. writer는 전략당 하나만 둬.
6. 기존 프로젝트의 패턴과 유틸을 재사용하고 새 의존성은 추가하지 마. 관련 테스트를 추가하고 실행해.
7. health, 인증된 portfolio 조회, 연동 테스트를 검증한 뒤 결과만 요약해. API 키 값은 어떤 결과에도 쓰지 마.

명확하고 되돌릴 수 있는 단계는 묻지 말고 진행하되, 전략의 공개 신원이나 시장을 확정할 근거가 없을 때만 질문해.`
  }
  return `Connect this project to Stance automatically.

First read the machine-readable contract at ${discovery}, then inspect the repository and runtime.

1. Propose a strategy ID, display name, public handle, market, and cadence, then register through the public API.
2. Never expose the issued API key in chat, logs, command output, commits, patches, or screenshots. Store it immediately outside the repository in a mode-0600 secret file or the existing secret manager, and verify it is not tracked by Git.
3. Do not migrate or backfill historical trades. If positions already exist, declare only their current target weights from the connection time onward, then record new decisions.
4. Keep live order execution separate from Stance reporting. Stance failures must not block or delay orders; integrate fail-open.
5. Recover with GET /portfolio last_seq + 1. On timeout, retry the identical seq and body. Use one writer per strategy.
6. Reuse existing project patterns and utilities. Add no dependency. Add and run relevant tests.
7. Verify health, authenticated portfolio recovery, and integration tests, then report only the results. Never include the API key value.

Proceed without asking for clear, reversible steps. Ask only if the public identity or market cannot be determined safely.`
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
                {ko ? "약 1분" : "≈ 1 min"}
              </Badge>
            </div>
            <CardTitle className="flex items-center gap-2 text-xl">
              <Bot className="h-6 w-6 text-violet-600" />
              {ko ? "AI 에이전트에게 연결을 맡기세요" : "Let your AI agent connect it"}
            </CardTitle>
            <p className="max-w-2xl text-sm leading-relaxed text-muted-foreground">
              {ko
                ? "프롬프트 하나로 등록, 키 보관, 코드 연동, 테스트까지 끝냅니다. 계좌나 증권사 키는 Stance에 보내지 않습니다."
                : "One prompt handles registration, secret storage, integration, and tests. Stance never receives your account or broker keys."}
            </p>
          </div>
          <Button onClick={copyPrompt} size="lg" className="min-w-48 bg-violet-600 text-white hover:bg-violet-700">
            {copied ? <Check className="mr-2 h-4 w-4" /> : <Copy className="mr-2 h-4 w-4" />}
            {copied
              ? (ko ? "복사 완료" : "Copied")
              : (ko ? "AI 연결 프롬프트 복사" : "Copy agent prompt")}
          </Button>
        </div>
      </CardHeader>

      <CardContent className="space-y-5">
        <div className="grid gap-3 sm:grid-cols-3">
          {[
            [Bot, ko ? "1. 붙여넣기" : "1. Paste", ko ? "Codex, Claude Code, Cursor 등" : "Codex, Claude Code, Cursor, and more"],
            [KeyRound, ko ? "2. 자동 연결" : "2. Auto-connect", ko ? "등록 · 비밀 저장 · 코드 적용" : "Register · secure · integrate"],
            [ShieldCheck, ko ? "3. 검증" : "3. Verify", ko ? "키 노출 없이 테스트 결과만" : "Results only, never the key"],
          ].map(([Icon, title, description]) => (
            <div key={String(title)} className="rounded-xl border border-border/60 bg-background/70 p-4 backdrop-blur-sm">
              <Icon className="mb-3 h-5 w-5 text-violet-600" />
              <div className="text-sm font-semibold">{String(title)}</div>
              <div className="mt-1 text-xs leading-relaxed text-muted-foreground">{String(description)}</div>
            </div>
          ))}
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
