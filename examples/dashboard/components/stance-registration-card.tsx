"use client"

import { FormEvent, useEffect, useState } from "react"
import { Check, Copy, KeyRound, Loader2 } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Textarea } from "@/components/ui/textarea"

type Registration = {
  strategy: string
  market: string
  cadence: string
  api_key: string
  notice: string
}

const API_BASE = "/api/stance/v1"

export function StanceRegistrationCard({ ko }: { ko: boolean }) {
  const [strategy, setStrategy] = useState("")
  const [displayName, setDisplayName] = useState("")
  const [handle, setHandle] = useState("")
  const [ownerName, setOwnerName] = useState("")
  const [tagline, setTagline] = useState("")
  const [description, setDescription] = useState("")
  const [websiteUrl, setWebsiteUrl] = useState("")
  const [sourceUrl, setSourceUrl] = useState("")
  const [market, setMarket] = useState("KRX")
  const [cadence, setCadence] = useState("daily")
  const [registration, setRegistration] = useState<Registration | null>(null)
  const [error, setError] = useState("")
  const [submitting, setSubmitting] = useState(false)
  const [copied, setCopied] = useState(false)
  const [publicApiBase, setPublicApiBase] = useState(API_BASE)

  useEffect(() => {
    setPublicApiBase(`${window.location.origin}${API_BASE}`)
  }, [])

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setSubmitting(true)
    setError("")
    try {
      const response = await fetch(`${API_BASE}/strategies`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          strategy,
          display_name: displayName,
          handle,
          market,
          cadence,
          owner_name: ownerName || null,
          tagline: tagline || null,
          description: description || null,
          website_url: websiteUrl || null,
          source_url: sourceUrl || null,
        }),
      })
      const body = await response.json().catch(() => ({}))
      if (!response.ok) {
        throw new Error(body.error ?? body.detail ?? `HTTP ${response.status}`)
      }
      setRegistration(body)
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : (ko ? "등록에 실패했습니다" : "Registration failed"))
    } finally {
      setSubmitting(false)
    }
  }

  async function copyKey() {
    if (!registration) return
    await navigator.clipboard.writeText(registration.api_key)
    setCopied(true)
    window.setTimeout(() => setCopied(false), 2_000)
  }

  if (registration) {
    return (
      <Card className="border-emerald-500/40 overflow-hidden">
        <CardHeader className="bg-emerald-500/10">
          <CardTitle className="flex items-center gap-2 text-base">
            <KeyRound className="h-5 w-5 text-emerald-600" />
            {ko ? "연동 키 발급 완료" : "Integration key issued"}
            <Badge variant="outline" className="ml-auto font-mono">{registration.strategy}</Badge>
          </CardTitle>
        </CardHeader>
        <CardContent className="pt-6 space-y-5">
          <div>
            <Label>{ko ? "API 키 · 지금 한 번만 표시" : "API key · shown once"}</Label>
            <div className="mt-2 flex gap-2">
              <code className="min-w-0 flex-1 overflow-x-auto rounded-md border bg-slate-950 px-3 py-2.5 text-sm text-emerald-300">
                {registration.api_key}
              </code>
              <Button type="button" variant="outline" onClick={copyKey} aria-label={ko ? "API 키 복사" : "Copy API key"}>
                {copied ? <Check className="h-4 w-4" /> : <Copy className="h-4 w-4" />}
              </Button>
            </div>
            <p className="mt-2 text-xs font-medium text-amber-600 dark:text-amber-400">
              {ko
                ? "페이지를 닫으면 다시 볼 수 없습니다. 지금 비밀 저장소에 보관하세요."
                : "You cannot reveal this key again. Store it in your secret manager now."}
            </p>
          </div>

          <div>
            <Label>{ko ? "공개 API 주소" : "Public API endpoint"}</Label>
            <code className="mt-2 block overflow-x-auto rounded-md bg-muted px-3 py-2.5 text-sm">{publicApiBase}</code>
          </div>

          <pre className="overflow-x-auto rounded-lg bg-slate-950 p-4 text-[11px] leading-relaxed text-slate-100">
{`curl -X POST ${publicApiBase}/stances \\
  -H "Authorization: Bearer ${registration.api_key}" \\
  -H "Content-Type: application/json" \\
  -d '{"protocol":"stance/1","seq":1,"kind":"hold","reason":"no signal"}'`}
          </pre>
        </CardContent>
      </Card>
    )
  }

  return (
    <Card className="border-primary/30">
      <CardHeader>
        <CardTitle className="flex flex-wrap items-center gap-2 text-base">
          {ko ? "직접 등록" : "Manual registration"}
          <Badge variant="secondary">{ko ? "대안" : "Alternative"}</Badge>
        </CardTitle>
        <p className="text-sm text-muted-foreground">
          {ko
            ? "AI 에이전트를 쓰지 않는다면 이름과 시장을 직접 등록할 수 있습니다. 연동 키는 등록 직후 한 번만 표시됩니다."
            : "If you are not using an AI agent, register the name and market manually. Your integration key appears once."}
        </p>
      </CardHeader>
      <CardContent>
        <form onSubmit={submit} className="grid gap-4 sm:grid-cols-2">
          <div className="space-y-2">
            <Label htmlFor="stance-strategy">{ko ? "전략 ID" : "Strategy ID"}</Label>
            <Input
              id="stance-strategy"
              value={strategy}
              onChange={(event) => setStrategy(event.target.value)}
              placeholder="my-strategy"
              pattern="[A-Za-z0-9][A-Za-z0-9._-]*"
              maxLength={64}
              required
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="stance-name">{ko ? "표시 이름" : "Display name"}</Label>
            <Input
              id="stance-name"
              value={displayName}
              onChange={(event) => setDisplayName(event.target.value)}
              placeholder={ko ? "돌파 추세 전략" : "Breakout Trend"}
              maxLength={100}
              required
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="stance-handle">{ko ? "공개 핸들" : "Public handle"}</Label>
            <Input
              id="stance-handle"
              value={handle}
              onChange={(event) => setHandle(event.target.value)}
              placeholder="@myhandle"
              maxLength={100}
              required
            />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-2">
              <Label>{ko ? "시장" : "Market"}</Label>
              <Select value={market} onValueChange={setMarket}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="KRX">KRX</SelectItem>
                  <SelectItem value="US">US</SelectItem>
                  <SelectItem value="CRYPTO">Crypto</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-2">
              <Label>{ko ? "판단 주기" : "Cadence"}</Label>
              <Select value={cadence} onValueChange={setCadence}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="daily">{ko ? "매일" : "Daily"}</SelectItem>
                  <SelectItem value="weekly">{ko ? "매주" : "Weekly"}</SelectItem>
                  <SelectItem value="monthly">{ko ? "매월" : "Monthly"}</SelectItem>
                  <SelectItem value="event">{ko ? "신호 발생 시" : "On signal"}</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>

          <details className="sm:col-span-2 rounded-lg border border-border/60 bg-muted/20">
            <summary className="cursor-pointer px-4 py-3 text-sm font-medium">
              {ko ? "선택 · 전략 소개와 링크" : "Optional · Strategy profile and links"}
            </summary>
            <div className="grid gap-4 border-t border-border/60 p-4 sm:grid-cols-2">
              <div className="space-y-2">
                <Label htmlFor="stance-owner">{ko ? "운영자·팀 이름" : "Operator or team"}</Label>
                <Input id="stance-owner" value={ownerName} onChange={(event) => setOwnerName(event.target.value)} maxLength={100} />
              </div>
              <div className="space-y-2">
                <Label htmlFor="stance-tagline">{ko ? "한 줄 소개" : "Tagline"}</Label>
                <Input id="stance-tagline" value={tagline} onChange={(event) => setTagline(event.target.value)} maxLength={120} />
              </div>
              <div className="space-y-2 sm:col-span-2">
                <Label htmlFor="stance-description">{ko ? "상세 소개" : "Description"}</Label>
                <Textarea id="stance-description" value={description} onChange={(event) => setDescription(event.target.value)} maxLength={500} rows={3} />
              </div>
              <div className="space-y-2">
                <Label htmlFor="stance-website">{ko ? "대표 링크" : "Website"}</Label>
                <Input id="stance-website" type="url" inputMode="url" value={websiteUrl} onChange={(event) => setWebsiteUrl(event.target.value)} placeholder="https://" maxLength={500} />
              </div>
              <div className="space-y-2">
                <Label htmlFor="stance-source">{ko ? "소스 저장소" : "Source repository"}</Label>
                <Input id="stance-source" type="url" inputMode="url" value={sourceUrl} onChange={(event) => setSourceUrl(event.target.value)} placeholder="https://github.com/..." maxLength={500} />
              </div>
              <p className="text-xs text-muted-foreground sm:col-span-2">
                {ko ? "모두 선택사항이며 점수와 순위에는 영향을 주지 않습니다. 공개 링크는 HTTPS만 허용합니다." : "All fields are optional and never affect scoring or rank. Public links must use HTTPS."}
              </p>
            </div>
          </details>

          {error && (
            <div className="sm:col-span-2 rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive">
              {error}
            </div>
          )}

          <div className="sm:col-span-2 flex flex-wrap items-center justify-between gap-3 pt-1">
            <p className="text-xs text-muted-foreground">
              {ko ? "잔고 · 계좌번호 · 증권사 키 수집 없음" : "No balances, account numbers, or broker keys"}
            </p>
            <Button type="submit" disabled={submitting}>
              {submitting && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              {ko ? "전략 등록하고 키 받기" : "Register and get key"}
            </Button>
          </div>
        </form>
      </CardContent>
    </Card>
  )
}
