"use client"

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { History, TrendingUp, TrendingDown, Award, Calendar, Target, Brain, Trophy, AlertCircle, Scale, Percent, Flame, Zap, BarChart3, Activity, LineChart, Gauge, HelpCircle } from "lucide-react"
import type { Trade, Summary, PrismPerformance, MarketCondition, Market } from "@/types/dashboard"
import { useLanguage } from "@/components/language-provider"
import { formatCurrency as formatCurrencyUtil } from "@/lib/currency"

interface TradingHistoryPageProps {
  history: Trade[]
  summary: Summary
  prismPerformance?: PrismPerformance[]
  marketCondition?: MarketCondition[]
  market?: Market
}

export function TradingHistoryPage({ history, summary, prismPerformance = [], marketCondition = [], market = "KR" }: TradingHistoryPageProps) {
  const { t, language } = useLanguage()

  const formatCurrency = (value: number) => {
    return formatCurrencyUtil(value, market, language as "ko" | "en")
  }

  const formatPercent = (value: number) => {
    return `${value >= 0 ? "+" : ""}${value.toFixed(2)}%`
  }

  const formatDate = (dateString: string) => {
    const date = new Date(dateString)
    return date.toLocaleDateString(language === "ko" ? "ko-KR" : "en-US", {
      year: "numeric",
      month: "long",
      day: "numeric"
    })
  }

  const { total_trades, win_rate, avg_profit_rate, avg_holding_days } = summary.trading

  // 최고/최저 수익 종목
  const bestTrade = history.reduce((best, trade) => 
    (trade.profit_rate > best.profit_rate) ? trade : best
  , history[0] || { profit_rate: 0 })
  
  const worstTrade = history.reduce((worst, trade) => 
    (trade.profit_rate < worst.profit_rate) ? trade : worst
  , history[0] || { profit_rate: 0 })

  // 섹터별 수익률 (scenario.sector 활용)
  const sectorPerformance = history.reduce((acc, trade) => {
    const sector = trade.scenario?.sector || t("common.other")
    if (!acc[sector]) {
      acc[sector] = { total: 0, count: 0, avgProfit: 0 }
    }
    acc[sector].total += trade.profit_rate
    acc[sector].count += 1
    acc[sector].avgProfit = acc[sector].total / acc[sector].count
    return acc
  }, {} as Record<string, { total: number; count: number; avgProfit: number }>)

  const sortedSectors = Object.entries(sectorPerformance)
    .sort(([, a], [, b]) => b.avgProfit - a.avgProfit)
    .slice(0, 3)

  // 투자기간별 수익률 (수익/손실 분리)
  const periodPerformance = history.reduce((acc, trade) => {
    const period = trade.scenario?.investment_period || t("common.unclassified")
    if (!acc[period]) {
      acc[period] = {
        total: 0, count: 0, avgProfit: 0,
        winTotal: 0, winCount: 0, avgWin: 0,
        lossTotal: 0, lossCount: 0, avgLoss: 0
      }
    }
    acc[period].total += trade.profit_rate
    acc[period].count += 1
    acc[period].avgProfit = acc[period].total / acc[period].count

    if (trade.profit_rate >= 0) {
      acc[period].winTotal += trade.profit_rate
      acc[period].winCount += 1
      acc[period].avgWin = acc[period].winTotal / acc[period].winCount
    } else {
      acc[period].lossTotal += trade.profit_rate
      acc[period].lossCount += 1
      acc[period].avgLoss = acc[period].lossTotal / acc[period].lossCount
    }
    return acc
  }, {} as Record<string, {
    total: number; count: number; avgProfit: number;
    winTotal: number; winCount: number; avgWin: number;
    lossTotal: number; lossCount: number; avgLoss: number;
  }>)

  // 수익 거래와 손실 거래 분리
  const winningTrades = history.filter(t => t.profit_rate >= 0)
  const losingTrades = history.filter(t => t.profit_rate < 0)

  // 평균 수익률 (수익 거래만)
  const avgWinRate = winningTrades.length > 0
    ? winningTrades.reduce((sum, t) => sum + t.profit_rate, 0) / winningTrades.length
    : 0

  // 평균 손실률 (손실 거래만)
  const avgLossRate = losingTrades.length > 0
    ? losingTrades.reduce((sum, t) => sum + t.profit_rate, 0) / losingTrades.length
    : 0

  // Profit Factor (총 수익 / 총 손실의 절대값)
  const totalProfit = winningTrades.reduce((sum, t) => sum + t.profit_rate, 0)
  const totalLoss = Math.abs(losingTrades.reduce((sum, t) => sum + t.profit_rate, 0))
  const profitFactor = totalLoss > 0 ? totalProfit / totalLoss : totalProfit > 0 ? Infinity : 0

  // 손익비 (Risk/Reward Ratio) - 평균 수익 / 평균 손실의 절대값
  const riskRewardRatio = Math.abs(avgLossRate) > 0 ? avgWinRate / Math.abs(avgLossRate) : avgWinRate > 0 ? Infinity : 0

  // 최대 연속 승/패 계산
  let maxConsecutiveWins = 0
  let maxConsecutiveLosses = 0
  let currentWinStreak = 0
  let currentLossStreak = 0

  // 날짜순 정렬
  const sortedHistory = [...history].sort((a, b) =>
    new Date(a.sell_date).getTime() - new Date(b.sell_date).getTime()
  )

  sortedHistory.forEach(trade => {
    if (trade.profit_rate >= 0) {
      currentWinStreak++
      currentLossStreak = 0
      maxConsecutiveWins = Math.max(maxConsecutiveWins, currentWinStreak)
    } else {
      currentLossStreak++
      currentWinStreak = 0
      maxConsecutiveLosses = Math.max(maxConsecutiveLosses, currentLossStreak)
    }
  })

  // MDD (Maximum Drawdown) 계산
  const calculateMDD = (performances: PrismPerformance[]): number => {
    if (performances.length === 0) return 0

    let maxReturn = -Infinity
    let maxDrawdown = 0

    for (const perf of performances) {
      const currentReturn = perf.prism_simulator_return
      if (currentReturn > maxReturn) {
        maxReturn = currentReturn
      }
      const drawdown = maxReturn - currentReturn
      if (drawdown > maxDrawdown) {
        maxDrawdown = drawdown
      }
    }

    return maxDrawdown
  }

  const prismMDD = calculateMDD(prismPerformance)

  // 알파, 베타, 샤프 비율 계산
  const calculateRiskMetrics = () => {
    if (prismPerformance.length < 2 || marketCondition.length < 2) {
      return { alpha: 0, beta: 0, sharpeRatio: 0, informationRatio: 0 }
    }

    // Season2 시작일 기준 필터링
    const season2StartDate = '2025-09-29'
    const filteredMarket = marketCondition
      .filter(m => m.date >= season2StartDate)
      .sort((a, b) => new Date(a.date).getTime() - new Date(b.date).getTime())

    if (filteredMarket.length < 2) {
      return { alpha: 0, beta: 0, sharpeRatio: 0, informationRatio: 0 }
    }

    // 시작 시점 KOSPI 지수
    const startKospi = filteredMarket[0]?.kospi_index || 0
    if (startKospi === 0) {
      return { alpha: 0, beta: 0, sharpeRatio: 0, informationRatio: 0 }
    }

    // 프리즘 누적 수익률 맵핑
    const prismMap = new Map<string, number>()
    prismPerformance.forEach(p => {
      prismMap.set(p.date, p.prism_simulator_return)
    })

    // 일별 수익률 계산
    // 프리즘: 누적 수익률의 차이 (이미 % 단위)
    // 시장: KOSPI 지수의 일별 변화율 (% 단위)
    const prismDailyReturns: number[] = []
    const marketDailyReturns: number[] = []

    for (let i = 1; i < filteredMarket.length; i++) {
      const prevDate = filteredMarket[i - 1].date
      const currDate = filteredMarket[i].date

      // 프리즘: 누적 수익률의 일별 변화
      const prevPrism = prismMap.get(prevDate) || 0
      const currPrism = prismMap.get(currDate) || 0
      const prismDailyReturn = currPrism - prevPrism // 이미 % 단위

      // 시장: KOSPI 일별 수익률
      const prevKospi = filteredMarket[i - 1].kospi_index ?? 0
      const currKospi = filteredMarket[i].kospi_index ?? 0
      const marketDailyReturn = prevKospi > 0 ? ((currKospi - prevKospi) / prevKospi) * 100 : 0

      prismDailyReturns.push(prismDailyReturn)
      marketDailyReturns.push(marketDailyReturn)
    }

    if (prismDailyReturns.length === 0) {
      return { alpha: 0, beta: 0, sharpeRatio: 0, informationRatio: 0 }
    }

    // 누적 수익률 계산 (최종 값)
    const latestPrismReturn = prismPerformance[prismPerformance.length - 1]?.prism_simulator_return || 0
    const latestKospi = filteredMarket[filteredMarket.length - 1]?.kospi_index || startKospi
    const totalMarketReturn = ((latestKospi - startKospi) / startKospi) * 100

    // ============================================
    // 알파 (Alpha) - 단순 초과수익률
    // ============================================
    // 가장 직관적인 알파: 포트폴리오 수익률 - 벤치마크 수익률
    const alpha = latestPrismReturn - totalMarketReturn

    // ============================================
    // 베타 (Beta) - 시장 민감도
    // ============================================
    // 일별 수익률 기반 계산
    const avgPrismDaily = prismDailyReturns.reduce((a, b) => a + b, 0) / prismDailyReturns.length
    const avgMarketDaily = marketDailyReturns.reduce((a, b) => a + b, 0) / marketDailyReturns.length

    let covariance = 0
    let marketVariance = 0
    let prismVariance = 0

    for (let i = 0; i < prismDailyReturns.length; i++) {
      const prismDiff = prismDailyReturns[i] - avgPrismDaily
      const marketDiff = marketDailyReturns[i] - avgMarketDaily
      covariance += prismDiff * marketDiff
      marketVariance += marketDiff * marketDiff
      prismVariance += prismDiff * prismDiff
    }

    covariance /= prismDailyReturns.length
    marketVariance /= prismDailyReturns.length
    prismVariance /= prismDailyReturns.length

    // 베타 = Cov(포트폴리오, 시장) / Var(시장)
    const beta = marketVariance > 0 ? covariance / marketVariance : 0

    // ============================================
    // 샤프 비율 (Sharpe Ratio)
    // ============================================
    // 무위험 수익률: 연 3% 가정
    const annualRiskFreeRate = 3 // %
    const tradingDays = prismDailyReturns.length
    const periodRiskFreeRate = (tradingDays / 252) * annualRiskFreeRate // 기간 비례 무위험수익률

    // 프리즘 표준편차 (일별 수익률 기준)
    const prismStdDev = Math.sqrt(prismVariance)
    // 연환산 표준편차
    const annualizedPrismStdDev = prismStdDev * Math.sqrt(252)

    // 샤프 비율 = (연환산 수익률 - 연환산 무위험수익률) / 연환산 표준편차
    // 기간 수익률을 연환산
    const annualizedPrismReturn = (latestPrismReturn / tradingDays) * 252
    const sharpeRatio = annualizedPrismStdDev > 0
      ? (annualizedPrismReturn - annualRiskFreeRate) / annualizedPrismStdDev
      : 0

    // ============================================
    // 정보 비율 (Information Ratio)
    // ============================================
    // 추적 오차: 포트폴리오 수익률 - 벤치마크 수익률의 표준편차
    const trackingErrors: number[] = []
    for (let i = 0; i < prismDailyReturns.length; i++) {
      trackingErrors.push(prismDailyReturns[i] - marketDailyReturns[i])
    }

    const avgTrackingError = trackingErrors.reduce((a, b) => a + b, 0) / trackingErrors.length
    let trackingErrorVariance = 0
    for (const te of trackingErrors) {
      trackingErrorVariance += (te - avgTrackingError) ** 2
    }
    trackingErrorVariance /= trackingErrors.length
    const trackingErrorStdDev = Math.sqrt(trackingErrorVariance)

    // 정보 비율 = 연환산 초과수익률 / 연환산 추적오차
    const annualizedAlpha = (alpha / tradingDays) * 252
    const annualizedTrackingError = trackingErrorStdDev * Math.sqrt(252)
    const informationRatio = annualizedTrackingError > 0
      ? annualizedAlpha / annualizedTrackingError
      : 0

    // 데이터 기간 계산
    const startDate = filteredMarket[0]?.date || ''
    const endDate = filteredMarket[filteredMarket.length - 1]?.date || ''
    const dataPoints = filteredMarket.length

    return { alpha, beta, sharpeRatio, informationRatio, startDate, endDate, dataPoints }
  }

  const { alpha, beta, sharpeRatio, informationRatio, startDate: riskStartDate, endDate: riskEndDate, dataPoints } = calculateRiskMetrics()

  return (
    <div className="space-y-6">
      {/* 헤더 */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="p-3 rounded-lg bg-gradient-to-br from-blue-500/20 to-indigo-500/20">
            <History className="w-6 h-6 text-blue-600 dark:text-blue-400" />
          </div>
          <div>
            <h2 className="text-2xl font-bold text-foreground">{t("trading.title")}</h2>
            <p className="text-sm text-muted-foreground">{t("trading.description")}</p>
          </div>
        </div>
        <Badge variant="outline" className="text-sm">
          {t("trading.totalTrades")} {total_trades || 0}{t("trading.tradeCount")}
        </Badge>
      </div>

      {/* 통계 카드 */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
        <Card className="border-border/50">
          <CardContent className="p-6">
            <div className="flex items-center gap-3 mb-2">
              <History className="w-5 h-5 text-primary" />
              <span className="text-sm text-muted-foreground">{t("trading.totalTrades")}</span>
            </div>
            <p className="text-3xl font-bold text-foreground">{total_trades || 0}{t("trading.times")}</p>
            <p className="text-xs text-muted-foreground mt-1">
              {t("trading.completedTrades")}
            </p>
          </CardContent>
        </Card>

        <Card className="border-border/50">
          <CardContent className="p-6">
            <div className="flex items-center gap-3 mb-2">
              <Award className="w-5 h-5 text-success" />
              <span className="text-sm text-muted-foreground">{t("trading.winRate")}</span>
            </div>
            <p className="text-3xl font-bold text-success">{(win_rate || 0).toFixed(0)}%</p>
            <p className="text-xs text-muted-foreground mt-1">
              {t("trading.winningTradeRatio")}
            </p>
          </CardContent>
        </Card>

        <Card className="border-border/50">
          <CardContent className="p-6">
            <div className="flex items-center gap-3 mb-2">
              <TrendingUp className="w-5 h-5 text-success" />
              <span className="text-sm text-muted-foreground">{t("trading.avgWinRate")}</span>
            </div>
            <p className="text-3xl font-bold text-success">{formatPercent(avgWinRate)}</p>
            <p className="text-xs text-muted-foreground mt-1">
              {winningTrades.length}{t("trading.winningTradesAvg")}
            </p>
          </CardContent>
        </Card>

        <Card className="border-border/50">
          <CardContent className="p-6">
            <div className="flex items-center gap-3 mb-2">
              <TrendingDown className="w-5 h-5 text-destructive" />
              <span className="text-sm text-muted-foreground">{t("trading.avgLossRate")}</span>
            </div>
            <p className="text-3xl font-bold text-destructive">{formatPercent(avgLossRate)}</p>
            <p className="text-xs text-muted-foreground mt-1">
              {losingTrades.length}{t("trading.losingTradesAvg")}
            </p>
          </CardContent>
        </Card>
      </div>

      {/* 성과 지표 카드 */}
      <div className="grid grid-cols-1 md:grid-cols-5 gap-4">
        <Card className="border-border/50">
          <CardContent className="p-6">
            <div className="flex items-center gap-2 mb-2">
              <Scale className="w-5 h-5 text-primary" />
              <span className="text-sm text-muted-foreground">{t("trading.profitFactor")}</span>
              <Tooltip>
                <TooltipTrigger asChild>
                  <HelpCircle className="w-4 h-4 text-muted-foreground/50 cursor-help" />
                </TooltipTrigger>
                <TooltipContent className="max-w-xs p-3">
                  <p className="font-semibold mb-1">{t("trading.profitFactor")}</p>
                  <p className="text-xs mb-2">{t("trading.profitFactorTooltip")}</p>
                  <div className="text-xs space-y-1 border-t border-border/50 pt-2">
                    <p className="text-success">{t("trading.profitFactorGood")}</p>
                    <p className="text-yellow-500">{t("trading.profitFactorNeutral")}</p>
                    <p className="text-destructive">{t("trading.profitFactorBad")}</p>
                  </div>
                </TooltipContent>
              </Tooltip>
            </div>
            <p className={`text-3xl font-bold ${profitFactor >= 1.5 ? "text-success" : profitFactor >= 1 ? "text-yellow-500" : "text-destructive"}`}>
              {profitFactor === Infinity ? "∞" : profitFactor.toFixed(2)}
            </p>
            <p className="text-xs text-muted-foreground mt-1">
              {t("trading.profitFactorDesc")}
            </p>
          </CardContent>
        </Card>

        <Card className="border-border/50">
          <CardContent className="p-6">
            <div className="flex items-center gap-2 mb-2">
              <Percent className="w-5 h-5 text-chart-3" />
              <span className="text-sm text-muted-foreground">{t("trading.riskRewardRatio")}</span>
              <Tooltip>
                <TooltipTrigger asChild>
                  <HelpCircle className="w-4 h-4 text-muted-foreground/50 cursor-help" />
                </TooltipTrigger>
                <TooltipContent className="max-w-xs p-3">
                  <p className="font-semibold mb-1">{t("trading.riskRewardRatio")}</p>
                  <p className="text-xs mb-2">{t("trading.riskRewardTooltip")}</p>
                  <div className="text-xs space-y-1 border-t border-border/50 pt-2">
                    <p className="text-success">{t("trading.riskRewardGood")}</p>
                    <p className="text-yellow-500">{t("trading.riskRewardNeutral")}</p>
                    <p className="text-destructive">{t("trading.riskRewardBad")}</p>
                  </div>
                </TooltipContent>
              </Tooltip>
            </div>
            <p className={`text-3xl font-bold ${riskRewardRatio >= 2 ? "text-success" : riskRewardRatio >= 1 ? "text-yellow-500" : "text-destructive"}`}>
              {riskRewardRatio === Infinity ? "∞" : riskRewardRatio.toFixed(2)}
            </p>
            <p className="text-xs text-muted-foreground mt-1">
              {t("trading.riskRewardDesc")}
            </p>
          </CardContent>
        </Card>

        <Card className="border-border/50">
          <CardContent className="p-6">
            <div className="flex items-center gap-2 mb-2">
              <BarChart3 className="w-5 h-5 text-orange-600" />
              <span className="text-sm text-muted-foreground">{t("trading.mdd")}</span>
              <Tooltip>
                <TooltipTrigger asChild>
                  <HelpCircle className="w-4 h-4 text-muted-foreground/50 cursor-help" />
                </TooltipTrigger>
                <TooltipContent className="max-w-xs p-3">
                  <p className="font-semibold mb-1">{t("trading.mdd")}</p>
                  <p className="text-xs mb-2">{t("trading.mddTooltip")}</p>
                  <div className="text-xs space-y-1 border-t border-border/50 pt-2">
                    <p className="text-success">{t("trading.mddGood")}</p>
                    <p className="text-yellow-500">{t("trading.mddNeutral")}</p>
                    <p className="text-destructive">{t("trading.mddBad")}</p>
                  </div>
                </TooltipContent>
              </Tooltip>
            </div>
            <p className={`text-3xl font-bold ${prismMDD <= 10 ? "text-success" : prismMDD <= 20 ? "text-yellow-500" : "text-destructive"}`}>
              {prismMDD > 0 ? `-${prismMDD.toFixed(2)}%` : "0%"}
            </p>
            <p className="text-xs text-muted-foreground mt-1">
              {t("trading.mddDesc")}
            </p>
          </CardContent>
        </Card>

        <Card className="border-border/50">
          <CardContent className="p-6">
            <div className="flex items-center gap-3 mb-2">
              <Flame className="w-5 h-5 text-orange-500" />
              <span className="text-sm text-muted-foreground">{t("trading.maxWinStreak")}</span>
            </div>
            <p className="text-3xl font-bold text-orange-500">{maxConsecutiveWins}{t("trading.consecutive")}</p>
            <p className="text-xs text-muted-foreground mt-1">
              {t("trading.maxWinStreakDesc")}
            </p>
          </CardContent>
        </Card>

        <Card className="border-border/50">
          <CardContent className="p-6">
            <div className="flex items-center gap-3 mb-2">
              <Calendar className="w-5 h-5 text-chart-4" />
              <span className="text-sm text-muted-foreground">{t("trading.avgHoldingDays")}</span>
            </div>
            <p className="text-3xl font-bold text-chart-4">{(avg_holding_days || 0).toFixed(0)}{t("common.days")}</p>
            <p className="text-xs text-muted-foreground mt-1">
              {t("trading.daysFromBuyToSell")}
            </p>
          </CardContent>
        </Card>
      </div>

      {/* 위험조정 성과 지표 (알파, 베타, 샤프비율, 정보비율) */}
      {(prismPerformance.length > 0 && marketCondition.length > 0 && dataPoints > 0) && (
        <>
        <div className="flex items-center justify-between">
          <h3 className="text-lg font-semibold text-foreground">{t("trading.riskAdjustedMetrics")}</h3>
          {riskStartDate && riskEndDate && (
            <span className="text-xs text-muted-foreground">
              {t("trading.dataPeriod")}: {riskStartDate} ~ {riskEndDate} ({dataPoints}{t("common.days")})
            </span>
          )}
        </div>
        <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
          <Card className="border-border/50 bg-gradient-to-br from-indigo-500/5 to-transparent">
            <CardContent className="p-6">
              <div className="flex items-center gap-2 mb-2">
                <Activity className="w-5 h-5 text-indigo-600" />
                <span className="text-sm text-muted-foreground">{t("trading.alpha")}</span>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <HelpCircle className="w-4 h-4 text-muted-foreground/50 cursor-help" />
                  </TooltipTrigger>
                  <TooltipContent className="max-w-xs p-3">
                    <p className="font-semibold mb-1">{t("trading.alpha")}</p>
                    <p className="text-xs mb-2">{t("trading.alphaTooltip")}</p>
                    <div className="text-xs space-y-1 border-t border-border/50 pt-2">
                      <p className="text-success">{t("trading.alphaGood")}</p>
                      <p className="text-destructive">{t("trading.alphaBad")}</p>
                    </div>
                  </TooltipContent>
                </Tooltip>
              </div>
              <p className={`text-3xl font-bold ${alpha >= 0 ? "text-success" : "text-destructive"}`}>
                {alpha >= 0 ? "+" : ""}{alpha.toFixed(2)}%
              </p>
              <p className="text-xs text-muted-foreground mt-1">
                {t("trading.alphaDesc")}
              </p>
            </CardContent>
          </Card>

          <Card className="border-border/50 bg-gradient-to-br from-cyan-500/5 to-transparent">
            <CardContent className="p-6">
              <div className="flex items-center gap-2 mb-2">
                <LineChart className="w-5 h-5 text-cyan-600" />
                <span className="text-sm text-muted-foreground">{t("trading.beta")}</span>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <HelpCircle className="w-4 h-4 text-muted-foreground/50 cursor-help" />
                  </TooltipTrigger>
                  <TooltipContent className="max-w-xs p-3">
                    <p className="font-semibold mb-1">{t("trading.beta")}</p>
                    <p className="text-xs mb-2">{t("trading.betaTooltip")}</p>
                    <div className="text-xs space-y-1 border-t border-border/50 pt-2">
                      <p className="text-cyan-600">{t("trading.betaLow")}</p>
                      <p className="text-foreground">{t("trading.betaNeutral")}</p>
                      <p className="text-orange-600">{t("trading.betaHigh")}</p>
                    </div>
                  </TooltipContent>
                </Tooltip>
              </div>
              <p className={`text-3xl font-bold ${beta <= 1 ? "text-cyan-600" : "text-orange-600"}`}>
                {beta.toFixed(2)}
              </p>
              <p className="text-xs text-muted-foreground mt-1">
                {t("trading.betaDesc")}
              </p>
            </CardContent>
          </Card>

          <Card className="border-border/50 bg-gradient-to-br from-violet-500/5 to-transparent">
            <CardContent className="p-6">
              <div className="flex items-center gap-2 mb-2">
                <Gauge className="w-5 h-5 text-violet-600" />
                <span className="text-sm text-muted-foreground">{t("trading.sharpeRatio")}</span>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <HelpCircle className="w-4 h-4 text-muted-foreground/50 cursor-help" />
                  </TooltipTrigger>
                  <TooltipContent className="max-w-xs p-3">
                    <p className="font-semibold mb-1">{t("trading.sharpeRatio")}</p>
                    <p className="text-xs mb-2">{t("trading.sharpeTooltip")}</p>
                    <div className="text-xs space-y-1 border-t border-border/50 pt-2">
                      <p className="text-success">{t("trading.sharpeGood")}</p>
                      <p className="text-violet-600">{t("trading.sharpeNeutral")}</p>
                      <p className="text-destructive">{t("trading.sharpeBad")}</p>
                    </div>
                  </TooltipContent>
                </Tooltip>
              </div>
              <p className={`text-3xl font-bold ${sharpeRatio >= 1 ? "text-success" : sharpeRatio >= 0 ? "text-violet-600" : "text-destructive"}`}>
                {sharpeRatio.toFixed(2)}
              </p>
              <p className="text-xs text-muted-foreground mt-1">
                {t("trading.sharpeDesc")}
              </p>
            </CardContent>
          </Card>

          <Card className="border-border/50 bg-gradient-to-br from-teal-500/5 to-transparent">
            <CardContent className="p-6">
              <div className="flex items-center gap-2 mb-2">
                <Target className="w-5 h-5 text-teal-600" />
                <span className="text-sm text-muted-foreground">{t("trading.informationRatio")}</span>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <HelpCircle className="w-4 h-4 text-muted-foreground/50 cursor-help" />
                  </TooltipTrigger>
                  <TooltipContent className="max-w-xs p-3">
                    <p className="font-semibold mb-1">{t("trading.informationRatio")}</p>
                    <p className="text-xs mb-2">{t("trading.irTooltip")}</p>
                    <div className="text-xs space-y-1 border-t border-border/50 pt-2">
                      <p className="text-success">{t("trading.irGood")}</p>
                      <p className="text-teal-600">{t("trading.irNeutral")}</p>
                      <p className="text-destructive">{t("trading.irBad")}</p>
                    </div>
                  </TooltipContent>
                </Tooltip>
              </div>
              <p className={`text-3xl font-bold ${informationRatio >= 0.5 ? "text-success" : informationRatio >= 0 ? "text-teal-600" : "text-destructive"}`}>
                {informationRatio.toFixed(2)}
              </p>
              <p className="text-xs text-muted-foreground mt-1">
                {t("trading.informationRatioDesc")}
              </p>
            </CardContent>
          </Card>
        </div>
        </>
      )}

      {/* 베스트/워스트 거래 */}
      {history.length > 0 && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <Card className="border-border/50 bg-gradient-to-br from-success/5 to-transparent">
            <CardHeader>
              <CardTitle className="text-base font-semibold flex items-center gap-2">
                <Trophy className="w-5 h-5 text-success" />
                {t("trading.bestTrade")}
              </CardTitle>
            </CardHeader>
            <CardContent>
              <div className="space-y-3">
                <div>
                  <p className="text-lg font-bold text-foreground">{bestTrade.company_name}</p>
                  <p className="text-sm text-muted-foreground">{bestTrade.ticker}</p>
                </div>
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <p className="text-xs text-muted-foreground mb-1">{t("trading.profitRate")}</p>
                    <p className="text-xl font-bold text-success">{formatPercent(bestTrade.profit_rate)}</p>
                  </div>
                  <div>
                    <p className="text-xs text-muted-foreground mb-1">{t("trading.holdingPeriod")}</p>
                    <p className="text-xl font-bold text-foreground">{bestTrade.holding_days}{t("common.days")}</p>
                  </div>
                </div>
                <div className="pt-2 border-t border-border/30">
                  <p className="text-xs text-muted-foreground">
                    {formatDate(bestTrade.buy_date)} → {formatDate(bestTrade.sell_date)}
                  </p>
                </div>
              </div>
            </CardContent>
          </Card>

          <Card className="border-border/50 bg-gradient-to-br from-destructive/5 to-transparent">
            <CardHeader>
              <CardTitle className="text-base font-semibold flex items-center gap-2">
                <AlertCircle className="w-5 h-5 text-destructive" />
                {t("trading.worstTrade")}
              </CardTitle>
            </CardHeader>
            <CardContent>
              <div className="space-y-3">
                <div>
                  <p className="text-lg font-bold text-foreground">{worstTrade.company_name}</p>
                  <p className="text-sm text-muted-foreground">{worstTrade.ticker}</p>
                </div>
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <p className="text-xs text-muted-foreground mb-1">{t("trading.profitRate")}</p>
                    <p className="text-xl font-bold text-destructive">{formatPercent(worstTrade.profit_rate)}</p>
                  </div>
                  <div>
                    <p className="text-xs text-muted-foreground mb-1">{t("trading.holdingPeriod")}</p>
                    <p className="text-xl font-bold text-foreground">{worstTrade.holding_days}{t("common.days")}</p>
                  </div>
                </div>
                <div className="pt-2 border-t border-border/30">
                  <p className="text-xs text-muted-foreground">
                    {formatDate(worstTrade.buy_date)} → {formatDate(worstTrade.sell_date)}
                  </p>
                </div>
              </div>
            </CardContent>
          </Card>
        </div>
      )}

      {/* 섹터별 & 기간별 성과 */}
      {history.length > 0 && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {sortedSectors.length > 0 && (
            <Card className="border-border/50">
              <CardHeader>
                <CardTitle className="text-base font-semibold flex items-center gap-2">
                  <TrendingUp className="w-5 h-5 text-primary" />
                  {t("trading.sectorPerformanceTop3")}
                </CardTitle>
              </CardHeader>
              <CardContent>
                <div className="space-y-3">
                  {sortedSectors.map(([sector, data], index) => (
                    <div key={sector} className="flex items-center justify-between p-3 rounded-lg bg-muted/30">
                      <div className="flex items-center gap-3">
                        <div className="flex items-center justify-center w-8 h-8 rounded-full bg-primary/20 text-primary font-bold text-sm">
                          {index + 1}
                        </div>
                        <div>
                          <p className="font-medium text-foreground">{sector}</p>
                          <p className="text-xs text-muted-foreground">{data.count}{t("trading.tradeCount")}</p>
                        </div>
                      </div>
                      <p className={`text-lg font-bold ${data.avgProfit >= 0 ? "text-success" : "text-destructive"}`}>
                        {formatPercent(data.avgProfit)}
                      </p>
                    </div>
                  ))}
                </div>
              </CardContent>
            </Card>
          )}

          {Object.keys(periodPerformance).length > 0 && (
            <Card className="border-border/50">
              <CardHeader>
                <CardTitle className="text-base font-semibold flex items-center gap-2">
                  <Calendar className="w-5 h-5 text-chart-3" />
                  {t("trading.periodPerformance")}
                </CardTitle>
              </CardHeader>
              <CardContent>
                <div className="space-y-3">
                  {Object.entries(periodPerformance).map(([period, data]) => (
                    <div key={period} className="p-3 rounded-lg bg-muted/30">
                      <div className="flex items-center justify-between mb-2">
                        <div>
                          <p className="font-medium text-foreground">{period}</p>
                          <p className="text-xs text-muted-foreground">{data.count}{t("trading.tradeCount")}</p>
                        </div>
                        <p className={`text-lg font-bold ${data.avgProfit >= 0 ? "text-success" : "text-destructive"}`}>
                          {formatPercent(data.avgProfit)}
                        </p>
                      </div>
                      <div className="flex gap-4 text-xs">
                        <div className="flex items-center gap-1">
                          <span className="text-success">▲</span>
                          <span className="text-muted-foreground">{t("trading.avgWinShort")}:</span>
                          <span className="font-medium text-success">
                            {data.winCount > 0 ? formatPercent(data.avgWin) : "-"}
                          </span>
                          <span className="text-muted-foreground">({data.winCount}{t("trading.tradeCountShort")})</span>
                        </div>
                        <div className="flex items-center gap-1">
                          <span className="text-destructive">▼</span>
                          <span className="text-muted-foreground">{t("trading.avgLossShort")}:</span>
                          <span className="font-medium text-destructive">
                            {data.lossCount > 0 ? formatPercent(data.avgLoss) : "-"}
                          </span>
                          <span className="text-muted-foreground">({data.lossCount}{t("trading.tradeCountShort")})</span>
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              </CardContent>
            </Card>
          )}
        </div>
      )}

      {/* 거래 상세 내역 */}
      <Card className="border-border/50">
        <CardHeader>
          <CardTitle className="text-lg font-semibold">{t("trading.detailedHistory")}</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="space-y-4">
            {history.map((trade) => (
              <Card key={trade.id} className="border-border/30 bg-muted/20">
                <CardContent className="p-6">
                  <div className="space-y-4">
                    {/* 종목 헤더 */}
                    <div className="flex items-start justify-between">
                      <div className="flex-1">
                        <div className="flex items-center gap-3 mb-2">
                          <h3 className="text-lg font-bold text-foreground">{trade.company_name}</h3>
                          <Badge variant="outline" className="text-xs">{trade.ticker}</Badge>
                          {trade.scenario?.sector && (
                            <Badge variant="secondary" className="text-xs">{trade.scenario.sector}</Badge>
                          )}
                          {trade.scenario?.investment_period && (
                            <Badge variant="secondary" className="text-xs">{trade.scenario.investment_period}</Badge>
                          )}
                        </div>
                        <p className="text-sm text-muted-foreground">
                          {formatDate(trade.buy_date)} → {formatDate(trade.sell_date)} ({trade.holding_days}{t("trading.daysHeld")})
                        </p>
                      </div>
                      <div className="text-right">
                        <p className="text-sm text-muted-foreground mb-1">{t("trading.profitRate")}</p>
                        <p className={`text-2xl font-bold ${trade.profit_rate >= 0 ? "text-success" : "text-destructive"}`}>
                          {formatPercent(trade.profit_rate)}
                        </p>
                      </div>
                    </div>

                    {/* 거래 정보 */}
                    <div className="grid grid-cols-2 md:grid-cols-4 gap-4 p-4 rounded-lg bg-background border border-border/50">
                      <div>
                        <p className="text-xs text-muted-foreground mb-1">{t("trading.buyPrice")}</p>
                        <p className="font-semibold text-foreground">{formatCurrency(trade.buy_price)}</p>
                      </div>
                      <div>
                        <p className="text-xs text-muted-foreground mb-1">{t("trading.sellPrice")}</p>
                        <p className="font-semibold text-foreground">{formatCurrency(trade.sell_price)}</p>
                      </div>
                      <div>
                        <p className="text-xs text-muted-foreground mb-1">{t("trading.aiTargetPrice")}</p>
                        <p className="font-semibold text-success">
                          {trade.scenario?.target_price ? formatCurrency(trade.scenario.target_price) : "-"}
                        </p>
                      </div>
                      <div>
                        <p className="text-xs text-muted-foreground mb-1">{t("trading.targetAchievementRate")}</p>
                        <p className="font-semibold text-foreground">
                          {trade.scenario?.target_price
                            ? `${((trade.sell_price / trade.scenario.target_price) * 100).toFixed(0)}%`
                            : "-"}
                        </p>
                      </div>
                    </div>

                    {/* AI 시나리오 */}
                    {trade.scenario && (
                      <div className="space-y-3">
                        {/* 투자 근거 */}
                        <div className="p-4 rounded-lg bg-primary/10 border border-primary/20">
                          <div className="flex items-start gap-2">
                            <Brain className="w-4 h-4 text-primary mt-0.5 flex-shrink-0" />
                            <div className="flex-1">
                              <p className="text-sm font-semibold text-primary mb-2">{t("trading.aiInvestmentRationale")}</p>
                              {trade.scenario.rationale && (
                                <p className="text-sm text-foreground leading-relaxed">{trade.scenario.rationale}</p>
                              )}
                            </div>
                          </div>
                        </div>

                        {/* 포트폴리오 분석 */}
                        {trade.scenario.portfolio_analysis && (
                          <div className="p-4 rounded-lg bg-muted/30 border border-border/50">
                            <p className="text-xs font-semibold text-muted-foreground mb-2">{t("modal.portfolioAnalysis")}</p>
                            <p className="text-sm text-foreground leading-relaxed">{trade.scenario.portfolio_analysis}</p>
                          </div>
                        )}

                        {/* 밸류에이션 분석 */}
                        {trade.scenario.valuation_analysis && (
                          <div className="p-4 rounded-lg bg-muted/30 border border-border/50">
                            <p className="text-xs font-semibold text-muted-foreground mb-2">{t("modal.valuationAnalysis")}</p>
                            <p className="text-sm text-foreground leading-relaxed">{trade.scenario.valuation_analysis}</p>
                          </div>
                        )}

                        {/* 섹터 전망 */}
                        {trade.scenario.sector_outlook && (
                          <div className="p-4 rounded-lg bg-muted/30 border border-border/50">
                            <p className="text-xs font-semibold text-muted-foreground mb-2">{t("modal.sectorOutlook")}</p>
                            <p className="text-sm text-foreground leading-relaxed">{trade.scenario.sector_outlook}</p>
                          </div>
                        )}

                        {/* 시장 상황 */}
                        {trade.scenario.market_condition && (
                          <div className="p-4 rounded-lg bg-muted/30 border border-border/50">
                            <p className="text-xs font-semibold text-muted-foreground mb-2">{t("trading.pastMarketCondition")}</p>
                            <p className="text-sm text-foreground leading-relaxed">{trade.scenario.market_condition}</p>
                          </div>
                        )}

                        {/* 매매 시나리오 상세 */}
                        {trade.scenario.trading_scenarios && (
                          <div className="p-4 rounded-lg bg-chart-1/10 border border-chart-1/20">
                            <p className="text-sm font-semibold text-chart-1 mb-3">{t("trading.aiScenarioDetails")}</p>

                            {/* 주요 레벨 */}
                            {trade.scenario.trading_scenarios.key_levels && (
                              <div className="mb-4">
                                <p className="text-xs font-semibold text-muted-foreground mb-2">{t("modal.keyPriceLevels")}</p>
                                <div className="grid grid-cols-2 gap-2 text-xs">
                                  {trade.scenario.trading_scenarios.key_levels.primary_support && (
                                    <div className="p-2 rounded bg-background/50">
                                      <span className="text-muted-foreground">{t("modal.primarySupportShort")}: </span>
                                      <span className="font-medium text-foreground">{trade.scenario.trading_scenarios.key_levels.primary_support}</span>
                                    </div>
                                  )}
                                  {trade.scenario.trading_scenarios.key_levels.secondary_support && (
                                    <div className="p-2 rounded bg-background/50">
                                      <span className="text-muted-foreground">{t("modal.secondarySupportShort")}: </span>
                                      <span className="font-medium text-foreground">{trade.scenario.trading_scenarios.key_levels.secondary_support}</span>
                                    </div>
                                  )}
                                  {trade.scenario.trading_scenarios.key_levels.primary_resistance && (
                                    <div className="p-2 rounded bg-background/50">
                                      <span className="text-muted-foreground">{t("modal.primaryResistanceShort")}: </span>
                                      <span className="font-medium text-foreground">{trade.scenario.trading_scenarios.key_levels.primary_resistance}</span>
                                    </div>
                                  )}
                                  {trade.scenario.trading_scenarios.key_levels.secondary_resistance && (
                                    <div className="p-2 rounded bg-background/50">
                                      <span className="text-muted-foreground">{t("modal.secondaryResistanceShort")}: </span>
                                      <span className="font-medium text-foreground">{trade.scenario.trading_scenarios.key_levels.secondary_resistance}</span>
                                    </div>
                                  )}
                                </div>
                              </div>
                            )}

                            {/* 매도 트리거 */}
                            {trade.scenario.trading_scenarios.sell_triggers && trade.scenario.trading_scenarios.sell_triggers.length > 0 && (
                              <div className="mb-4">
                                <p className="text-xs font-semibold text-muted-foreground mb-2">{t("modal.sellTriggers")}</p>
                                <ul className="space-y-1.5">
                                  {trade.scenario.trading_scenarios.sell_triggers.map((trigger, idx) => (
                                    <li key={idx} className="text-xs text-foreground leading-relaxed pl-3 relative before:content-['•'] before:absolute before:left-0 before:text-chart-1">
                                      {trigger}
                                    </li>
                                  ))}
                                </ul>
                              </div>
                            )}

                            {/* 보유 조건 */}
                            {trade.scenario.trading_scenarios.hold_conditions && trade.scenario.trading_scenarios.hold_conditions.length > 0 && (
                              <div className="mb-4">
                                <p className="text-xs font-semibold text-muted-foreground mb-2">{t("modal.holdConditions")}</p>
                                <ul className="space-y-1.5">
                                  {trade.scenario.trading_scenarios.hold_conditions.map((condition, idx) => (
                                    <li key={idx} className="text-xs text-foreground leading-relaxed pl-3 relative before:content-['•'] before:absolute before:left-0 before:text-success">
                                      {condition}
                                    </li>
                                  ))}
                                </ul>
                              </div>
                            )}

                            {/* 포트폴리오 맥락 */}
                            {trade.scenario.trading_scenarios.portfolio_context && (
                              <div>
                                <p className="text-xs font-semibold text-muted-foreground mb-2">{t("modal.portfolioContext")}</p>
                                <p className="text-xs text-foreground leading-relaxed">{trade.scenario.trading_scenarios.portfolio_context}</p>
                              </div>
                            )}
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                </CardContent>
              </Card>
            ))}
          </div>
        </CardContent>
      </Card>

      {history.length === 0 && (
        <Card className="border-border/50">
          <CardContent className="p-12 text-center">
            <History className="w-12 h-12 text-muted-foreground mx-auto mb-4 opacity-50" />
            <p className="text-muted-foreground">{t("trading.noData")}</p>
          </CardContent>
        </Card>
      )}
    </div>
  )
}
