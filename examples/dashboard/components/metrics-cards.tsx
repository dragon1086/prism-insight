"use client"

import { Card, CardContent } from "@/components/ui/card"
import { useLanguage } from "@/components/language-provider"
import { formatCurrency as formatCurrencyUtil, getSeasonInfo, getDaysElapsed } from "@/lib/currency"
import type { Summary, Market } from "@/types/dashboard"

type Tone = "gain" | "loss" | "neutral"
const TONE_CLASS: Record<Tone, string> = { gain: "text-success", loss: "text-destructive", neutral: "text-foreground" }

interface Metric {
  label: string
  value: string
  change: string
  changeValue?: string
  description?: string
  tone: Tone
}

interface MetricsCardsProps {
  summary: Summary
  realPortfolio?: Array<{
    profit_rate: number
    name?: string
    profit?: number
  }>
  tradingHistoryCount?: number
  tradingHistoryTotalProfit?: number
  tradingHistoryAvgProfit?: number
  tradingHistoryAvgDays?: number
  tradingHistoryWinRate?: number
  tradingHistoryWinCount?: number
  tradingHistoryLossCount?: number
  market?: Market
}

export function MetricsCards({
  summary,
  realPortfolio = [],
  tradingHistoryCount = 0,
  tradingHistoryTotalProfit = 0,
  tradingHistoryAvgProfit = 0,
  tradingHistoryAvgDays = 0,
  tradingHistoryWinRate = 0,
  tradingHistoryWinCount = 0,
  tradingHistoryLossCount = 0,
  market = "KR"
}: MetricsCardsProps) {
  const { language, t } = useLanguage()

  const formatCurrency = (value: number) => {
    return formatCurrencyUtil(value, market, language as "ko" | "en")
  }

  const formatPercent = (value: number) => {
    return `${value >= 0 ? "+" : ""}${value.toFixed(2)}%`
  }

  // Season info based on market
  const seasonInfo = getSeasonInfo(market)
  const daysElapsed = getDaysElapsed(market)

  // 총 자산 계산 (평가금액 + 예수금)
  // US sends a trade-date total; summing settled cash alone drops sale proceeds still settling
  const totalAssets = summary.real_trading.total_assets ||
                      (summary.real_trading.total_eval_amount || 0) + (summary.real_trading.available_amount || 0)

  // Season 시작 금액
  const seasonStartAmount = seasonInfo.startAmount
  const totalAssetsReturn = totalAssets > 0 ? ((totalAssets - seasonStartAmount) / seasonStartAmount) * 100 : 0

  const isUSMarket = market === "US"
  const toneOf = (v: number): Tone => (v > 0 ? "gain" : v < 0 ? "loss" : "neutral")

  // 현금 비율 계산 (total_cash 사용: D+2 포함 총 현금, fallback으로 deposit)
  const totalCash = summary.real_trading.total_cash || summary.real_trading.deposit || 0
  const cashRatio = totalAssets > 0 ? (totalCash / totalAssets) * 100 : 0
  const investmentRatio = 100 - cashRatio

  const rt = summary.real_trading
  const accountMissing = !(rt.total_eval_amount || rt.available_amount || rt.total_cash || rt.deposit || rt.total_stocks)
  const missingNote = language === "ko" ? "계좌 조회값 없음 · 다음 갱신 때 표시" : "No account snapshot yet · shown after next refresh"

  const realMetrics: Metric[] = accountMissing ? [
    { label: t("metrics.realTotalAssets"), description: t("metrics.assetsDesc"), value: "—",
      change: `${t("metrics.startAmount")} ${formatCurrency(seasonStartAmount)}`, changeValue: missingNote, tone: "neutral" },
    { label: t("metrics.realHoldingsProfit"), description: t("metrics.excludeRealized"), value: "—",
      change: t("metrics.holdingsProfitDesc"), changeValue: missingNote, tone: "neutral" },
    { label: t("metrics.cashAndStability"), description: t("metrics.cashStabilityDesc"), value: "—",
      change: missingNote, tone: "neutral" },
  ] : [
    {
      label: t("metrics.realTotalAssets"),
      value: formatCurrency(totalAssets),
      change: `${t("metrics.startAmount")} ${formatCurrency(seasonStartAmount)} (${formatPercent(totalAssetsReturn)})`,
      // KR balance API never fills available_amount (ord_psbl_cash), so use total cash
      changeValue: totalCash > 0
        ? `${t("metrics.deposit")} ${formatCurrency(totalCash)} | ${summary.real_trading.total_stocks || 0}${t("metrics.stocks")}`
        : `${t("metrics.fullyInvested")} | ${summary.real_trading.total_stocks || 0}${t("metrics.stocks")}`,
      description: t("metrics.assetsDesc"),
      tone: toneOf(totalAssetsReturn),
    },
    {
      label: t("metrics.realHoldingsProfit"),
      value: formatCurrency(summary.real_trading.total_profit_amount || 0),
      change: formatPercent(summary.real_trading.total_profit_rate || 0),
      changeValue: t("metrics.holdingsProfitDesc"),
      description: t("metrics.excludeRealized"),
      tone: toneOf(summary.real_trading.total_profit_amount || 0),
    },
    {
      label: t("metrics.cashAndStability"),
      value: formatCurrency(totalCash),
      change: `${t("metrics.cashRatio")} ${cashRatio.toFixed(1)}%`,
      changeValue: `${t("metrics.investmentRatio")} ${investmentRatio.toFixed(1)}% | ${summary.real_trading.total_stocks || 0}${t("metrics.stocks")}`,
      description: t("metrics.cashStabilityDesc"),
      tone: "neutral" as Tone,
    },
  ]

  const simulatorMetrics: Metric[] = [
    {
      label: t("metrics.simSoldProfit"),
      value: tradingHistoryCount > 0 ? formatPercent(tradingHistoryTotalProfit) : t("metrics.waitingSell"),
      change: tradingHistoryCount > 0
        ? `${tradingHistoryCount}${t("common.trades")} ${t("metrics.sold")}`
        : t("metrics.onlyHolding"),
      changeValue: tradingHistoryCount > 0
        ? `${tradingHistoryWinCount}${t("metrics.wins")} ${tradingHistoryLossCount}${t("metrics.losses")} (${t("metrics.avgProfit")} ${formatPercent(tradingHistoryAvgProfit)})`
        : t("metrics.updateOnSell"),
      description: t("metrics.soldProfitDesc"),
      tone: tradingHistoryCount === 0 ? ("neutral" as Tone) : toneOf(tradingHistoryTotalProfit),
    },
    {
      label: t("metrics.simAvgHoldingDays"),
      value: tradingHistoryCount > 0 ? `${Math.round(tradingHistoryAvgDays)}${t("common.days")}` : `-${t("common.days")}`,
      change: tradingHistoryCount > 0
        ? `${tradingHistoryCount}${t("metrics.soldBasis")}`
        : t("metrics.waitingSell"),
      changeValue: tradingHistoryCount > 0
        ? `${t("metrics.winRate")} ${tradingHistoryWinRate.toFixed(0)}%`
        : t("metrics.needStrategy"),
      description: t("metrics.avgHoldingDesc"),
      tone: "neutral" as Tone,
    },
    {
      label: t("metrics.simCurrentProfit"),
      value: formatPercent(summary.portfolio.total_profit || 0),
      change: `${t("metrics.holding")} ${summary.portfolio.total_stocks || 0}${t("metrics.stocks")} (${t("metrics.avgProfit")} ${formatPercent(summary.portfolio.avg_profit_rate || 0)})`,
      changeValue: `${t("metrics.slotUsage")} ${summary.portfolio.slot_usage}` +
        (summary.portfolio.allocated_slots !== undefined &&
        summary.portfolio.allocated_slots < (summary.portfolio.total_stocks || 0)
          ? ` (${t("metrics.allocatedSlots")} ${summary.portfolio.allocated_slots.toFixed(2)})`
          : ""),
      description: t("metrics.currentProfitDesc"),
      tone: toneOf(summary.portfolio.total_profit || 0),
    },
  ]

  const realTitle = isUSMarket ? (language === "ko" ? "미국 실전투자" : "US Real Trading") : t("metrics.realTrading")

  const section = (title: string, metrics: Metric[]) => (
    <section className="space-y-2.5">
      <div className="flex flex-col gap-0.5 sm:flex-row sm:items-baseline sm:justify-between sm:gap-4">
        <h2 className="text-sm font-medium text-foreground">{title}</h2>
        <span className="text-xs text-muted-foreground font-mono tabular-nums">
          {seasonInfo.startDate.replace(/-/g, ".")} {t("metrics.started")} · {daysElapsed}{t("metrics.elapsed")}
        </span>
      </div>
      <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
        {metrics.map((metric, index) => (
          <Card key={index} className="gap-0 py-0 rounded-lg shadow-none">
            <CardContent className="p-4 sm:p-5 space-y-2.5 sm:space-y-3">
              <div>
                <span className="text-[13px] text-muted-foreground block">{metric.label}</span>
                {metric.description && (
                  <span className="text-xs text-muted-foreground/70">{metric.description}</span>
                )}
              </div>
              <p className={`font-serif text-[1.75rem] sm:text-[2rem] leading-none tracking-tight tabular-nums ${TONE_CLASS[metric.tone]}`}>
                {metric.value}
              </p>
              <div className="flex flex-col gap-0.5 text-xs tabular-nums">
                <span className="text-foreground/80">{metric.change}</span>
                {metric.changeValue && <span className="text-muted-foreground">{metric.changeValue}</span>}
              </div>
            </CardContent>
          </Card>
        ))}
      </div>
    </section>
  )

  return (
    <div className="space-y-6">
      {section(realTitle.includes(seasonInfo.seasonName) ? realTitle : `${realTitle} (${seasonInfo.seasonName})`, realMetrics)}
      {section(isUSMarket ? (language === "ko" ? "미국 시뮬레이터" : "US Simulator") : t("metrics.simulator"), simulatorMetrics)}
    </div>
  )
}
