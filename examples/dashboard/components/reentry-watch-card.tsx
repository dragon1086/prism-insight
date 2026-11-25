"use client"

import { useEffect, useState } from "react"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { Badge } from "@/components/ui/badge"
import { useLanguage } from "@/components/language-provider"
import type { Market } from "@/types/dashboard"

interface ReentryItem {
  ticker: string
  name: string
  source: "stop_exit" | "skipped"
  since: string
  sessions_left: number
  proximity: "near" | "mid" | "far" | null
}

interface ReentryWatch {
  market: Market
  as_of: string
  generated_at: string
  items: ReentryItem[] | null
}

// Longer than a normal weekend gap; older than this the list is unknown, not empty
const MAX_AGE_MS = 4 * 24 * 60 * 60 * 1000

export function ReentryWatchCard({ market }: { market: Market }) {
  const { language } = useLanguage()
  const ko = language === "ko"
  const [watch, setWatch] = useState<ReentryWatch | null>(null)
  const [loaded, setLoaded] = useState(false)

  useEffect(() => {
    let active = true
    const load = () =>
      fetch(`/reentry_watch_${market.toLowerCase()}.json`, { cache: "no-store" })
        .then((r) => (r.ok ? r.json() : null))
        .catch(() => null)
        .then((value: ReentryWatch | null) => {
          if (!active) return
          setWatch(value)
          setLoaded(true)
        })
    setWatch(null)
    setLoaded(false)
    load()
    const interval = setInterval(load, 5 * 60 * 1000)
    return () => {
      active = false
      clearInterval(interval)
    }
  }, [market])

  if (!loaded) return null

  const fresh = watch?.items && Date.now() - new Date(watch.generated_at).getTime() < MAX_AGE_MS
  const items = fresh ? watch.items! : null
  const stops = items?.filter((i) => i.source === "stop_exit") ?? []
  const skips = items?.filter((i) => i.source === "skipped") ?? []

  const proximityLabel = {
    near: ko ? "가까움" : "Near",
    mid: ko ? "보통" : "Medium",
    far: ko ? "멂" : "Far",
  }

  const table = (rows: ReentryItem[]) => (
    <div className="overflow-x-auto">
      <Table>
        <TableHeader>
          <TableRow className="hover:bg-transparent">
            <TableHead className="pl-5 text-xs font-normal text-muted-foreground">{ko ? "종목명" : "Stock"}</TableHead>
            <TableHead className="hidden sm:table-cell text-right text-xs font-normal text-muted-foreground">{ko ? "감시 시작" : "Since"}</TableHead>
            <TableHead className="text-right text-xs font-normal text-muted-foreground">{ko ? "남은 거래일" : "Sessions left"}</TableHead>
            <TableHead className="pr-5 text-right text-xs font-normal text-muted-foreground">{ko ? "기준선까지" : "To trigger"}</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((i) => (
            <TableRow key={i.ticker} className="font-mono tabular-nums text-[13px]">
              <TableCell className="pl-5 font-sans whitespace-normal">
                <p className="font-medium text-foreground">{i.name}</p>
                <p className="text-xs text-muted-foreground">{i.ticker}</p>
              </TableCell>
              <TableCell className="hidden sm:table-cell text-right text-muted-foreground">{i.since}</TableCell>
              <TableCell className="text-right text-muted-foreground">{i.sessions_left}</TableCell>
              <TableCell className="pr-5 text-right font-sans">
                <Badge
                  variant="outline"
                  className={`text-xs font-normal ${i.proximity === "near" ? "text-foreground" : "text-muted-foreground"}`}
                >
                  {i.proximity ? proximityLabel[i.proximity] : ko ? "확인 불가" : "Unknown"}
                </Badge>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  )

  return (
    <Card className="gap-0 py-0 rounded-lg shadow-none">
      <CardHeader className="px-5 pt-4 pb-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <CardTitle className="text-sm font-medium">{ko ? "재진입 감시" : "Re-entry Watch"}</CardTitle>
          <span className="text-xs text-muted-foreground font-mono tabular-nums">
            {items ? `${items.length} · ${watch!.as_of}` : "—"}
          </span>
        </div>
        <p className="text-xs text-muted-foreground">
          {ko
            ? "조건이 맞으면 다시 사려고 지켜보는 종목입니다. 조건이 오지 않으면 사지 않고 끝납니다. 가까움은 기준선에서 3% 이내, 보통은 10% 이내입니다."
            : "Stocks watched for a possible re-entry. They expire without a buy if conditions are not met. Near is within 3% of the trigger line, Medium within 10%."}
        </p>
      </CardHeader>
      <CardContent className="px-0 pb-1">
        {!items ? (
          <p className="px-5 pb-4 text-sm text-muted-foreground">
            {ko ? "최신 감시 목록을 확인할 수 없습니다." : "The latest watch list is unavailable."}
          </p>
        ) : items.length === 0 ? (
          <p className="px-5 pb-4 text-sm text-muted-foreground">
            {ko ? "감시 중인 종목이 없습니다." : "No stocks on watch."}
          </p>
        ) : (
          <>
            {stops.length > 0 && (
              <>
                <p className="px-5 pb-1 text-xs font-medium">{ko ? "손절 후 감시" : "After a stop"} {stops.length}</p>
                {table(stops)}
              </>
            )}
            {skips.length > 0 && (
              <details>
                <summary className="cursor-pointer px-5 py-3 text-xs font-medium">
                  {ko ? "매수 보류 후 감시" : "After a skipped buy"} {skips.length}
                </summary>
                {table(skips)}
              </details>
            )}
          </>
        )}
      </CardContent>
    </Card>
  )
}
