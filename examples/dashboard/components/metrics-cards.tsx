"use client"

import { TrendingUp, TrendingDown, Wallet, DollarSign, PiggyBank, Zap, Clock } from "lucide-react"
import { Card, CardContent } from "@/components/ui/card"
import { useLanguage } from "@/components/language-provider"
import { formatCurrency as formatCurrencyUtil, getSeasonInfo, getDaysElapsed } from "@/lib/currency"
import type { Summary, Market } from "@/types/dashboard"

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
  const totalAssets = (summary.real_trading.total_eval_amount || 0) +
                      (summary.real_trading.available_amount || 0)

  // Season 시작 금액
  const seasonStartAmount = seasonInfo.startAmount
  const totalAssetsReturn = totalAssets > 0 ? ((totalAssets - seasonStartAmount) / seasonStartAmount) * 100 : 0

  // Market-specific colors
  const isUSMarket = market === "US"
  const primaryGradient = isUSMarket ? "from-emerald-500/20 to-emerald-500/5" : "from-blue-500/20 to-blue-500/5"
  const secondaryGradient = isUSMarket ? "from-teal-500/20 to-teal-500/5" : "from-indigo-500/20 to-indigo-500/5"
  const sectionGradient = isUSMarket ? "from-emerald-500 to-teal-500" : "from-blue-500 to-indigo-500"
  const sectionTextColor = isUSMarket ? "text-emerald-600 dark:text-emerald-400" : "text-blue-600 dark:text-blue-400"

  // 현금 비율 계산 (total_cash 사용: D+2 포함 총 현금, fallback으로 deposit)
  const totalCash = summary.real_trading.total_cash || summary.real_trading.deposit || 0
  const cashRatio = totalAssets > 0 ? (totalCash / totalAssets) * 100 : 0
  const investmentRatio = 100 - cashRatio

  const realMetrics = [
    {
      label: t("metrics.realTotalAssets"),
      value: formatCurrency(totalAssets),
      change: `${t("metrics.startAmount")} ${formatCurrency(seasonStartAmount)} (${formatPercent(totalAssetsReturn)})`,
      changeValue: summary.real_trading.available_amount > 0
        ? `${t("metrics.deposit")} ${formatCurrency(summary.real_trading.available_amount)} | ${summary.real_trading.total_stocks || 0}${t("metrics.stocks")}`
        : `${t("metrics.fullyInvested")} | ${summary.real_trading.total_stocks || 0}${t("metrics.stocks")}`,
      description: t("metrics.assetsDesc"),
      isPositive: true,
      icon: Wallet,
      gradient: primaryGradient,
    },
    {
      label: t("metrics.realHoldingsProfit"),
      value: formatCurrency(summary.real_trading.total_profit_amount || 0),
      change: formatPercent(summary.real_trading.total_profit_rate || 0),
      changeValue: t("metrics.holdingsProfitDesc"),
      description: t("metrics.excludeRealized"),
      isPositive: (summary.real_trading.total_profit_amount || 0) >= 0,
      icon: (summary.real_trading.total_profit_amount || 0) >= 0 ? TrendingUp : TrendingDown,
      gradient:
        (summary.real_trading.total_profit_amount || 0) >= 0
          ? "from-success/20 to-success/5"
          : "from-destructive/20 to-destructive/5",
    },
    {
      label: t("metrics.cashAndStability"),
      value: formatCurrency(totalCash),
      change: `${t("metrics.cashRatio")} ${cashRatio.toFixed(1)}%`,
      changeValue: `${t("metrics.investmentRatio")} ${investmentRatio.toFixed(1)}% | ${summary.real_trading.total_stocks || 0}${t("metrics.stocks")}`,
      description: t("metrics.cashStabilityDesc"),
      isPositive: cashRatio >= 10,
      icon: PiggyBank,
      gradient: cashRatio >= 20
        ? "from-emerald-500/20 to-emerald-500/5"
        : cashRatio >= 10
          ? "from-yellow-500/20 to-yellow-500/5"
          : "from-orange-500/20 to-orange-500/5",
    },
  ]

  const simulatorMetrics = [
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
      isPositive: tradingHistoryCount === 0 || tradingHistoryTotalProfit >= 0,
      icon: DollarSign,
      gradient: "from-purple-500/20 to-purple-500/5",
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
      isPositive: true,
      icon: Clock,
      gradient: "from-indigo-500/20 to-indigo-500/5",
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
      isPositive: (summary.portfolio.total_profit || 0) >= 0,
      icon: Zap,
      gradient: "from-pink-500/20 to-pink-500/5",
    },
  ]

  return (
    <div className="space-y-4">
      {/* Real Trading Section */}
      <div>
        <div className="flex items-center justify-between mb-3">
          <div className="flex items-center gap-2">
            <div className={`h-1 w-8 rounded-full bg-gradient-to-r ${sectionGradient}`} />
            <h2 className="text-sm font-semibold text-muted-foreground">
              {isUSMarket ? (language === "ko" ? "미국 실전투자" : "US Real Trading") : t("metrics.realTrading")}
              {" "}({seasonInfo.seasonName})
            </h2>
          </div>
          <div className="flex items-center gap-2">
            <span className={`text-xs font-semibold ${sectionTextColor}`}>
              {seasonInfo.startDate.replace(/-/g, ".")} {t("metrics.started")}
            </span>
            <span className="text-xs text-muted-foreground">
              ({daysElapsed}{t("metrics.elapsed")})
            </span>
          </div>
        </div>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          {realMetrics.map((metric, index) => {
            const Icon = metric.icon
            return (
              <Card
                key={index}
                className="relative overflow-hidden border-border/50 hover:border-border transition-all duration-300 hover:shadow-lg"
              >
                <div className={`absolute inset-0 bg-gradient-to-br ${metric.gradient} opacity-50`} />
                <CardContent className="relative p-6">
                  <div className="flex items-start justify-between mb-4">
                    <div className="flex items-center gap-2">
                      <div className="p-2 rounded-lg bg-background/80 backdrop-blur-sm">
                        <Icon className="w-4 h-4 text-foreground" />
                      </div>
                      <div>
                        <span className="text-sm font-medium text-muted-foreground block">{metric.label}</span>
                        {metric.description && (
                          <span className="text-xs text-muted-foreground/70">{metric.description}</span>
                        )}
                      </div>
                    </div>
                  </div>
                  <div className="space-y-1">
                    <p className="text-2xl font-bold text-foreground">{metric.value}</p>
                    <div className="flex flex-col gap-0.5">
                      <span className={`text-sm font-medium ${metric.isPositive ? "text-success" : "text-muted-foreground"}`}>
                        {metric.change}
                      </span>
                      {metric.changeValue && <span className="text-xs text-muted-foreground">{metric.changeValue}</span>}
                    </div>
                  </div>
                </CardContent>
              </Card>
            )
          })}
        </div>
      </div>

      {/* Simulator Section */}
      <div>
        <div className="flex items-center justify-between mb-3">
          <div className="flex items-center gap-2">
            <div className="h-1 w-8 rounded-full bg-gradient-to-r from-purple-500 to-pink-500" />
            <h2 className="text-sm font-semibold text-muted-foreground">
              {isUSMarket ? (language === "ko" ? "미국 시뮬레이터" : "US Simulator") : t("metrics.simulator")}
            </h2>
          </div>
          <div className="flex items-center gap-2">
            <span className="text-xs font-semibold text-purple-600 dark:text-purple-400">
              {seasonInfo.startDate.replace(/-/g, ".")} {t("metrics.started")}
            </span>
            <span className="text-xs text-muted-foreground">
              ({daysElapsed}{t("metrics.elapsed")})
            </span>
          </div>
        </div>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          {simulatorMetrics.map((metric, index) => {
            const Icon = metric.icon
            return (
              <Card
                key={index}
                className="relative overflow-hidden border-border/50 hover:border-border transition-all duration-300 hover:shadow-lg"
              >
                <div className={`absolute inset-0 bg-gradient-to-br ${metric.gradient} opacity-50`} />
                <CardContent className="relative p-6">
                  <div className="flex items-start justify-between mb-4">
                    <div className="flex items-center gap-2">
                      <div className="p-2 rounded-lg bg-background/80 backdrop-blur-sm">
                        <Icon className="w-4 h-4 text-foreground" />
                      </div>
                      <div>
                        <span className="text-sm font-medium text-muted-foreground block">{metric.label}</span>
                        {metric.description && (
                          <span className="text-xs text-muted-foreground/70">{metric.description}</span>
                        )}
                      </div>
                    </div>
                  </div>
                  <div className="space-y-1">
                    <p className="text-2xl font-bold text-foreground">{metric.value}</p>
                    <div className="flex flex-col gap-0.5">
                      <span className={`text-sm font-medium ${metric.isPositive ? "text-success" : "text-muted-foreground"}`}>
                        {metric.change}
                      </span>
                      {metric.changeValue && <span className="text-xs text-muted-foreground">{metric.changeValue}</span>}
                    </div>
                  </div>
                </CardContent>
              </Card>
            )
          })}
        </div>
      </div>
    </div>
  )
}
