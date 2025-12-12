"use client"

import { TrendingUp, TrendingDown, Wallet, DollarSign, PiggyBank, Zap, Clock } from "lucide-react"
import { Card, CardContent } from "@/components/ui/card"
import { useLanguage } from "@/components/language-provider"
import type { Summary } from "@/types/dashboard"

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
  tradingHistoryLossCount = 0
}: MetricsCardsProps) {
  const { language, t } = useLanguage()

  const formatCurrency = (value: number) => {
    return new Intl.NumberFormat(language === "en" ? "en-US" : "ko-KR", {
      style: "currency",
      currency: "KRW",
      maximumFractionDigits: 0,
    }).format(value)
  }

  const formatPercent = (value: number) => {
    return `${value >= 0 ? "+" : ""}${value.toFixed(2)}%`
  }

  // 시즌2 시작일 계산
  const season2StartDate = new Date('2025-09-29')
  const today = new Date()
  const daysElapsed = Math.floor((today.getTime() - season2StartDate.getTime()) / (1000 * 60 * 60 * 24))

  // 총 자산 계산 (평가금액 + 예수금)
  const totalAssets = (summary.real_trading.total_eval_amount || 0) + 
                      (summary.real_trading.available_amount || 0)

  // Season2 시작 금액
  const season2StartAmount = 9969801
  const totalAssetsReturn = totalAssets > 0 ? ((totalAssets - season2StartAmount) / season2StartAmount) * 100 : 0

  // 현금 비율 계산 (deposit 사용, KIS API의 tot_evlu_amt가 이미 예수금 포함)
  const deposit = summary.real_trading.deposit || 0
  const cashRatio = totalAssets > 0 ? (deposit / totalAssets) * 100 : 0
  const investmentRatio = 100 - cashRatio

  const realMetrics = [
    {
      label: t("metrics.realTotalAssets"),
      value: formatCurrency(totalAssets),
      change: `${t("metrics.startAmount")} ${formatCurrency(season2StartAmount)} (${formatPercent(totalAssetsReturn)})`,
      changeValue: summary.real_trading.available_amount > 0
        ? `${t("metrics.deposit")} ${formatCurrency(summary.real_trading.available_amount)} | ${summary.real_trading.total_stocks || 0}${t("metrics.stocks")}`
        : `${t("metrics.fullyInvested")} | ${summary.real_trading.total_stocks || 0}${t("metrics.stocks")}`,
      description: t("metrics.assetsDesc"),
      isPositive: true,
      icon: Wallet,
      gradient: "from-blue-500/20 to-blue-500/5",
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
      value: formatCurrency(deposit),
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
      changeValue: `${t("metrics.slotUsage")} ${summary.portfolio.slot_usage}`,
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
            <div className="h-1 w-8 rounded-full bg-gradient-to-r from-blue-500 to-indigo-500" />
            <h2 className="text-sm font-semibold text-muted-foreground">{t("metrics.realTrading")}</h2>
          </div>
          <div className="flex items-center gap-2">
            <span className="text-xs font-semibold text-blue-600 dark:text-blue-400">
              2025.09.29 {t("metrics.started")}
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
            <h2 className="text-sm font-semibold text-muted-foreground">{t("metrics.simulator")}</h2>
          </div>
          <div className="flex items-center gap-2">
            <span className="text-xs font-semibold text-purple-600 dark:text-purple-400">
              2025.09.29 {t("metrics.started")}
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
