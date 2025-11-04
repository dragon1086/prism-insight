"use client"

import { TrendingUp, TrendingDown, Wallet, DollarSign, BarChart3, Zap, Clock } from "lucide-react"
import { Card, CardContent } from "@/components/ui/card"
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
  const formatCurrency = (value: number) => {
    return new Intl.NumberFormat("ko-KR", {
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

  // 실전 포트폴리오 손익 분포 계산
  const profitStocks = realPortfolio.filter(stock => (stock.profit_rate || 0) > 0)
  const lossStocks = realPortfolio.filter(stock => (stock.profit_rate || 0) < 0)
  const breakEvenStocks = realPortfolio.filter(stock => (stock.profit_rate || 0) === 0)
  const winRateReal = realPortfolio.length > 0 
    ? (profitStocks.length / realPortfolio.length) * 100 
    : 0

  const realMetrics = [
    {
      label: "실전 총 자산",
      value: formatCurrency(totalAssets),
      change: `시작금액 ${formatCurrency(season2StartAmount)} (${formatPercent(totalAssetsReturn)})`,
      changeValue: summary.real_trading.available_amount > 0 
        ? `예수금 ${formatCurrency(summary.real_trading.available_amount)} | ${summary.real_trading.total_stocks || 0}개 종목`
        : `전액 투자중 | ${summary.real_trading.total_stocks || 0}개 종목`,
      description: "평가금액 + 예수금",
      isPositive: true,
      icon: Wallet,
      gradient: "from-blue-500/20 to-blue-500/5",
    },
    {
      label: "실전 보유종목 평가손익",
      value: formatCurrency(summary.real_trading.total_profit_amount || 0),
      change: formatPercent(summary.real_trading.total_profit_rate || 0),
      changeValue: "현재 보유중인 종목의 손익",
      description: "실현손익 제외",
      isPositive: (summary.real_trading.total_profit_amount || 0) >= 0,
      icon: (summary.real_trading.total_profit_amount || 0) >= 0 ? TrendingUp : TrendingDown,
      gradient:
        (summary.real_trading.total_profit_amount || 0) >= 0 
          ? "from-success/20 to-success/5" 
          : "from-destructive/20 to-destructive/5",
    },
    {
      label: "실전 포트폴리오 손익 분포",
      value: `${profitStocks.length}승 ${lossStocks.length}패`,
      change: `승률 ${winRateReal.toFixed(0)}%`,
      changeValue: breakEvenStocks.length > 0 
        ? `보합 ${breakEvenStocks.length}개` 
        : `총 ${realPortfolio.length}개 종목`,
      description: "현재 보유 종목 수익/손실 현황",
      isPositive: profitStocks.length >= lossStocks.length,
      icon: BarChart3,
      gradient: profitStocks.length >= lossStocks.length
        ? "from-emerald-500/20 to-emerald-500/5"
        : "from-orange-500/20 to-orange-500/5",
    },
  ]

  const simulatorMetrics = [
    {
      label: "시뮬레이터 매도종목 누적수익률",
      value: tradingHistoryCount > 0 ? formatPercent(tradingHistoryTotalProfit) : "매도 대기중",
      change: tradingHistoryCount > 0 
        ? `${tradingHistoryCount}건 매도` 
        : "현재 보유만 존재",
      changeValue: tradingHistoryCount > 0
        ? `${tradingHistoryWinCount}승 ${tradingHistoryLossCount}패 (평균 ${formatPercent(tradingHistoryAvgProfit)})`
        : "매도 시 업데이트",
      description: "매도 완료한 종목 수익률 합계",
      isPositive: tradingHistoryCount === 0 || tradingHistoryTotalProfit >= 0,
      icon: DollarSign,
      gradient: "from-purple-500/20 to-purple-500/5",
    },
    {
      label: "시뮬레이터 평균 보유기간",
      value: tradingHistoryCount > 0 ? `${Math.round(tradingHistoryAvgDays)}일` : "-일",
      change: tradingHistoryCount > 0 
        ? `${tradingHistoryCount}건 매도 기준` 
        : "매도 대기중",
      changeValue: tradingHistoryCount > 0
        ? `승률 ${tradingHistoryWinRate.toFixed(0)}%`
        : "보유 전략 확인 필요",
      description: "매도까지 평균 소요 기간",
      isPositive: true,
      icon: Clock,
      gradient: "from-indigo-500/20 to-indigo-500/5",
    },
    {
      label: "시뮬레이터 보유종목 누적수익률",
      value: formatPercent(summary.portfolio.total_profit || 0),
      change: `보유 ${summary.portfolio.total_stocks || 0}개 (평균 ${formatPercent(summary.portfolio.avg_profit_rate || 0)})`,
      changeValue: `슬롯 사용률 ${summary.portfolio.slot_usage}`,
      description: "현재 보유 종목 수익률 합계",
      isPositive: (summary.portfolio.total_profit || 0) >= 0,
      icon: Zap,
      gradient: "from-pink-500/20 to-pink-500/5",
    },
  ]

  return (
    <div className="space-y-4">
      {/* 실전투자 섹션 */}
      <div>
        <div className="flex items-center justify-between mb-3">
          <div className="flex items-center gap-2">
            <div className="h-1 w-8 rounded-full bg-gradient-to-r from-blue-500 to-indigo-500" />
            <h2 className="text-sm font-semibold text-muted-foreground">실전투자 (Season 2)</h2>
          </div>
          <div className="flex items-center gap-2">
            <span className="text-xs font-semibold text-blue-600 dark:text-blue-400">
              2025.09.29 시작
            </span>
            <span className="text-xs text-muted-foreground">
              ({daysElapsed}일 경과)
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

      {/* 시뮬레이터 섹션 */}
      <div>
        <div className="flex items-center justify-between mb-3">
          <div className="flex items-center gap-2">
            <div className="h-1 w-8 rounded-full bg-gradient-to-r from-purple-500 to-pink-500" />
            <h2 className="text-sm font-semibold text-muted-foreground">프리즘 시뮬레이터</h2>
          </div>
          <div className="flex items-center gap-2">
            <span className="text-xs font-semibold text-purple-600 dark:text-purple-400">
              2025.09.29 시작
            </span>
            <span className="text-xs text-muted-foreground">
              ({daysElapsed}일 경과)
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
