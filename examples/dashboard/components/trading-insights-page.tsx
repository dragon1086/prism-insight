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
  Lightbulb,
  BookOpen,
  Brain,
  TrendingUp,
  TrendingDown,
  AlertCircle,
  CheckCircle,
  Target,
  Zap
} from "lucide-react"
import { useLanguage } from "@/components/language-provider"
import type { TradingInsightsData, TradingPrinciple, TradingJournal, TradingIntuition } from "@/types/dashboard"

interface TradingInsightsPageProps {
  data: TradingInsightsData
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

      {/* Summary Cards */}
      <div className="grid gap-4 md:grid-cols-3 lg:grid-cols-6">
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
              <BookOpen className="w-4 h-4 text-blue-500" />
              <span className="text-sm text-muted-foreground">{t("insights.summary.totalTrades")}</span>
            </div>
            <p className="text-2xl font-bold mt-2">{data.summary.total_journal_entries}</p>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="pt-6">
            <div className="flex items-center gap-2">
              {data.summary.avg_profit_rate >= 0 ? (
                <TrendingUp className="w-4 h-4 text-green-500" />
              ) : (
                <TrendingDown className="w-4 h-4 text-red-500" />
              )}
              <span className="text-sm text-muted-foreground">{t("insights.summary.avgProfit")}</span>
            </div>
            <p className={`text-2xl font-bold mt-2 ${
              data.summary.avg_profit_rate >= 0 ? "text-green-600" : "text-red-600"
            }`}>
              {formatPercent(data.summary.avg_profit_rate)}
            </p>
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
                          {formatPercent(entry.profit_rate)}
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
                            {formatPercent(entry.profit_rate)}
                          </p>
                        </div>
                        <div>
                          <span className="text-muted-foreground">{t("insights.layer")}</span>
                          <p className="font-medium">Layer {entry.compression_layer}</p>
                        </div>
                      </div>

                      {/* Situation Analysis */}
                      {entry.situation_analysis && (
                        <div>
                          <h4 className="text-sm font-medium text-muted-foreground mb-1">
                            {t("insights.situationAnalysis")}
                          </h4>
                          <p className="text-sm">{entry.situation_analysis}</p>
                        </div>
                      )}

                      {/* Judgment Evaluation */}
                      {entry.judgment_evaluation && (
                        <div>
                          <h4 className="text-sm font-medium text-muted-foreground mb-1">
                            {t("insights.judgmentEvaluation")}
                          </h4>
                          <p className="text-sm">{entry.judgment_evaluation}</p>
                        </div>
                      )}

                      {/* Lessons */}
                      {entry.lessons && entry.lessons.length > 0 && (
                        <div>
                          <h4 className="text-sm font-medium text-muted-foreground mb-2">
                            {t("insights.lessons")}
                          </h4>
                          <div className="space-y-2">
                            {entry.lessons.map((lesson, idx) => (
                              <div key={idx} className="flex items-start gap-2 text-sm">
                                <Badge
                                  variant="outline"
                                  className={`${getPriorityColor(lesson.priority)} text-xs shrink-0`}
                                >
                                  {t(`insights.priority.${lesson.priority}`)}
                                </Badge>
                                <span>{lesson.content}</span>
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
                        {intuition.scope && (
                          <Badge variant="outline" className={getScopeColor(intuition.scope)}>
                            {t(`insights.scope.${intuition.scope}`)}
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
                        <span>{t("insights.timesApplied")}: {intuition.times_applied}</span>
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
