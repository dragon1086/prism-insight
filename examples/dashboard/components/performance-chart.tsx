"use client"

import { useMemo, useState } from "react"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import {
  Area,
  ComposedChart,
  Line,
  LineChart,
  CartesianGrid,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts"
import type { MarketCondition, PrismPerformance, Holding, Summary, Market } from "@/types/dashboard"
import { useLanguage } from "@/components/language-provider"
import { getSeasonInfo } from "@/lib/currency"

interface PerformanceChartProps {
  data: MarketCondition[]
  prismPerformance?: PrismPerformance[]
  holdings?: Holding[]
  summary?: Summary
  market?: Market
}

type Row = { date: string; prism: number; idx1: number; idx2: number }

// Trading-day windows; 0 = whole season
const RANGES = [
  { key: "1M", days: 21 },
  { key: "3M", days: 63 },
  { key: "6M", days: 126 },
  { key: "ALL", days: 0 },
] as const

const AXIS_TICK = { fill: "var(--muted-foreground)", fontSize: 11, fontFamily: "var(--font-geist-mono), ui-monospace, monospace" }

const signed = (v: number, digits = 2) => `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v).toFixed(digits)}%`
const toneClass = (v: number) => (v > 0 ? "text-success" : v < 0 ? "text-destructive" : "text-foreground")

export function PerformanceChart({ data, prismPerformance = [], market = "KR" }: PerformanceChartProps) {
  const { t, language } = useLanguage()
  const ko = language === "ko"
  const isUSMarket = market === "US"
  const seasonInfo = getSeasonInfo(market)
  const [range, setRange] = useState<(typeof RANGES)[number]["key"]>("ALL")

  const index1Name = isUSMarket ? "S&P 500" : "KOSPI"
  const index2Name = isUSMarket ? "NASDAQ" : "KOSDAQ"

  const sortedData = useMemo(
    () => [...data].sort((a, b) => new Date(a.date).getTime() - new Date(b.date).getTime()),
    [data],
  )

  const seasonRows: Row[] = useMemo(() => {
    const filtered = sortedData.filter((d) => d.date >= seasonInfo.startDate)
    if (filtered.length === 0) return []
    const v1 = (d: MarketCondition) => (isUSMarket ? d.spx_index : d.kospi_index) || 0
    const v2 = (d: MarketCondition) => (isUSMarket ? d.nasdaq_index : d.kosdaq_index) || 0
    const s1 = v1(filtered[0])
    const s2 = v2(filtered[0])
    const prismByDate = new Map(prismPerformance.map((p) => [p.date, p.prism_simulator_return]))
    let lastPrism = 0
    return filtered.map((d) => {
      // Carry the last realized value forward on days without a simulator row
      lastPrism = prismByDate.get(d.date) ?? lastPrism
      return {
        date: d.date,
        prism: lastPrism,
        idx1: s1 > 0 ? (v1(d) / s1 - 1) * 100 : 0,
        idx2: s2 > 0 ? (v2(d) / s2 - 1) * 100 : 0,
      }
    })
  }, [sortedData, prismPerformance, seasonInfo.startDate, isUSMarket])

  // Peak-to-current drawdown of the realized curve, in %p of starting capital
  const prismMDD = useMemo(() => {
    let peak = -Infinity
    let mdd = 0
    for (const p of prismPerformance) {
      peak = Math.max(peak, p.prism_simulator_return)
      mdd = Math.max(mdd, peak - p.prism_simulator_return)
    }
    return mdd
  }, [prismPerformance])

  const chartRows: Row[] = useMemo(() => {
    const days = RANGES.find((r) => r.key === range)?.days ?? 0
    if (!days || seasonRows.length <= days) return seasonRows
    const slice = seasonRows.slice(-days)
    const b = slice[0]
    const rebase = (v: number, base: number) => ((1 + v / 100) / (1 + base / 100) - 1) * 100
    return slice.map((r) => ({
      date: r.date,
      prism: r.prism - b.prism,
      idx1: rebase(r.idx1, b.idx1),
      idx2: rebase(r.idx2, b.idx2),
    }))
  }, [seasonRows, range])

  const title = ko ? "누적 수익률 비교" : "Cumulative Return vs Index"

  if (seasonRows.length === 0) {
    return (
      <Card className="gap-0 py-0 rounded-lg shadow-none">
        <CardHeader className="px-5 pt-4 pb-3">
          <CardTitle className="text-sm font-medium">{title}</CardTitle>
        </CardHeader>
        <CardContent className="px-5 pb-5">
          <div className="flex items-center justify-center h-[240px] text-sm text-muted-foreground">
            {ko ? "데이터가 없습니다." : "No data available."}
          </div>
        </CardContent>
      </Card>
    )
  }

  const last = chartRows[chartRows.length - 1]
  const spansYears = chartRows[0].date.slice(0, 4) !== last.date.slice(0, 4)
  const formatTick = (value: string) => {
    const [y, m, d] = value.split("-")
    return spansYears && range === "ALL" ? `${y.slice(2)}.${m}` : `${Number(m)}/${Number(d)}`
  }
  const prismLabel = t("chart.prismReturn")

  return (
    <div className="space-y-3">
      <Card className="gap-0 py-0 rounded-lg shadow-none">
        <CardHeader className="px-5 pt-4 pb-0">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="space-y-2 min-w-0">
              <CardTitle className="text-sm font-medium">
                {title}{" "}
                <span className="font-normal text-muted-foreground">
                  ({seasonInfo.seasonName}{range === "ALL" ? "" : ` · ${range}`})
                </span>
              </CardTitle>
              <dl className="flex flex-wrap gap-x-5 gap-y-1 text-xs">
                <div className="flex items-baseline gap-1.5">
                  <dt className="flex items-center gap-1.5 text-muted-foreground">
                    <span className="inline-block w-3 border-t-2" style={{ borderColor: "var(--chart-1)" }} />
                    PRISM
                  </dt>
                  <dd className={`font-mono tabular-nums font-medium ${toneClass(last.prism)}`}>{signed(last.prism)}</dd>
                </div>
                <div className="flex items-baseline gap-1.5">
                  <dt className="flex items-center gap-1.5 text-muted-foreground">
                    <span className="inline-block w-3 border-t-2 border-dashed" style={{ borderColor: "var(--muted-foreground)" }} />
                    {index1Name}
                  </dt>
                  <dd className="font-mono tabular-nums">{signed(last.idx1)}</dd>
                </div>
                <div className="flex items-baseline gap-1.5">
                  <dt className="flex items-center gap-1.5 text-muted-foreground">
                    <span className="inline-block w-3 border-t-2 border-dotted opacity-60" style={{ borderColor: "var(--muted-foreground)" }} />
                    {index2Name}
                  </dt>
                  <dd className="font-mono tabular-nums">{signed(last.idx2)}</dd>
                </div>
                {prismMDD > 0 && (
                  <div className="flex items-baseline gap-1.5">
                    <dt className="text-muted-foreground">MDD</dt>
                    <dd className="font-mono tabular-nums text-destructive">−{prismMDD.toFixed(2)}%p</dd>
                  </div>
                )}
              </dl>
            </div>
            <div className="flex rounded-md border border-border p-0.5 gap-0.5" role="group" aria-label={ko ? "기간" : "Range"}>
              {RANGES.map((r) => (
                <button
                  key={r.key}
                  type="button"
                  onClick={() => setRange(r.key)}
                  aria-pressed={range === r.key}
                  className={`px-2.5 py-0.5 rounded text-xs font-mono transition-colors ${
                    range === r.key ? "bg-secondary text-foreground" : "text-muted-foreground hover:text-foreground"
                  }`}
                >
                  {r.key === "ALL" ? (ko ? "전체" : "All") : r.key}
                </button>
              ))}
            </div>
          </div>
        </CardHeader>
        <CardContent className="px-2 pb-3 pt-2">
          <ResponsiveContainer width="100%" height={640}>
            <ComposedChart data={chartRows} margin={{ top: 8, right: 24, bottom: 0, left: 0 }}>
              <defs>
                <linearGradient id="prismFill" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="var(--chart-1)" stopOpacity={0.22} />
                  <stop offset="100%" stopColor="var(--chart-1)" stopOpacity={0} />
                </linearGradient>
              </defs>
              <CartesianGrid vertical={false} stroke="var(--border)" />
              <XAxis
                dataKey="date"
                tick={AXIS_TICK}
                tickLine={false}
                axisLine={false}
                tickMargin={8}
                minTickGap={40}
                tickFormatter={formatTick}
              />
              <YAxis
                tick={AXIS_TICK}
                tickLine={false}
                axisLine={false}
                width={52}
                tickFormatter={(v: number) => `${v > 0 ? "+" : ""}${Math.round(v)}%`}
              />
              <ReferenceLine y={0} stroke="var(--muted-foreground)" strokeOpacity={0.4} />
              <Tooltip
                cursor={{ stroke: "var(--muted-foreground)", strokeOpacity: 0.3 }}
                content={({ active, payload, label }) => {
                  if (!active || !payload?.length) return null
                  const row = payload[0].payload as Row
                  return (
                    <div className="rounded-md border border-border bg-popover px-3 py-2 text-xs shadow-md">
                      <div className="mb-1.5 font-mono text-muted-foreground">{String(label)}</div>
                      <div className="grid grid-cols-[auto_auto] gap-x-4 gap-y-0.5">
                        <span className="text-muted-foreground">{prismLabel}</span>
                        <span className={`text-right font-mono tabular-nums ${toneClass(row.prism)}`}>{signed(row.prism)}</span>
                        <span className="text-muted-foreground">{index1Name}</span>
                        <span className="text-right font-mono tabular-nums">{signed(row.idx1)}</span>
                        <span className="text-muted-foreground">{index2Name}</span>
                        <span className="text-right font-mono tabular-nums">{signed(row.idx2)}</span>
                        <span className="text-muted-foreground">{ko ? `${index1Name} 대비` : `vs ${index1Name}`}</span>
                        <span className={`text-right font-mono tabular-nums ${toneClass(row.prism - row.idx1)}`}>
                          {signed(row.prism - row.idx1).replace("%", "%p")}
                        </span>
                      </div>
                    </div>
                  )
                }}
              />
              <Line type="monotone" dataKey="idx2" stroke="var(--muted-foreground)" strokeOpacity={0.55} strokeWidth={1.25} strokeDasharray="2 3" dot={false} activeDot={false} isAnimationActive={false} />
              <Line type="monotone" dataKey="idx1" stroke="var(--muted-foreground)" strokeWidth={1.25} strokeDasharray="5 4" dot={false} activeDot={false} isAnimationActive={false} />
              <Area type="monotone" dataKey="prism" stroke="var(--chart-1)" strokeWidth={2} fill="url(#prismFill)" dot={false} activeDot={{ r: 3.5, strokeWidth: 0 }} isAnimationActive={false} />
            </ComposedChart>
          </ResponsiveContainer>
          <p className="px-3 pt-1 text-[11px] text-muted-foreground">
            {ko
              ? "PRISM은 매도 완료한 거래의 누적 실현 수익(시작 자금 대비 %p), 지수는 시즌 시작일 대비 등락률입니다. 기간을 바꾸면 그 시작일 기준으로 다시 계산합니다."
              : "PRISM is cumulative realized return on starting capital (%p); indices are change since season start. Changing the range rebases all lines to that range's first day."}
          </p>
        </CardContent>
      </Card>

      <IndexCharts data={sortedData} market={market} />
    </div>
  )
}

// KOSPI/KOSDAQ (or S&P 500/NASDAQ) level charts
function IndexCharts({ data, market = "KR" }: { data: MarketCondition[]; market?: Market }) {
  const { t } = useLanguage()
  const isUSMarket = market === "US"

  const formatNumber = (value: number) => new Intl.NumberFormat("ko-KR", { maximumFractionDigits: 0 }).format(value)

  const series = (key: "kospi_index" | "kosdaq_index" | "spx_index" | "nasdaq_index") =>
    data.filter((d) => (d[key] || 0) > 0).map((d) => ({ date: d.date, value: d[key] as number }))

  const cards = isUSMarket
    ? [
        { name: "S&P 500", rows: series("spx_index") },
        { name: "NASDAQ", rows: series("nasdaq_index") },
      ]
    : [
        { name: "KOSPI", rows: series("kospi_index") },
        { name: "KOSDAQ", rows: series("kosdaq_index") },
      ]

  return (
    <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
      {cards.map(({ name, rows }) => {
        if (rows.length < 2) return null
        const current = rows[rows.length - 1].value
        const prev = rows[rows.length - 2].value
        const change = current - prev
        const pct = (change / prev) * 100
        const values = rows.map((r) => r.value)
        const min = Math.min(...values)
        const max = Math.max(...values)
        const pad = (max - min) * 0.05
        return (
          <Card key={name} className="gap-0 py-0 rounded-lg shadow-none">
            <CardHeader className="px-5 pt-4 pb-0">
              <div className="flex items-baseline justify-between gap-3">
                <CardTitle className="text-sm font-medium">
                  {name} <span className="font-normal text-muted-foreground">{t("chart.index")}</span>
                </CardTitle>
                <div className="text-right">
                  <span className="font-serif text-xl tabular-nums">{formatNumber(current)}</span>{" "}
                  <span className={`text-xs font-mono tabular-nums ${toneClass(change)}`}>
                    {change >= 0 ? "+" : "−"}
                    {formatNumber(Math.abs(change))} ({signed(pct)})
                  </span>
                </div>
              </div>
            </CardHeader>
            <CardContent className="px-2 pb-3 pt-2">
              <ResponsiveContainer width="100%" height={150}>
                <LineChart data={rows} margin={{ top: 4, right: 24, bottom: 0, left: 0 }}>
                  <CartesianGrid vertical={false} stroke="var(--border)" />
                  <XAxis
                    dataKey="date"
                    tick={AXIS_TICK}
                    tickLine={false}
                    axisLine={false}
                    tickMargin={6}
                    minTickGap={48}
                    tickFormatter={(v: string) => `${v.slice(2, 4)}.${v.slice(5, 7)}`}
                  />
                  <YAxis
                    tick={AXIS_TICK}
                    tickLine={false}
                    axisLine={false}
                    width={52}
                    domain={[Math.floor(min - pad), Math.ceil(max + pad)]}
                    tickFormatter={formatNumber}
                  />
                  <Tooltip
                    cursor={{ stroke: "var(--muted-foreground)", strokeOpacity: 0.3 }}
                    contentStyle={{
                      backgroundColor: "var(--popover)",
                      border: "1px solid var(--border)",
                      borderRadius: 6,
                      padding: "6px 10px",
                      fontSize: 12,
                    }}
                    labelStyle={{ color: "var(--muted-foreground)" }}
                    itemStyle={{ color: "var(--popover-foreground)" }}
                    formatter={(value: number) => [formatNumber(value), name]}
                  />
                  <Line type="monotone" dataKey="value" stroke="var(--foreground)" strokeOpacity={0.8} strokeWidth={1.5} dot={false} activeDot={{ r: 3 }} isAnimationActive={false} />
                </LineChart>
              </ResponsiveContainer>
            </CardContent>
          </Card>
        )
      })}
    </div>
  )
}
