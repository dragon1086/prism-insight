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
  Zap,
  BarChart3,
  ArrowUpRight,
  ArrowDownRight,
  Timer,
  Eye,
  Trophy,
  XCircle,
  HelpCircle,
  Filter,
  Target
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

      {/* Two Category Summary Boxes */}
      <div className="grid md:grid-cols-2 gap-4">
        {/* 📊 Performance Analysis Summary */}
        <Card className="border-2 border-blue-500/20">
          <CardContent className="pt-4">
            <div className="flex items-center gap-2 mb-3">
              <div className="p-1.5 rounded-md bg-blue-500/10">
                <BarChart3 className="w-4 h-4 text-blue-600 dark:text-blue-400" />
              </div>
              <span className="font-semibold">{t("insights.category.performance")}</span>
              <Badge variant="outline" className="text-xs ml-auto">
                {language === "ko" ? "최근 1개월" : "Last 30d"}
              </Badge>
            </div>
            <div className="grid grid-cols-2 gap-3 text-sm">
              <div>
                <span className="text-muted-foreground text-xs">
                  {language === "ko" ? "실제 매매" : "Actual Trades"}
                </span>
                <p className="font-bold text-green-600">
                  {data.performance_analysis?.actual_trading?.count || 0}{language === "ko" ? "건" : ""}
                  {data.performance_analysis?.actual_trading?.win_rate !== undefined && (
                    <span className="text-muted-foreground font-normal text-xs ml-1">
                      ({language === "ko" ? "승률" : "WR"} {(data.performance_analysis.actual_trading.win_rate * 100).toFixed(0)}%)
                    </span>
                  )}
                </p>
              </div>
              <div>
                <span className="text-muted-foreground text-xs">
                  {language === "ko" ? "평균 수익률" : "Avg Return"}
                </span>
                <p className={`font-bold ${
                  (data.performance_analysis?.actual_trading?.avg_profit_rate || 0) >= 0
                    ? "text-green-600" : "text-red-600"
                }`}>
                  {data.performance_analysis?.actual_trading?.avg_profit_rate !== undefined
                    ? formatPercent(data.performance_analysis.actual_trading.avg_profit_rate)
                    : "-"}
                </p>
              </div>
              <TooltipProvider>
                <div>
                  <span className="text-muted-foreground text-xs flex items-center gap-1">
                    {language === "ko" ? "관망 종목" : "Watched Stocks"}
                    <Tooltip>
                      <TooltipTrigger>
                        <HelpCircle className="w-3 h-3" />
                      </TooltipTrigger>
                      <TooltipContent className="max-w-xs">
                        <p>{language === "ko"
                          ? "분석 후 매수하지 않고 관망한 종목. 30일 추적 완료된 종목만 성과 분석에 포함."
                          : "Stocks analyzed but not purchased. Only 30-day completed stocks included in analysis."}</p>
                      </TooltipContent>
                    </Tooltip>
                  </span>
                  <p className="font-bold">
                    {data.performance_analysis?.overview.total || 0}{language === "ko" ? "건" : ""}
                    <span className="text-muted-foreground font-normal text-xs ml-1">
                      ({t("insights.performance.completed")} {data.performance_analysis?.overview.completed || 0})
                    </span>
                  </p>
                </div>
                <div>
                  <span className="text-muted-foreground text-xs flex items-center gap-1">
                    {language === "ko" ? "관망 승률" : "Watched Win Rate"}
                    <Tooltip>
                      <TooltipTrigger>
                        <HelpCircle className="w-3 h-3" />
                      </TooltipTrigger>
                      <TooltipContent className="max-w-xs">
                        <p>{language === "ko"
                          ? "관망 종목 중 30일 후 수익인 종목 비율 (30일 추적 완료 기준)"
                          : "Percentage of watched stocks profitable after 30 days"}</p>
                      </TooltipContent>
                    </Tooltip>
                  </span>
                  <p className="font-bold">
                    {(() => {
                      const triggers = data.performance_analysis?.trigger_performance || []
                      const totalWins = triggers.reduce((sum, t) => sum + ((t.win_rate_30d || 0) * t.count), 0)
                      const totalCount = triggers.reduce((sum, t) => sum + t.count, 0)
                      return totalCount > 0 ? `${((totalWins / totalCount) * 100).toFixed(0)}%` : "-"
                    })()}
                  </p>
                </div>
              </TooltipProvider>
            </div>
          </CardContent>
        </Card>

        {/* 🧠 Trading Wisdom Summary */}
        <Card className="border-2 border-purple-500/20">
          <CardContent className="pt-4">
            <div className="flex items-center gap-2 mb-3">
              <div className="p-1.5 rounded-md bg-purple-500/10">
                <Brain className="w-4 h-4 text-purple-600 dark:text-purple-400" />
              </div>
              <span className="font-semibold">{t("insights.category.wisdom")}</span>
            </div>
            <div className="grid grid-cols-2 gap-3 text-sm">
              <div>
                <span className="text-muted-foreground text-xs">{t("insights.summary.totalPrinciples")}</span>
                <p className="font-bold">{data.summary.total_principles}{language === "ko" ? "개" : ""}</p>
              </div>
              <div>
                <span className="text-muted-foreground text-xs">{t("insights.summary.highPriority")}</span>
                <p className="font-bold text-red-600">{data.summary.high_priority_count}{language === "ko" ? "개" : ""}</p>
              </div>
              <div>
                <span className="text-muted-foreground text-xs">{t("insights.summary.totalIntuitions")}</span>
                <p className="font-bold">{data.summary.total_intuitions}{language === "ko" ? "개" : ""}</p>
              </div>
              <div>
                <span className="text-muted-foreground text-xs">{t("insights.summary.avgConfidence")}</span>
                <p className="font-bold">{(data.summary.avg_confidence * 100).toFixed(0)}%</p>
              </div>
            </div>
          </CardContent>
        </Card>
      </div>

      {/* ═══════════════════════════════════════════════════════════════════ */}
      {/* 📊 Performance Analysis Section Header */}
      <div className="flex items-center gap-3 pt-2">
        <div className="p-2 rounded-lg bg-blue-500/10">
          <BarChart3 className="w-5 h-5 text-blue-600 dark:text-blue-400" />
        </div>
        <h3 className="text-lg font-semibold">{t("insights.category.performance")}</h3>
        <div className="flex-1 h-px bg-border" />
      </div>

      {/* Performance Analysis Section */}
      {data.performance_analysis && (
        <Card>
          <CardHeader className="pb-2">
            <CardDescription>{t("insights.performance.description")}</CardDescription>
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
                {/* Trigger Type Performance - 관망종목의 트리거 유형별 성과 */}
                {data.performance_analysis.trigger_performance.length > 0 && (
                  <div className="space-y-3">
                    <h4 className="text-sm font-medium flex items-center gap-2">
                      <Eye className="w-4 h-4 text-cyan-500" />
                      {language === "ko" ? "관망종목의 트리거 유형별 성과" : "Watched Stocks by Trigger Type"}
                      <Tooltip>
                        <TooltipTrigger>
                          <HelpCircle className="w-3 h-3 text-muted-foreground" />
                        </TooltipTrigger>
                        <TooltipContent className="max-w-xs">
                          <p>{language === "ko"
                            ? "분석 후 관망한 종목들의 7/14/30일 가격 변화 추적 결과"
                            : "Price tracking results for stocks analyzed but not traded"}</p>
                        </TooltipContent>
                      </Tooltip>
                    </h4>
                    <div className="overflow-x-auto">
                      <table className="w-full text-sm">
                        <thead>
                          <tr className="border-b">
                            <th className="text-left py-2 px-3 font-medium text-muted-foreground">
                              {language === "ko" ? "트리거" : "Trigger"}
                            </th>
                            <th className="text-center py-2 px-3 font-medium text-muted-foreground">
                              {t("insights.performance.count")}
                            </th>
                            <th className="text-center py-2 px-3 font-medium text-muted-foreground">
                              {t("insights.performance.day7")}
                            </th>
                            <th className="text-center py-2 px-3 font-medium text-muted-foreground">
                              {t("insights.performance.day14")}
                            </th>
                            <th className="text-center py-2 px-3 font-medium text-muted-foreground">
                              {t("insights.performance.day30")}
                            </th>
                            <th className="text-center py-2 px-3 font-medium text-muted-foreground">
                              <div className="flex items-center justify-center gap-1">
                                {t("insights.performance.winRate")}
                                <Tooltip>
                                  <TooltipTrigger>
                                    <HelpCircle className="w-3 h-3 text-muted-foreground" />
                                  </TooltipTrigger>
                                  <TooltipContent className="max-w-xs">
                                    <p>{language === "ko"
                                      ? "30일 후에도 수익인 종목의 비율"
                                      : "Percentage of stocks still profitable after 30 days"}</p>
                                  </TooltipContent>
                                </Tooltip>
                              </div>
                            </th>
                          </tr>
                        </thead>
                        <tbody>
                          {data.performance_analysis.trigger_performance.map((trigger, idx) => (
                            <tr key={idx} className="border-b hover:bg-muted/50">
                              <td className="py-2 px-3 font-medium">{trigger.trigger_type}</td>
                              <td className="py-2 px-3 text-center">{trigger.count}</td>
                              <td className={`py-2 px-3 text-center ${
                                trigger.avg_7d_return !== null && (trigger.avg_7d_return || 0) >= 0 ? "text-green-600" : "text-red-600"
                              }`}>
                                {trigger.avg_7d_return !== null ? formatPercent(trigger.avg_7d_return) : "-"}
                              </td>
                              <td className={`py-2 px-3 text-center ${
                                trigger.avg_14d_return !== null && (trigger.avg_14d_return || 0) >= 0 ? "text-green-600" : "text-red-600"
                              }`}>
                                {trigger.avg_14d_return !== null ? formatPercent(trigger.avg_14d_return) : "-"}
                              </td>
                              <td className={`py-2 px-3 text-center font-medium ${
                                trigger.avg_30d_return !== null && (trigger.avg_30d_return || 0) >= 0 ? "text-green-600" : "text-red-600"
                              }`}>
                                {trigger.avg_30d_return !== null ? formatPercent(trigger.avg_30d_return) : "-"}
                              </td>
                              <td className="py-2 px-3 text-center">
                                {trigger.win_rate_30d !== null && trigger.win_rate_30d !== undefined ? (
                                  <Badge variant={trigger.win_rate_30d >= 0.5 ? "default" : "secondary"} className="text-xs">
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

                {/* Risk/Reward Threshold Analysis - 관망종목의 손익비 구간별 분석 */}
                {data.performance_analysis.rr_threshold_analysis.length > 0 && (
                  <div className="space-y-3">
                    <h4 className="text-sm font-medium flex items-center gap-2">
                      <BarChart3 className="w-4 h-4 text-cyan-500" />
                      {language === "ko" ? "관망종목의 손익비 구간별 분석" : "Watched Stocks by R/R Ratio"}
                      <Tooltip>
                        <TooltipTrigger>
                          <HelpCircle className="w-3 h-3 text-muted-foreground" />
                        </TooltipTrigger>
                        <TooltipContent className="max-w-xs">
                          <p>{language === "ko"
                            ? "관망 종목의 손익비(목표가÷손절가) 구간별 30일 후 수익률. 30일 추적 완료된 종목 기준."
                            : "30-day returns of watched stocks by Risk/Reward ratio range."}</p>
                        </TooltipContent>
                      </Tooltip>
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

                {/* Actual Trading Performance - 실제 매매 성과 */}
                {data.performance_analysis.actual_trading && data.performance_analysis.actual_trading.count > 0 && (
                  <div className="space-y-3">
                    <h4 className="text-sm font-medium flex items-center gap-2">
                      <TrendingUp className="w-4 h-4 text-green-500" />
                      {language === "ko" ? "실제 매매 성과 (최근 30일)" : "Actual Trading (Last 30 Days)"}
                      <Tooltip>
                        <TooltipTrigger>
                          <HelpCircle className="w-3 h-3 text-muted-foreground" />
                        </TooltipTrigger>
                        <TooltipContent className="max-w-xs">
                          <p>{language === "ko"
                            ? "최근 30일간 매도 완료된 거래 기준. 현재 보유중인 종목은 제외."
                            : "Based on trades sold in the last 30 days. Current holdings excluded."}</p>
                        </TooltipContent>
                      </Tooltip>
                    </h4>
                    <div className="p-4 rounded-lg bg-gradient-to-r from-green-500/10 to-emerald-500/10 border border-green-500/20">
                      <div className="flex items-center justify-between mb-3">
                        <span className="font-medium text-green-700 dark:text-green-400">
                          {data.performance_analysis.actual_trading.count || 0}{language === "ko" ? "건 완료" : " trades"}
                        </span>
                      </div>
                      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-sm">
                        {/* 평균 수익률 */}
                        <div>
                          <span className="text-muted-foreground text-xs">{language === "ko" ? "평균 수익률" : "Avg Return"}</span>
                          <p className={`font-bold text-lg ${
                            (data.performance_analysis.actual_trading.avg_profit_rate || 0) >= 0 ? "text-green-600" : "text-red-600"
                          }`}>
                            {formatPercent(data.performance_analysis.actual_trading.avg_profit_rate)}
                          </p>
                        </div>
                        {/* 승률 */}
                        <div>
                          <span className="text-muted-foreground text-xs">{language === "ko" ? "승률" : "Win Rate"}</span>
                          <p className="font-bold text-lg">
                            {data.performance_analysis.actual_trading.win_rate !== null
                              ? `${(data.performance_analysis.actual_trading.win_rate * 100).toFixed(0)}%`
                              : "-"}
                            <span className="text-xs font-normal text-muted-foreground ml-1">
                              ({data.performance_analysis.actual_trading.win_count || 0}W/{data.performance_analysis.actual_trading.loss_count || 0}L)
                            </span>
                          </p>
                        </div>
                        {/* 평균 수익 (수익건) */}
                        <div>
                          <span className="text-muted-foreground text-xs">{language === "ko" ? "평균 수익 (수익건)" : "Avg Profit (wins)"}</span>
                          <p className="font-bold text-green-600">
                            {formatPercent(data.performance_analysis.actual_trading.avg_profit)}
                          </p>
                        </div>
                        {/* 평균 손실 (손실건) */}
                        <div>
                          <span className="text-muted-foreground text-xs">{language === "ko" ? "평균 손실 (손실건)" : "Avg Loss (losses)"}</span>
                          <p className="font-bold text-red-600">
                            {formatPercent(data.performance_analysis.actual_trading.avg_loss)}
                          </p>
                        </div>
                        {/* 최대 수익 */}
                        <div>
                          <span className="text-muted-foreground text-xs">{language === "ko" ? "최대 수익" : "Max Profit"}</span>
                          <p className="font-bold text-green-600">
                            {formatPercent(data.performance_analysis.actual_trading.max_profit)}
                          </p>
                        </div>
                        {/* 최대 손실 */}
                        <div>
                          <span className="text-muted-foreground text-xs">{language === "ko" ? "최대 손실" : "Max Loss"}</span>
                          <p className="font-bold text-red-600">
                            {formatPercent(data.performance_analysis.actual_trading.max_loss)}
                          </p>
                        </div>
                        {/* Profit Factor */}
                        <div>
                          <span className="text-muted-foreground text-xs flex items-center gap-1">
                            Profit Factor
                            <Tooltip>
                              <TooltipTrigger>
                                <HelpCircle className="w-3 h-3" />
                              </TooltipTrigger>
                              <TooltipContent className="max-w-xs">
                                <p>{language === "ko"
                                  ? "총수익 ÷ 총손실. 1.0 이상이면 수익, 2.0 이상이면 우수"
                                  : "Total Profit ÷ Total Loss. Above 1.0 is profitable, above 2.0 is excellent"}</p>
                              </TooltipContent>
                            </Tooltip>
                          </span>
                          <p className={`font-bold ${
                            data.performance_analysis.actual_trading.profit_factor !== null &&
                            data.performance_analysis.actual_trading.profit_factor >= 1 ? "text-green-600" : "text-red-600"
                          }`}>
                            {data.performance_analysis.actual_trading.profit_factor !== null
                              ? data.performance_analysis.actual_trading.profit_factor.toFixed(2)
                              : "-"}
                          </p>
                        </div>
                      </div>
                    </div>
                  </div>
                )}

                {/* Actual Trading by Trigger Type - 실제 매매 종목의 트리거 유형별 성과 (2026.01.12~) */}
                {data.performance_analysis.actual_trading_by_trigger && data.performance_analysis.actual_trading_by_trigger.length > 0 && (
                  <div className="space-y-3">
                    <h4 className="text-sm font-medium flex items-center gap-2">
                      <Filter className="w-4 h-4 text-purple-500" />
                      {language === "ko" ? "실제 매매 종목의 트리거 유형별 성과 (2026.01.12~)" : "Actual Trading by Trigger Type (Since 2026.01.12)"}
                      <Tooltip>
                        <TooltipTrigger>
                          <HelpCircle className="w-3 h-3 text-muted-foreground" />
                        </TooltipTrigger>
                        <TooltipContent className="max-w-xs">
                          <p>{language === "ko"
                            ? "2026.01.12부터 트리거 유형 추적 시작. 어떤 트리거로 진입한 매매가 성과가 좋은지 비교."
                            : "Trigger type tracking started from 2026.01.12. Compare performance by entry trigger type."}</p>
                        </TooltipContent>
                      </Tooltip>
                    </h4>
                    <div className="overflow-x-auto">
                      <table className="w-full text-sm">
                        <thead>
                          <tr className="border-b">
                            <th className="text-left py-2 px-3 font-medium text-muted-foreground">{t("insights.performance.triggerType")}</th>
                            <th className="text-center py-2 px-3 font-medium text-muted-foreground">{t("insights.performance.count")}</th>
                            <th className="text-center py-2 px-3 font-medium text-muted-foreground">{language === "ko" ? "승률" : "Win Rate"}</th>
                            <th className="text-center py-2 px-3 font-medium text-muted-foreground">
                              <div className="flex items-center justify-center gap-1">
                                {language === "ko" ? "평균 수익" : "Avg Profit"}
                                <Tooltip>
                                  <TooltipTrigger>
                                    <HelpCircle className="w-3 h-3" />
                                  </TooltipTrigger>
                                  <TooltipContent>
                                    <p>{language === "ko" ? "수익 거래만의 평균" : "Average of winning trades only"}</p>
                                  </TooltipContent>
                                </Tooltip>
                              </div>
                            </th>
                            <th className="text-center py-2 px-3 font-medium text-muted-foreground">
                              <div className="flex items-center justify-center gap-1">
                                {language === "ko" ? "평균 손실" : "Avg Loss"}
                                <Tooltip>
                                  <TooltipTrigger>
                                    <HelpCircle className="w-3 h-3" />
                                  </TooltipTrigger>
                                  <TooltipContent>
                                    <p>{language === "ko" ? "손실 거래만의 평균" : "Average of losing trades only"}</p>
                                  </TooltipContent>
                                </Tooltip>
                              </div>
                            </th>
                            <th className="text-center py-2 px-3 font-medium text-muted-foreground">Profit Factor</th>
                          </tr>
                        </thead>
                        <tbody>
                          {data.performance_analysis.actual_trading_by_trigger.map((item, idx) => (
                            <tr key={idx} className="border-b hover:bg-muted/50">
                              <td className="py-2 px-3 font-medium">{item.trigger_type}</td>
                              <td className="py-2 px-3 text-center">
                                {item.count}
                                <span className="text-xs text-muted-foreground ml-1">
                                  ({item.win_count || 0}W/{item.loss_count || 0}L)
                                </span>
                              </td>
                              <td className="py-2 px-3 text-center">
                                {item.win_rate !== null ? `${(item.win_rate * 100).toFixed(0)}%` : "-"}
                              </td>
                              <td className="py-2 px-3 text-center text-green-600">
                                {item.avg_profit !== null && item.avg_profit !== undefined
                                  ? formatPercent(item.avg_profit)
                                  : "-"}
                              </td>
                              <td className="py-2 px-3 text-center text-red-600">
                                {item.avg_loss !== null && item.avg_loss !== undefined
                                  ? formatPercent(item.avg_loss)
                                  : "-"}
                              </td>
                              <td className={`py-2 px-3 text-center ${
                                item.profit_factor !== null && item.profit_factor >= 1 ? "text-green-600" : "text-red-600"
                              }`}>
                                {item.profit_factor !== null ? item.profit_factor.toFixed(2) : "-"}
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
                        <Tooltip>
                          <TooltipTrigger>
                            <HelpCircle className="w-3 h-3 text-muted-foreground" />
                          </TooltipTrigger>
                          <TooltipContent className="max-w-xs">
                            <p>{language === "ko"
                              ? "관망했지만 30일 후 +10% 이상 상승한 종목. 분석 시점 가격 기준."
                              : "Stocks skipped but rose +10%+ after 30 days from analysis price."}</p>
                          </TooltipContent>
                        </Tooltip>
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
                            <div className="mt-2 text-xs text-muted-foreground grid grid-cols-3 gap-2">
                              <div>
                                <span>{language === "ko" ? "분석일" : "Analyzed"}: </span>
                                <span>{opp.analyzed_date?.split(' ')[0] || '-'}</span>
                              </div>
                              <div>
                                <span>{language === "ko" ? "트리거" : "Trigger"}: </span>
                                <span>{opp.trigger_type}</span>
                              </div>
                              <div>
                                <span>{language === "ko" ? "판정" : "Decision"}: </span>
                                <span className="text-red-600">{opp.decision || opp.skip_reason || '-'}</span>
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
                        <Tooltip>
                          <TooltipTrigger>
                            <HelpCircle className="w-3 h-3 text-muted-foreground" />
                          </TooltipTrigger>
                          <TooltipContent className="max-w-xs">
                            <p>{language === "ko"
                              ? "관망하여 30일 후 -10% 이상 하락을 피한 종목. 분석 시점 가격 기준."
                              : "Stocks skipped that fell -10%+ after 30 days. Avoided loss from analysis price."}</p>
                          </TooltipContent>
                        </Tooltip>
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
                            <div className="mt-2 text-xs text-muted-foreground grid grid-cols-3 gap-2">
                              <div>
                                <span>{language === "ko" ? "분석일" : "Analyzed"}: </span>
                                <span>{loss.analyzed_date?.split(' ')[0] || '-'}</span>
                              </div>
                              <div>
                                <span>{language === "ko" ? "트리거" : "Trigger"}: </span>
                                <span>{loss.trigger_type}</span>
                              </div>
                              <div>
                                <span>{language === "ko" ? "판정" : "Decision"}: </span>
                                <span className="text-green-600">{loss.decision || loss.skip_reason || '-'}</span>
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

      {/* ═══════════════════════════════════════════════════════════════════ */}
      {/* 🧠 Trading Wisdom Section Header */}
      <div className="flex items-center gap-3 pt-2">
        <div className="p-2 rounded-lg bg-purple-500/10">
          <Brain className="w-5 h-5 text-purple-600 dark:text-purple-400" />
        </div>
        <h3 className="text-lg font-semibold">{t("insights.category.wisdom")}</h3>
        <div className="flex-1 h-px bg-border" />
      </div>

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
