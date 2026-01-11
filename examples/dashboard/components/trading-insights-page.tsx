"use client"

import { useState } from "react"
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from "@/components/ui/accordion"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import {
  Lightbulb,
  BookOpen,
  Brain,
  TrendingUp,
  TrendingDown,
  AlertCircle,
  CheckCircle,
  Target,
  Zap,
  BarChart3,
  ArrowUpRight,
  ArrowDownRight,
  Timer,
  Eye,
  ShoppingCart,
  Trophy,
  XCircle,
  HelpCircle
} from "lucide-react"
import { useLanguage } from "@/components/language-provider"
import type { TradingInsightsData, TradingPrinciple, TradingJournal, TradingIntuition, SituationAnalysis, JudgmentEvaluation } from "@/types/dashboard"

interface TradingInsightsPageProps {
  data: TradingInsightsData
}

// Helper to safely parse JSON
function tryParseJSON<T>(str: string | T): T | null {
  if (typeof str !== 'string') return str as T
  try {
    return JSON.parse(str) as T
  } catch {
    return null
  }
}

export function TradingInsightsPage({ data }: TradingInsightsPageProps) {
  const { t, language } = useLanguage()

  const formatDate = (dateString: string) => {
    if (!dateString) return "-"
    const date = new Date(dateString)
    return date.toLocaleDateString(language === "ko" ? "ko-KR" : "en-US", {
      year: "numeric",
      month: "short",
      day: "numeric",
    })
  }

  const formatPercent = (value: number) => {
    if (value === null || value === undefined) return "-"
    // value is in decimal form (0.07 = 7%), multiply by 100 for display
    const percentage = value * 100
    const sign = percentage >= 0 ? "+" : ""
    return `${sign}${percentage.toFixed(2)}%`
  }

  // For values already in percent form (e.g., 35.57 = 35.57%)
  const formatPercentDirect = (value: number) => {
    if (value === null || value === undefined) return "-"
    const sign = value >= 0 ? "+" : ""
    return `${sign}${value.toFixed(2)}%`
  }

  const getPriorityColor = (priority: string) => {
    switch (priority) {
      case "high":
        return "bg-red-500/10 text-red-600 dark:text-red-400 border-red-500/20"
      case "medium":
        return "bg-yellow-500/10 text-yellow-600 dark:text-yellow-400 border-yellow-500/20"
      case "low":
        return "bg-green-500/10 text-green-600 dark:text-green-400 border-green-500/20"
      default:
        return "bg-gray-500/10 text-gray-600 dark:text-gray-400"
    }
  }

  const getScopeColor = (scope: string) => {
    switch (scope) {
      case "universal":
        return "bg-purple-500/10 text-purple-600 dark:text-purple-400 border-purple-500/20"
      case "sector":
        return "bg-blue-500/10 text-blue-600 dark:text-blue-400 border-blue-500/20"
      case "market":
        return "bg-cyan-500/10 text-cyan-600 dark:text-cyan-400 border-cyan-500/20"
      default:
        return "bg-gray-500/10 text-gray-600 dark:text-gray-400"
    }
  }

  const getConfidenceBar = (confidence: number) => {
    const percentage = Math.round(confidence * 100)
    return (
      <div className="flex items-center gap-2">
        <div className="flex-1 h-2 bg-muted rounded-full overflow-hidden">
          <div
            className={`h-full rounded-full ${
              percentage >= 70 ? "bg-green-500" :
              percentage >= 40 ? "bg-yellow-500" :
              "bg-red-500"
            }`}
            style={{ width: `${percentage}%` }}
          />
        </div>
        <span className="text-xs text-muted-foreground w-12">{percentage}%</span>
      </div>
    )
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="p-3 rounded-lg bg-gradient-to-br from-amber-500/20 to-yellow-500/20">
            <Lightbulb className="w-6 h-6 text-amber-600 dark:text-amber-400" />
          </div>
          <div>
            <h2 className="text-2xl font-bold text-foreground">{t("insights.title")}</h2>
            <p className="text-sm text-muted-foreground">{t("insights.description")}</p>
          </div>
        </div>
      </div>

      {/* Summary Cards - 4 key metrics */}
      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
        <Card>
          <CardContent className="pt-6">
            <div className="flex items-center gap-2">
              <Target className="w-4 h-4 text-purple-500" />
              <span className="text-sm text-muted-foreground">{t("insights.summary.totalPrinciples")}</span>
            </div>
            <p className="text-2xl font-bold mt-2">{data.summary.total_principles}</p>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="pt-6">
            <div className="flex items-center gap-2">
              <AlertCircle className="w-4 h-4 text-red-500" />
              <span className="text-sm text-muted-foreground">{t("insights.summary.highPriority")}</span>
            </div>
            <p className="text-2xl font-bold mt-2">{data.summary.high_priority_count}</p>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="pt-6">
            <div className="flex items-center gap-2">
              <Brain className="w-4 h-4 text-cyan-500" />
              <span className="text-sm text-muted-foreground">{t("insights.summary.totalIntuitions")}</span>
            </div>
            <p className="text-2xl font-bold mt-2">{data.summary.total_intuitions}</p>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="pt-6">
            <div className="flex items-center gap-2">
              <Zap className="w-4 h-4 text-yellow-500" />
              <span className="text-sm text-muted-foreground">{t("insights.summary.avgConfidence")}</span>
            </div>
            <p className="text-2xl font-bold mt-2">{(data.summary.avg_confidence * 100).toFixed(0)}%</p>
          </CardContent>
        </Card>
      </div>

      {/* Performance Analysis Section */}
      {data.performance_analysis && (
        <Card className="border-2 border-blue-500/20">
          <CardHeader>
            <div className="flex items-center gap-2">
              <div className="p-2 rounded-lg bg-gradient-to-br from-blue-500/20 to-cyan-500/20">
                <BarChart3 className="w-5 h-5 text-blue-600 dark:text-blue-400" />
              </div>
              <div>
                <CardTitle>{t("insights.performance.title")}</CardTitle>
                <CardDescription>{t("insights.performance.description")}</CardDescription>
              </div>
            </div>
          </CardHeader>
          <CardContent className="space-y-6">
            {data.performance_analysis.overview.completed === 0 ? (
              <div className="flex flex-col items-center justify-center py-12">
                <BarChart3 className="w-12 h-12 text-muted-foreground mb-4" />
                <p className="text-muted-foreground text-center">{t("insights.performance.noData")}</p>
                <p className="text-sm text-muted-foreground mt-2">{t("insights.performance.noDataHint")}</p>
              </div>
            ) : (
              <TooltipProvider>
                {/* Overview Cards */}
                <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
                  <div className="p-3 rounded-lg bg-muted/50 border">
                    <div className="flex items-center gap-2">
                      <BarChart3 className="w-4 h-4 text-blue-500" />
                      <span className="text-xs text-muted-foreground">{t("insights.performance.total")}</span>
                    </div>
                    <p className="text-xl font-bold mt-1">{data.performance_analysis.overview.total}</p>
                  </div>
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <div className="p-3 rounded-lg bg-green-500/10 border border-green-500/20 cursor-help">
                        <div className="flex items-center gap-2">
                          <CheckCircle className="w-4 h-4 text-green-500" />
                          <span className="text-xs text-muted-foreground">{t("insights.performance.completed")}</span>
                          <HelpCircle className="w-3 h-3 text-muted-foreground" />
                        </div>
                        <p className="text-xl font-bold mt-1 text-green-600">{data.performance_analysis.overview.completed}</p>
                      </div>
                    </TooltipTrigger>
                    <TooltipContent className="max-w-xs">
                      <p>{t("insights.performance.tooltip.completed")}</p>
                    </TooltipContent>
                  </Tooltip>
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <div className="p-3 rounded-lg bg-yellow-500/10 border border-yellow-500/20 cursor-help">
                        <div className="flex items-center gap-2">
                          <Timer className="w-4 h-4 text-yellow-500" />
                          <span className="text-xs text-muted-foreground">{t("insights.performance.pending")}</span>
                          <HelpCircle className="w-3 h-3 text-muted-foreground" />
                        </div>
                        <p className="text-xl font-bold mt-1 text-yellow-600">{data.performance_analysis.overview.pending}</p>
                      </div>
                    </TooltipTrigger>
                    <TooltipContent className="max-w-xs">
                      <p>{t("insights.performance.tooltip.pending")}</p>
                    </TooltipContent>
                  </Tooltip>
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <div className="p-3 rounded-lg bg-orange-500/10 border border-orange-500/20 cursor-help">
                        <div className="flex items-center gap-2">
                          <Timer className="w-4 h-4 text-orange-500" />
                          <span className="text-xs text-muted-foreground">{t("insights.performance.inProgress")}</span>
                          <HelpCircle className="w-3 h-3 text-muted-foreground" />
                        </div>
                        <p className="text-xl font-bold mt-1 text-orange-600">{data.performance_analysis.overview.in_progress}</p>
                      </div>
                    </TooltipTrigger>
                    <TooltipContent className="max-w-xs">
                      <p>{t("insights.performance.tooltip.inProgress")}</p>
                    </TooltipContent>
                  </Tooltip>
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <div className="p-3 rounded-lg bg-purple-500/10 border border-purple-500/20 cursor-help">
                        <div className="flex items-center gap-2">
                          <ShoppingCart className="w-4 h-4 text-purple-500" />
                          <span className="text-xs text-muted-foreground">{t("insights.performance.traded")}</span>
                          <HelpCircle className="w-3 h-3 text-muted-foreground" />
                        </div>
                        <p className="text-xl font-bold mt-1 text-purple-600">{data.performance_analysis.overview.traded_count}</p>
                      </div>
                    </TooltipTrigger>
                    <TooltipContent className="max-w-xs">
                      <p>{t("insights.performance.tooltip.traded")}</p>
                    </TooltipContent>
                  </Tooltip>
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <div className="p-3 rounded-lg bg-cyan-500/10 border border-cyan-500/20 cursor-help">
                        <div className="flex items-center gap-2">
                          <Eye className="w-4 h-4 text-cyan-500" />
                          <span className="text-xs text-muted-foreground">{t("insights.performance.watched")}</span>
                          <HelpCircle className="w-3 h-3 text-muted-foreground" />
                        </div>
                        <p className="text-xl font-bold mt-1 text-cyan-600">{data.performance_analysis.overview.watched_count}</p>
                      </div>
                    </TooltipTrigger>
                    <TooltipContent className="max-w-xs">
                      <p>{t("insights.performance.tooltip.watched")}</p>
                    </TooltipContent>
                  </Tooltip>
                </div>

                {/* Trigger Type Performance */}
                {data.performance_analysis.trigger_performance.length > 0 && (
                  <div className="space-y-3">
                    <h4 className="text-sm font-medium flex items-center gap-2">
                      <Zap className="w-4 h-4 text-yellow-500" />
                      {t("insights.performance.triggerType")}
                    </h4>
                    <div className="overflow-x-auto">
                      <table className="w-full text-sm">
                        <thead>
                          <tr className="border-b">
                            <th className="text-left py-2 px-3 font-medium text-muted-foreground">
                              {language === "ko" ? "트리거" : "Trigger"}
                            </th>
                            <th className="text-center py-2 px-3 font-medium text-muted-foreground">{t("insights.performance.count")}</th>
                            <th className="text-center py-2 px-3 font-medium text-muted-foreground">
                              <Tooltip>
                                <TooltipTrigger className="flex items-center gap-1 justify-center cursor-help">
                                  {t("insights.performance.tradedRate")}
                                  <HelpCircle className="w-3 h-3" />
                                </TooltipTrigger>
                                <TooltipContent className="max-w-xs">
                                  <p>{t("insights.performance.tooltip.tradedRate")}</p>
                                </TooltipContent>
                              </Tooltip>
                            </th>
                            <th className="text-center py-2 px-3 font-medium text-muted-foreground">
                              <Tooltip>
                                <TooltipTrigger className="flex items-center gap-1 justify-center cursor-help">
                                  {t("insights.performance.day7")}
                                  <HelpCircle className="w-3 h-3" />
                                </TooltipTrigger>
                                <TooltipContent className="max-w-xs">
                                  <p>{t("insights.performance.tooltip.dayReturn")}</p>
                                </TooltipContent>
                              </Tooltip>
                            </th>
                            <th className="text-center py-2 px-3 font-medium text-muted-foreground">
                              <Tooltip>
                                <TooltipTrigger className="flex items-center gap-1 justify-center cursor-help">
                                  {t("insights.performance.day14")}
                                  <HelpCircle className="w-3 h-3" />
                                </TooltipTrigger>
                                <TooltipContent className="max-w-xs">
                                  <p>{t("insights.performance.tooltip.dayReturn")}</p>
                                </TooltipContent>
                              </Tooltip>
                            </th>
                            <th className="text-center py-2 px-3 font-medium text-muted-foreground">
                              <Tooltip>
                                <TooltipTrigger className="flex items-center gap-1 justify-center cursor-help">
                                  {t("insights.performance.day30")}
                                  <HelpCircle className="w-3 h-3" />
                                </TooltipTrigger>
                                <TooltipContent className="max-w-xs">
                                  <p>{t("insights.performance.tooltip.dayReturn")}</p>
                                </TooltipContent>
                              </Tooltip>
                            </th>
                            <th className="text-center py-2 px-3 font-medium text-muted-foreground">
                              <Tooltip>
                                <TooltipTrigger className="flex items-center gap-1 justify-center cursor-help">
                                  {t("insights.performance.winRate")}
                                  <HelpCircle className="w-3 h-3" />
                                </TooltipTrigger>
                                <TooltipContent className="max-w-xs">
                                  <p>{t("insights.performance.tooltip.winRate")}</p>
                                </TooltipContent>
                              </Tooltip>
                            </th>
                          </tr>
                        </thead>
                        <tbody>
                          {data.performance_analysis.trigger_performance.map((trigger, idx) => (
                            <tr key={idx} className="border-b hover:bg-muted/50">
                              <td className="py-2 px-3 font-medium">{trigger.trigger_type}</td>
                              <td className="py-2 px-3 text-center">{trigger.count}</td>
                              <td className="py-2 px-3 text-center">
                                <Badge variant="outline" className="text-xs">
                                  {(trigger.traded_rate * 100).toFixed(0)}%
                                </Badge>
                              </td>
                              <td className={`py-2 px-3 text-center ${
                                trigger.avg_7d_return !== null && trigger.avg_7d_return >= 0 ? "text-green-600" : "text-red-600"
                              }`}>
                                {trigger.avg_7d_return !== null ? formatPercent(trigger.avg_7d_return) : "-"}
                              </td>
                              <td className={`py-2 px-3 text-center ${
                                trigger.avg_14d_return !== null && trigger.avg_14d_return >= 0 ? "text-green-600" : "text-red-600"
                              }`}>
                                {trigger.avg_14d_return !== null ? formatPercent(trigger.avg_14d_return) : "-"}
                              </td>
                              <td className={`py-2 px-3 text-center ${
                                trigger.avg_30d_return !== null && trigger.avg_30d_return >= 0 ? "text-green-600" : "text-red-600"
                              }`}>
                                {trigger.avg_30d_return !== null ? formatPercent(trigger.avg_30d_return) : "-"}
                              </td>
                              <td className="py-2 px-3 text-center">
                                {trigger.win_rate_30d !== null ? (
                                  <Badge variant={trigger.win_rate_30d >= 0.5 ? "default" : "destructive"} className="text-xs">
                                    {(trigger.win_rate_30d * 100).toFixed(0)}%
                                  </Badge>
                                ) : "-"}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </div>
                )}

                {/* Actual Trading Performance - 실제 매매 성과 */}
                {data.performance_analysis.traded_vs_watched?.actual_trading && (
                  <div className="space-y-3">
                    <h4 className="text-sm font-medium flex items-center gap-2">
                      <TrendingUp className="w-4 h-4 text-green-500" />
                      {language === "ko" ? "실제 매매 성과 (최근 30일)" : "Actual Trading (Last 30 Days)"}
                    </h4>
                    <div className="p-4 rounded-lg bg-gradient-to-r from-green-500/10 to-emerald-500/10 border border-green-500/20">
                      <div className="flex items-center justify-between mb-3">
                        <span className="font-medium text-green-700 dark:text-green-400">
                          {data.performance_analysis.traded_vs_watched.actual_trading.count || 0}{language === "ko" ? "건 완료" : " trades"}
                        </span>
                      </div>
                      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-sm">
                        {/* 평균 수익률 */}
                        <div>
                          <span className="text-muted-foreground text-xs">{language === "ko" ? "평균 수익률" : "Avg Return"}</span>
                          <p className={`font-bold text-lg ${
                            (data.performance_analysis.traded_vs_watched.actual_trading.avg_profit_rate || 0) >= 0 ? "text-green-600" : "text-red-600"
                          }`}>
                            {formatPercent(data.performance_analysis.traded_vs_watched.actual_trading.avg_profit_rate)}
                          </p>
                        </div>
                        {/* 승률 */}
                        <div>
                          <span className="text-muted-foreground text-xs">{language === "ko" ? "승률" : "Win Rate"}</span>
                          <p className="font-bold text-lg">
                            {data.performance_analysis.traded_vs_watched.actual_trading.win_rate !== null
                              ? `${(data.performance_analysis.traded_vs_watched.actual_trading.win_rate * 100).toFixed(0)}%`
                              : "-"}
                            <span className="text-xs font-normal text-muted-foreground ml-1">
                              ({data.performance_analysis.traded_vs_watched.actual_trading.win_count || 0}W/{data.performance_analysis.traded_vs_watched.actual_trading.loss_count || 0}L)
                            </span>
                          </p>
                        </div>
                        {/* 평균 수익 (수익건) */}
                        <div>
                          <span className="text-muted-foreground text-xs">{language === "ko" ? "평균 수익 (수익건)" : "Avg Profit (wins)"}</span>
                          <p className="font-bold text-green-600">
                            {formatPercent(data.performance_analysis.traded_vs_watched.actual_trading.avg_profit)}
                          </p>
                        </div>
                        {/* 평균 손실 (손실건) */}
                        <div>
                          <span className="text-muted-foreground text-xs">{language === "ko" ? "평균 손실 (손실건)" : "Avg Loss (losses)"}</span>
                          <p className="font-bold text-red-600">
                            {formatPercent(data.performance_analysis.traded_vs_watched.actual_trading.avg_loss)}
                          </p>
                        </div>
                        {/* 최대 수익 */}
                        <div>
                          <span className="text-muted-foreground text-xs">{language === "ko" ? "최대 수익" : "Max Profit"}</span>
                          <p className="font-bold text-green-600">
                            {formatPercent(data.performance_analysis.traded_vs_watched.actual_trading.max_profit)}
                          </p>
                        </div>
                        {/* 최대 손실 */}
                        <div>
                          <span className="text-muted-foreground text-xs">{language === "ko" ? "최대 손실" : "Max Loss"}</span>
                          <p className="font-bold text-red-600">
                            {formatPercent(data.performance_analysis.traded_vs_watched.actual_trading.max_loss)}
                          </p>
                        </div>
                        {/* Profit Factor */}
                        <div>
                          <span className="text-muted-foreground text-xs">Profit Factor</span>
                          <p className={`font-bold ${
                            data.performance_analysis.traded_vs_watched.actual_trading.profit_factor !== null &&
                            data.performance_analysis.traded_vs_watched.actual_trading.profit_factor >= 1 ? "text-green-600" : "text-red-600"
                          }`}>
                            {data.performance_analysis.traded_vs_watched.actual_trading.profit_factor !== null
                              ? data.performance_analysis.traded_vs_watched.actual_trading.profit_factor.toFixed(2)
                              : "-"}
                          </p>
                        </div>
                      </div>
                    </div>
                  </div>
                )}

                {/* Traded vs Watched Comparison - 분석 후 가격 추적 */}
                {data.performance_analysis.traded_vs_watched && (
                  <div className="space-y-3">
                    <div className="flex items-center gap-2">
                      <h4 className="text-sm font-medium flex items-center gap-2">
                        <Target className="w-4 h-4 text-purple-500" />
                        {t("insights.performance.tradedVsWatched")}
                      </h4>
                      <span className="text-xs text-muted-foreground">
                        ({language === "ko" ? "분석일 기준 가격 변동" : "Price change from analysis date"})
                      </span>
                    </div>
                    <div className="grid md:grid-cols-2 gap-4">
                      {/* Traded */}
                      <div className="p-4 rounded-lg bg-purple-500/5 border border-purple-500/20">
                        <div className="flex items-center gap-2 mb-3">
                          <ShoppingCart className="w-5 h-5 text-purple-600" />
                          <span className="font-medium text-purple-700 dark:text-purple-400">
                            {t("insights.performance.traded")} ({data.performance_analysis.traded_vs_watched.traded.count || 0})
                          </span>
                        </div>
                        <div className="grid grid-cols-2 gap-3 text-sm">
                          {/* 승률 */}
                          <div>
                            <span className="text-muted-foreground">{t("insights.performance.winRate")}</span>
                            <p className="font-bold">
                              {data.performance_analysis.traded_vs_watched.traded.win_rate !== null
                                ? `${(data.performance_analysis.traded_vs_watched.traded.win_rate * 100).toFixed(0)}%`
                                : "-"}
                              <span className="text-xs font-normal text-muted-foreground ml-1">
                                ({data.performance_analysis.traded_vs_watched.traded.win_count || 0}/{data.performance_analysis.traded_vs_watched.traded.loss_count || 0})
                              </span>
                            </p>
                          </div>
                          {/* Profit Factor */}
                          <div>
                            <span className="text-muted-foreground">Profit Factor</span>
                            <p className={`font-bold ${
                              data.performance_analysis.traded_vs_watched.traded.profit_factor !== null &&
                              data.performance_analysis.traded_vs_watched.traded.profit_factor >= 1 ? "text-green-600" : "text-red-600"
                            }`}>
                              {data.performance_analysis.traded_vs_watched.traded.profit_factor !== null
                                ? data.performance_analysis.traded_vs_watched.traded.profit_factor.toFixed(2)
                                : "-"}
                            </p>
                          </div>
                          {/* 평균 수익률 (수익건) */}
                          <div>
                            <span className="text-muted-foreground text-xs">{language === "ko" ? "평균 수익 (수익건)" : "Avg Profit (wins)"}</span>
                            <p className="font-bold text-green-600">
                              {data.performance_analysis.traded_vs_watched.traded.avg_profit !== null
                                ? formatPercent(data.performance_analysis.traded_vs_watched.traded.avg_profit)
                                : "-"}
                            </p>
                          </div>
                          {/* 평균 손실률 (손실건) */}
                          <div>
                            <span className="text-muted-foreground text-xs">{language === "ko" ? "평균 손실 (손실건)" : "Avg Loss (losses)"}</span>
                            <p className="font-bold text-red-600">
                              {data.performance_analysis.traded_vs_watched.traded.avg_loss !== null
                                ? formatPercent(data.performance_analysis.traded_vs_watched.traded.avg_loss)
                                : "-"}
                            </p>
                          </div>
                          {/* 최대 수익 */}
                          <div>
                            <span className="text-muted-foreground text-xs">{language === "ko" ? "최대 수익" : "Max Profit"}</span>
                            <p className="font-bold text-green-600">
                              {data.performance_analysis.traded_vs_watched.traded.max_profit !== null
                                ? formatPercent(data.performance_analysis.traded_vs_watched.traded.max_profit)
                                : "-"}
                            </p>
                          </div>
                          {/* 최대 손실 */}
                          <div>
                            <span className="text-muted-foreground text-xs">{language === "ko" ? "최대 손실" : "Max Loss"}</span>
                            <p className="font-bold text-red-600">
                              {data.performance_analysis.traded_vs_watched.traded.max_loss !== null
                                ? formatPercent(data.performance_analysis.traded_vs_watched.traded.max_loss)
                                : "-"}
                            </p>
                          </div>
                        </div>
                      </div>
                      {/* Watched */}
                      <div className="p-4 rounded-lg bg-cyan-500/5 border border-cyan-500/20">
                        <div className="flex items-center gap-2 mb-3">
                          <Eye className="w-5 h-5 text-cyan-600" />
                          <span className="font-medium text-cyan-700 dark:text-cyan-400">
                            {t("insights.performance.watched")} ({data.performance_analysis.traded_vs_watched.watched.count || 0})
                          </span>
                        </div>
                        <div className="grid grid-cols-2 gap-3 text-sm">
                          {/* 승률 */}
                          <div>
                            <span className="text-muted-foreground">{t("insights.performance.winRate")}</span>
                            <p className="font-bold">
                              {data.performance_analysis.traded_vs_watched.watched.win_rate !== null
                                ? `${(data.performance_analysis.traded_vs_watched.watched.win_rate * 100).toFixed(0)}%`
                                : "-"}
                              <span className="text-xs font-normal text-muted-foreground ml-1">
                                ({data.performance_analysis.traded_vs_watched.watched.win_count || 0}/{data.performance_analysis.traded_vs_watched.watched.loss_count || 0})
                              </span>
                            </p>
                          </div>
                          {/* Profit Factor */}
                          <div>
                            <span className="text-muted-foreground">Profit Factor</span>
                            <p className={`font-bold ${
                              data.performance_analysis.traded_vs_watched.watched.profit_factor !== null &&
                              data.performance_analysis.traded_vs_watched.watched.profit_factor >= 1 ? "text-green-600" : "text-red-600"
                            }`}>
                              {data.performance_analysis.traded_vs_watched.watched.profit_factor !== null
                                ? data.performance_analysis.traded_vs_watched.watched.profit_factor.toFixed(2)
                                : "-"}
                            </p>
                          </div>
                          {/* 평균 수익률 (수익건) */}
                          <div>
                            <span className="text-muted-foreground text-xs">{language === "ko" ? "평균 수익 (수익건)" : "Avg Profit (wins)"}</span>
                            <p className="font-bold text-green-600">
                              {data.performance_analysis.traded_vs_watched.watched.avg_profit !== null
                                ? formatPercent(data.performance_analysis.traded_vs_watched.watched.avg_profit)
                                : "-"}
                            </p>
                          </div>
                          {/* 평균 손실률 (손실건) */}
                          <div>
                            <span className="text-muted-foreground text-xs">{language === "ko" ? "평균 손실 (손실건)" : "Avg Loss (losses)"}</span>
                            <p className="font-bold text-red-600">
                              {data.performance_analysis.traded_vs_watched.watched.avg_loss !== null
                                ? formatPercent(data.performance_analysis.traded_vs_watched.watched.avg_loss)
                                : "-"}
                            </p>
                          </div>
                          {/* 최대 수익 */}
                          <div>
                            <span className="text-muted-foreground text-xs">{language === "ko" ? "최대 수익" : "Max Profit"}</span>
                            <p className="font-bold text-green-600">
                              {data.performance_analysis.traded_vs_watched.watched.max_profit !== null
                                ? formatPercent(data.performance_analysis.traded_vs_watched.watched.max_profit)
                                : "-"}
                            </p>
                          </div>
                          {/* 최대 손실 */}
                          <div>
                            <span className="text-muted-foreground text-xs">{language === "ko" ? "최대 손실" : "Max Loss"}</span>
                            <p className="font-bold text-red-600">
                              {data.performance_analysis.traded_vs_watched.watched.max_loss !== null
                                ? formatPercent(data.performance_analysis.traded_vs_watched.watched.max_loss)
                                : "-"}
                            </p>
                          </div>
                        </div>
                      </div>
                    </div>
                    {/* T-Test Result */}
                    {data.performance_analysis.traded_vs_watched.t_test && (
                      <div className="flex items-center gap-2 mt-2">
                        <Badge
                          variant={data.performance_analysis.traded_vs_watched.t_test.significant ? "default" : "secondary"}
                          className="text-xs"
                        >
                          p={data.performance_analysis.traded_vs_watched.t_test.p_value.toFixed(3)}
                        </Badge>
                        <span className="text-xs text-muted-foreground">
                          {data.performance_analysis.traded_vs_watched.t_test.significant
                            ? t("insights.performance.significant")
                            : t("insights.performance.notSignificant")}
                        </span>
                      </div>
                    )}
                  </div>
                )}

                {/* Risk/Reward Threshold Analysis */}
                {data.performance_analysis.rr_threshold_analysis.length > 0 && (
                  <div className="space-y-3">
                    <h4 className="text-sm font-medium flex items-center gap-2">
                      <BarChart3 className="w-4 h-4 text-blue-500" />
                      {t("insights.performance.rrAnalysis")}
                    </h4>
                    <div className="overflow-x-auto">
                      <table className="w-full text-sm">
                        <thead>
                          <tr className="border-b">
                            <th className="text-left py-2 px-3 font-medium text-muted-foreground">{t("insights.performance.range")}</th>
                            <th className="text-center py-2 px-3 font-medium text-muted-foreground">{t("insights.performance.count")}</th>
                            <th className="text-center py-2 px-3 font-medium text-muted-foreground">{t("insights.performance.traded")}</th>
                            <th className="text-center py-2 px-3 font-medium text-muted-foreground">{t("insights.performance.watched")}</th>
                            <th className="text-center py-2 px-3 font-medium text-muted-foreground">{t("insights.performance.allAvg")}</th>
                            <th className="text-center py-2 px-3 font-medium text-muted-foreground">{t("insights.performance.watchedAvg")}</th>
                          </tr>
                        </thead>
                        <tbody>
                          {data.performance_analysis.rr_threshold_analysis.map((rr, idx) => (
                            <tr key={idx} className="border-b hover:bg-muted/50">
                              <td className="py-2 px-3 font-medium">{rr.range}</td>
                              <td className="py-2 px-3 text-center">{rr.total_count}</td>
                              <td className="py-2 px-3 text-center text-purple-600">{rr.traded_count}</td>
                              <td className="py-2 px-3 text-center text-cyan-600">{rr.watched_count}</td>
                              <td className={`py-2 px-3 text-center ${
                                rr.avg_all_return !== null && rr.avg_all_return >= 0 ? "text-green-600" : "text-red-600"
                              }`}>
                                {rr.avg_all_return !== null ? formatPercent(rr.avg_all_return) : "-"}
                              </td>
                              <td className={`py-2 px-3 text-center ${
                                rr.avg_watched_return !== null && rr.avg_watched_return >= 0 ? "text-green-600" : "text-red-600"
                              }`}>
                                {rr.avg_watched_return !== null ? formatPercent(rr.avg_watched_return) : "-"}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </div>
                )}

                {/* Missed Opportunities & Avoided Losses */}
                <div className="grid md:grid-cols-2 gap-4">
                  {/* Missed Opportunities */}
                  {data.performance_analysis.missed_opportunities.length > 0 && (
                    <div className="space-y-3">
                      <h4 className="text-sm font-medium flex items-center gap-2">
                        <XCircle className="w-4 h-4 text-red-500" />
                        {t("insights.performance.missedOpportunities")}
                        <Badge variant="destructive" className="text-xs">
                          {data.performance_analysis.missed_opportunities.length}
                        </Badge>
                      </h4>
                      <div className="space-y-2 max-h-[300px] overflow-y-auto">
                        {data.performance_analysis.missed_opportunities.map((opp, idx) => (
                          <div key={idx} className="p-3 rounded-lg bg-red-500/5 border border-red-500/20">
                            <div className="flex items-center justify-between">
                              <div>
                                <span className="font-medium">{opp.company_name}</span>
                                <span className="text-muted-foreground text-sm ml-2">({opp.ticker})</span>
                              </div>
                              <Badge variant="outline" className="bg-green-500/10 text-green-600 border-green-500/20">
                                {formatPercent(opp.tracked_30d_return)}
                              </Badge>
                            </div>
                            <div className="mt-2 text-xs text-muted-foreground grid grid-cols-2 gap-2">
                              <div>
                                <span>{t("insights.performance.skipReason")}: </span>
                                <span className="text-red-600">{opp.skip_reason}</span>
                              </div>
                              <div>
                                <span>{language === "ko" ? "트리거" : "Trigger"}: </span>
                                <span>{opp.trigger_type}</span>
                              </div>
                            </div>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}

                  {/* Avoided Losses */}
                  {data.performance_analysis.avoided_losses.length > 0 && (
                    <div className="space-y-3">
                      <h4 className="text-sm font-medium flex items-center gap-2">
                        <Trophy className="w-4 h-4 text-green-500" />
                        {t("insights.performance.avoidedLosses")}
                        <Badge variant="default" className="bg-green-500 text-xs">
                          {data.performance_analysis.avoided_losses.length}
                        </Badge>
                      </h4>
                      <div className="space-y-2 max-h-[300px] overflow-y-auto">
                        {data.performance_analysis.avoided_losses.map((loss, idx) => (
                          <div key={idx} className="p-3 rounded-lg bg-green-500/5 border border-green-500/20">
                            <div className="flex items-center justify-between">
                              <div>
                                <span className="font-medium">{loss.company_name}</span>
                                <span className="text-muted-foreground text-sm ml-2">({loss.ticker})</span>
                              </div>
                              <Badge variant="outline" className="bg-red-500/10 text-red-600 border-red-500/20">
                                {formatPercent(loss.tracked_30d_return)}
                              </Badge>
                            </div>
                            <div className="mt-2 text-xs text-muted-foreground grid grid-cols-2 gap-2">
                              <div>
                                <span>{t("insights.performance.skipReason")}: </span>
                                <span className="text-green-600">{loss.skip_reason}</span>
                              </div>
                              <div>
                                <span>{language === "ko" ? "트리거" : "Trigger"}: </span>
                                <span>{loss.trigger_type}</span>
                              </div>
                            </div>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                </div>

                {/* Recommendations */}
                {data.performance_analysis.recommendations.length > 0 && (
                  <div className="space-y-3">
                    <h4 className="text-sm font-medium flex items-center gap-2">
                      <Lightbulb className="w-4 h-4 text-amber-500" />
                      {t("insights.performance.recommendations")}
                    </h4>
                    <div className="p-4 rounded-lg bg-amber-500/5 border border-amber-500/20">
                      <ul className="space-y-2">
                        {data.performance_analysis.recommendations.map((rec, idx) => (
                          <li key={idx} className="flex items-start gap-2 text-sm">
                            <span className="text-amber-500 mt-0.5">•</span>
                            <span>{rec}</span>
                          </li>
                        ))}
                      </ul>
                    </div>
                  </div>
                )}
              </TooltipProvider>
            )}
          </CardContent>
        </Card>
      )}

      {/* Principles Section */}
      <Card>
        <CardHeader>
          <div className="flex items-center gap-2">
            <Target className="w-5 h-5 text-purple-500" />
            <CardTitle>{t("insights.principles")}</CardTitle>
          </div>
          <CardDescription>{t("insights.principlesDescription")}</CardDescription>
        </CardHeader>
        <CardContent>
          {data.principles.length === 0 ? (
            <p className="text-muted-foreground text-center py-8">{t("insights.noPrinciples")}</p>
          ) : (
            <div className="space-y-4">
              {data.principles.map((principle) => (
                <div
                  key={principle.id}
                  className="p-4 rounded-lg border bg-card hover:bg-muted/50 transition-colors"
                >
                  <div className="flex items-start justify-between gap-4">
                    <div className="flex-1 space-y-2">
                      <div className="flex items-center gap-2 flex-wrap">
                        <Badge variant="outline" className={getPriorityColor(principle.priority)}>
                          {t(`insights.priority.${principle.priority}`)}
                        </Badge>
                        <Badge variant="outline" className={getScopeColor(principle.scope)}>
                          {t(`insights.scope.${principle.scope}`)}
                          {principle.scope_context && `: ${principle.scope_context}`}
                        </Badge>
                      </div>
                      <div className="space-y-1">
                        <p className="font-medium">
                          <span className="text-muted-foreground">{t("insights.condition")}:</span>{" "}
                          {principle.condition}
                        </p>
                        <p className="text-primary">
                          <span className="text-muted-foreground">{t("insights.action")}:</span>{" "}
                          {principle.action}
                        </p>
                        {principle.reason && (
                          <p className="text-sm text-muted-foreground">
                            <span>{t("insights.reason")}:</span> {principle.reason}
                          </p>
                        )}
                      </div>
                    </div>
                    <div className="text-right space-y-1 min-w-[120px]">
                      <div className="text-sm">
                        <span className="text-muted-foreground">{t("insights.confidence")}:</span>
                      </div>
                      {getConfidenceBar(principle.confidence)}
                      <div className="text-xs text-muted-foreground">
                        {t("insights.supportingTrades")}: {principle.supporting_trades}
                      </div>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      {/* Journal Section */}
      <Card>
        <CardHeader>
          <div className="flex items-center gap-2">
            <BookOpen className="w-5 h-5 text-blue-500" />
            <CardTitle>{t("insights.journal")}</CardTitle>
          </div>
          <CardDescription>{t("insights.journalDescription")}</CardDescription>
        </CardHeader>
        <CardContent>
          {data.journal_entries.length === 0 ? (
            <p className="text-muted-foreground text-center py-8">{t("insights.noJournal")}</p>
          ) : (
            <Accordion type="single" collapsible className="w-full">
              {data.journal_entries.map((entry) => (
                <AccordionItem key={entry.id} value={`journal-${entry.id}`}>
                  <AccordionTrigger className="hover:no-underline">
                    <div className="flex items-center justify-between w-full pr-4">
                      <div className="flex items-center gap-3">
                        <div className={`w-2 h-2 rounded-full ${
                          entry.profit_rate >= 0 ? "bg-green-500" : "bg-red-500"
                        }`} />
                        <span className="font-medium">{entry.company_name}</span>
                        <span className="text-muted-foreground text-sm">({entry.ticker})</span>
                      </div>
                      <div className="flex items-center gap-4">
                        <span className={`font-medium ${
                          entry.profit_rate >= 0 ? "text-green-600" : "text-red-600"
                        }`}>
                          {formatPercentDirect(entry.profit_rate)}
                        </span>
                        <span className="text-sm text-muted-foreground">
                          {formatDate(entry.trade_date)}
                        </span>
                        <Badge variant="outline" className="text-xs">
                          L{entry.compression_layer}
                        </Badge>
                      </div>
                    </div>
                  </AccordionTrigger>
                  <AccordionContent>
                    <div className="space-y-4 pt-2">
                      {/* One-line Summary */}
                      {entry.one_line_summary && (
                        <div className="p-3 rounded-lg bg-muted/50">
                          <p className="font-medium">{entry.one_line_summary}</p>
                        </div>
                      )}

                      {/* Trade Details */}
                      <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
                        <div>
                          <span className="text-muted-foreground">{t("insights.tradeDate")}</span>
                          <p className="font-medium">{formatDate(entry.trade_date)}</p>
                        </div>
                        <div>
                          <span className="text-muted-foreground">{t("insights.holdingDays")}</span>
                          <p className="font-medium">{entry.holding_days}{language === "ko" ? "일" : " days"}</p>
                        </div>
                        <div>
                          <span className="text-muted-foreground">{t("insights.profitRate")}</span>
                          <p className={`font-medium ${entry.profit_rate >= 0 ? "text-green-600" : "text-red-600"}`}>
                            {formatPercentDirect(entry.profit_rate)}
                          </p>
                        </div>
                        <div>
                          <span className="text-muted-foreground">{t("insights.layer")}</span>
                          <p className="font-medium">Layer {entry.compression_layer}</p>
                        </div>
                      </div>

                      {/* Situation Analysis */}
                      {entry.situation_analysis && (() => {
                        const parsed = tryParseJSON<SituationAnalysis>(entry.situation_analysis)
                        if (!parsed) return (
                          <div>
                            <h4 className="text-sm font-medium text-muted-foreground mb-1">
                              {t("insights.situationAnalysis")}
                            </h4>
                            <p className="text-sm">{entry.situation_analysis}</p>
                          </div>
                        )
                        return (
                          <div className="space-y-3">
                            <h4 className="text-sm font-medium text-muted-foreground">
                              {t("insights.situationAnalysis")}
                            </h4>
                            <div className="grid gap-3 text-sm">
                              {parsed.buy_context_summary && (
                                <div className="p-3 rounded-lg bg-green-500/5 border border-green-500/10">
                                  <div className="flex items-center gap-2 mb-1">
                                    <TrendingUp className="w-4 h-4 text-green-600" />
                                    <span className="font-medium text-green-700 dark:text-green-400">
                                      {language === "ko" ? "매수 컨텍스트" : "Buy Context"}
                                    </span>
                                  </div>
                                  <p className="text-muted-foreground">{parsed.buy_context_summary}</p>
                                </div>
                              )}
                              {parsed.sell_context_summary && (
                                <div className="p-3 rounded-lg bg-red-500/5 border border-red-500/10">
                                  <div className="flex items-center gap-2 mb-1">
                                    <TrendingDown className="w-4 h-4 text-red-600" />
                                    <span className="font-medium text-red-700 dark:text-red-400">
                                      {language === "ko" ? "매도 컨텍스트" : "Sell Context"}
                                    </span>
                                  </div>
                                  <p className="text-muted-foreground">{parsed.sell_context_summary}</p>
                                </div>
                              )}
                              {(parsed.market_at_buy || parsed.market_at_sell) && (
                                <div className="grid md:grid-cols-2 gap-3">
                                  {parsed.market_at_buy && (
                                    <div className="p-2 rounded bg-muted/30">
                                      <span className="text-xs text-muted-foreground">{language === "ko" ? "매수시점 시장" : "Market at Buy"}</span>
                                      <p className="text-sm">{parsed.market_at_buy}</p>
                                    </div>
                                  )}
                                  {parsed.market_at_sell && (
                                    <div className="p-2 rounded bg-muted/30">
                                      <span className="text-xs text-muted-foreground">{language === "ko" ? "매도시점 시장" : "Market at Sell"}</span>
                                      <p className="text-sm">{parsed.market_at_sell}</p>
                                    </div>
                                  )}
                                </div>
                              )}
                              {parsed.key_changes && parsed.key_changes.length > 0 && (
                                <div className="p-3 rounded-lg bg-blue-500/5 border border-blue-500/10">
                                  <div className="flex items-center gap-2 mb-2">
                                    <Zap className="w-4 h-4 text-blue-600" />
                                    <span className="font-medium text-blue-700 dark:text-blue-400">
                                      {language === "ko" ? "핵심 변화" : "Key Changes"}
                                    </span>
                                  </div>
                                  <ul className="space-y-1 text-muted-foreground">
                                    {parsed.key_changes.map((change, i) => (
                                      <li key={i} className="flex items-start gap-2">
                                        <span className="text-blue-500 mt-1">•</span>
                                        <span>{change}</span>
                                      </li>
                                    ))}
                                  </ul>
                                </div>
                              )}
                            </div>
                          </div>
                        )
                      })()}

                      {/* Judgment Evaluation */}
                      {entry.judgment_evaluation && (() => {
                        const parsed = tryParseJSON<JudgmentEvaluation>(entry.judgment_evaluation)
                        if (!parsed) return (
                          <div>
                            <h4 className="text-sm font-medium text-muted-foreground mb-1">
                              {t("insights.judgmentEvaluation")}
                            </h4>
                            <p className="text-sm">{entry.judgment_evaluation}</p>
                          </div>
                        )
                        return (
                          <div className="space-y-3">
                            <h4 className="text-sm font-medium text-muted-foreground">
                              {t("insights.judgmentEvaluation")}
                            </h4>
                            <div className="grid md:grid-cols-2 gap-3 text-sm">
                              {parsed.buy_quality && (
                                <div className="p-3 rounded-lg bg-muted/30">
                                  <div className="flex items-center gap-2 mb-1">
                                    <Badge variant="outline" className={
                                      parsed.buy_quality === "적절" || parsed.buy_quality === "Good"
                                        ? "bg-green-500/10 text-green-600 border-green-500/20"
                                        : "bg-yellow-500/10 text-yellow-600 border-yellow-500/20"
                                    }>
                                      {language === "ko" ? "매수" : "Buy"}: {parsed.buy_quality}
                                    </Badge>
                                  </div>
                                  {parsed.buy_quality_reason && (
                                    <p className="text-muted-foreground text-xs mt-2">{parsed.buy_quality_reason}</p>
                                  )}
                                </div>
                              )}
                              {parsed.sell_quality && (
                                <div className="p-3 rounded-lg bg-muted/30">
                                  <div className="flex items-center gap-2 mb-1">
                                    <Badge variant="outline" className={
                                      parsed.sell_quality === "적절" || parsed.sell_quality === "Good"
                                        ? "bg-green-500/10 text-green-600 border-green-500/20"
                                        : "bg-yellow-500/10 text-yellow-600 border-yellow-500/20"
                                    }>
                                      {language === "ko" ? "매도" : "Sell"}: {parsed.sell_quality}
                                    </Badge>
                                  </div>
                                  {parsed.sell_quality_reason && (
                                    <p className="text-muted-foreground text-xs mt-2">{parsed.sell_quality_reason}</p>
                                  )}
                                </div>
                              )}
                            </div>
                            {parsed.missed_signals && parsed.missed_signals.length > 0 && (
                              <div className="p-3 rounded-lg bg-orange-500/5 border border-orange-500/10">
                                <div className="flex items-center gap-2 mb-2">
                                  <AlertCircle className="w-4 h-4 text-orange-600" />
                                  <span className="font-medium text-orange-700 dark:text-orange-400 text-sm">
                                    {language === "ko" ? "놓친 신호" : "Missed Signals"}
                                  </span>
                                </div>
                                <ul className="space-y-1 text-xs text-muted-foreground">
                                  {parsed.missed_signals.map((signal, i) => (
                                    <li key={i} className="flex items-start gap-2">
                                      <span className="text-orange-500 mt-0.5">•</span>
                                      <span>{signal}</span>
                                    </li>
                                  ))}
                                </ul>
                              </div>
                            )}
                            {parsed.overreacted_signals && parsed.overreacted_signals.length > 0 && (
                              <div className="p-3 rounded-lg bg-purple-500/5 border border-purple-500/10">
                                <div className="flex items-center gap-2 mb-2">
                                  <Target className="w-4 h-4 text-purple-600" />
                                  <span className="font-medium text-purple-700 dark:text-purple-400 text-sm">
                                    {language === "ko" ? "과잉 반응 신호" : "Overreacted Signals"}
                                  </span>
                                </div>
                                <ul className="space-y-1 text-xs text-muted-foreground">
                                  {parsed.overreacted_signals.map((signal, i) => (
                                    <li key={i} className="flex items-start gap-2">
                                      <span className="text-purple-500 mt-0.5">•</span>
                                      <span>{signal}</span>
                                    </li>
                                  ))}
                                </ul>
                              </div>
                            )}
                          </div>
                        )
                      })()}

                      {/* Lessons */}
                      {entry.lessons && entry.lessons.length > 0 && (
                        <div className="space-y-3">
                          <h4 className="text-sm font-medium text-muted-foreground">
                            {t("insights.lessons")}
                          </h4>
                          <div className="space-y-3">
                            {entry.lessons.map((lesson, idx) => (
                              <div key={idx} className="p-3 rounded-lg border bg-card">
                                <div className="flex items-center gap-2 mb-2">
                                  <Badge
                                    variant="outline"
                                    className={`${getPriorityColor(lesson.priority)} text-xs`}
                                  >
                                    {t(`insights.priority.${lesson.priority}`)}
                                  </Badge>
                                </div>
                                <div className="space-y-2 text-sm">
                                  <div>
                                    <span className="text-muted-foreground font-medium">
                                      {language === "ko" ? "조건" : "Condition"}:
                                    </span>
                                    <p className="mt-0.5">{lesson.condition}</p>
                                  </div>
                                  <div>
                                    <span className="text-muted-foreground font-medium">
                                      {language === "ko" ? "행동" : "Action"}:
                                    </span>
                                    <p className="mt-0.5 text-primary">{lesson.action}</p>
                                  </div>
                                  {lesson.reason && (
                                    <div>
                                      <span className="text-muted-foreground font-medium">
                                        {language === "ko" ? "이유" : "Reason"}:
                                      </span>
                                      <p className="mt-0.5 text-muted-foreground text-xs">{lesson.reason}</p>
                                    </div>
                                  )}
                                </div>
                              </div>
                            ))}
                          </div>
                        </div>
                      )}

                      {/* Pattern Tags */}
                      {entry.pattern_tags && entry.pattern_tags.length > 0 && (
                        <div>
                          <h4 className="text-sm font-medium text-muted-foreground mb-2">
                            {t("insights.patternTags")}
                          </h4>
                          <div className="flex flex-wrap gap-2">
                            {entry.pattern_tags.map((tag, idx) => (
                              <Badge key={idx} variant="secondary" className="text-xs">
                                {tag}
                              </Badge>
                            ))}
                          </div>
                        </div>
                      )}
                    </div>
                  </AccordionContent>
                </AccordionItem>
              ))}
            </Accordion>
          )}
        </CardContent>
      </Card>

      {/* Intuitions Section */}
      <Card>
        <CardHeader>
          <div className="flex items-center gap-2">
            <Brain className="w-5 h-5 text-cyan-500" />
            <CardTitle>{t("insights.intuitions")}</CardTitle>
          </div>
          <CardDescription>{t("insights.intuitionsDescription")}</CardDescription>
        </CardHeader>
        <CardContent>
          {data.intuitions.length === 0 ? (
            <p className="text-muted-foreground text-center py-8">{t("insights.noIntuitions")}</p>
          ) : (
            <div className="space-y-4">
              {data.intuitions.map((intuition) => (
                <div
                  key={intuition.id}
                  className="p-4 rounded-lg border bg-card hover:bg-muted/50 transition-colors"
                >
                  <div className="flex items-start justify-between gap-4">
                    <div className="flex-1 space-y-2">
                      <div className="flex items-center gap-2">
                        <Badge variant="outline" className="bg-cyan-500/10 text-cyan-600 dark:text-cyan-400">
                          {intuition.category}
                        </Badge>
                        {intuition.subcategory && (
                          <Badge variant="outline" className="bg-gray-500/10 text-gray-600 dark:text-gray-400">
                            {intuition.subcategory}
                          </Badge>
                        )}
                      </div>
                      <p className="font-medium">
                        <span className="text-muted-foreground">{t("insights.condition")}:</span>{" "}
                        {intuition.condition}
                      </p>
                      <p className="text-primary">
                        <span className="text-muted-foreground">{t("insights.insight")}:</span>{" "}
                        {intuition.insight}
                      </p>
                    </div>
                    <div className="text-right space-y-2 min-w-[140px]">
                      <div>
                        <div className="text-sm text-muted-foreground">{t("insights.confidence")}</div>
                        {getConfidenceBar(intuition.confidence)}
                      </div>
                      <div className="flex items-center justify-end gap-4 text-xs text-muted-foreground">
                        <span>{t("insights.successRate")}: {(intuition.success_rate * 100).toFixed(0)}%</span>
                        <span>{t("insights.supportingTrades")}: {intuition.supporting_trades}</span>
                      </div>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
