"use client"

import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip"
import { Shield, TrendingUp, Target, BookOpen, HelpCircle } from "lucide-react"
import { useLanguage } from "@/components/language-provider"
import type { TriggerReliabilityData, Market } from "@/types/dashboard"

interface TriggerReliabilityCardProps {
  data: TriggerReliabilityData
  market?: Market
}

export function TriggerReliabilityCard({ data, market = "KR" }: TriggerReliabilityCardProps) {
  const { t, language } = useLanguage()

  const getGradeColor = (grade: "A" | "B" | "C" | "D") => {
    switch (grade) {
      case "A":
        return "bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-400"
      case "B":
        return "bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-400"
      case "C":
        return "bg-yellow-100 text-yellow-800 dark:bg-yellow-900/30 dark:text-yellow-400"
      case "D":
        return "bg-gray-100 text-gray-600 dark:bg-gray-800/30 dark:text-gray-400"
      default:
        return "bg-gray-100 text-gray-600 dark:bg-gray-800/30 dark:text-gray-400"
    }
  }

  const formatPercent = (value: number | null) => {
    if (value === null || value === undefined) return "-"
    return `${(value * 100).toFixed(0)}%`
  }

  if (!data.trigger_reliability || data.trigger_reliability.length === 0) {
    return (
      <Card>
        <CardHeader>
          <div className="flex items-center gap-2">
            <Shield className="w-5 h-5 text-amber-500" />
            <CardTitle>{t("insights.triggerReliability.title")}</CardTitle>
          </div>
          <CardDescription>{t("insights.triggerReliability.description")}</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="flex flex-col items-center justify-center py-12">
            <Shield className="w-12 h-12 text-muted-foreground mb-4" />
            <p className="text-muted-foreground text-center">{t("insights.triggerReliability.noData")}</p>
          </div>
        </CardContent>
      </Card>
    )
  }

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between flex-wrap gap-4">
          <div className="flex items-center gap-2">
            <Shield className="w-5 h-5 text-amber-500" />
            <CardTitle>{t("insights.triggerReliability.title")}</CardTitle>
          </div>
          {data.best_trigger && (
            <div className="flex items-center gap-2">
              <span className="text-sm text-muted-foreground">{t("insights.triggerReliability.bestTrigger")}:</span>
              <Badge className="bg-gradient-to-r from-amber-500 to-yellow-500 text-white">
                {data.best_trigger} ({data.trigger_reliability.find(t => t.trigger_type === data.best_trigger)?.grade || "?"})
              </Badge>
            </div>
          )}
        </div>
        <CardDescription>{t("insights.triggerReliability.description")}</CardDescription>
      </CardHeader>
      <CardContent>
        <TooltipProvider>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b">
                  <th className="text-left py-3 px-3 font-medium text-muted-foreground">
                    {t("insights.triggerReliability.grade")}
                  </th>
                  <th className="text-left py-3 px-3 font-medium text-muted-foreground">
                    {language === "ko" ? "트리거" : "Trigger"}
                  </th>
                  <th className="text-center py-3 px-3 font-medium text-muted-foreground">
                    <div className="flex items-center justify-center gap-1">
                      {t("insights.triggerReliability.analysisAccuracy")}
                      <Tooltip>
                        <TooltipTrigger>
                          <HelpCircle className="w-3 h-3" />
                        </TooltipTrigger>
                        <TooltipContent className="max-w-xs">
                          <p>{language === "ko"
                            ? "분석 후 관망한 종목의 30일 추적 성과. 완료/전체, 승률"
                            : "30-day tracking of watched stocks. Completed/Total, Win rate"}</p>
                        </TooltipContent>
                      </Tooltip>
                    </div>
                  </th>
                  <th className="text-center py-3 px-3 font-medium text-muted-foreground">
                    <div className="flex items-center justify-center gap-1">
                      {t("insights.triggerReliability.actualTrading")}
                      <Tooltip>
                        <TooltipTrigger>
                          <HelpCircle className="w-3 h-3" />
                        </TooltipTrigger>
                        <TooltipContent className="max-w-xs">
                          <p>{language === "ko"
                            ? "실제 매매 건수, 승률, Profit Factor"
                            : "Actual trades count, win rate, profit factor"}</p>
                        </TooltipContent>
                      </Tooltip>
                    </div>
                  </th>
                  <th className="text-center py-3 px-3 font-medium text-muted-foreground">
                    {t("insights.triggerReliability.principles")}
                  </th>
                  <th className="text-left py-3 px-3 font-medium text-muted-foreground">
                    {t("insights.triggerReliability.recommendation")}
                  </th>
                </tr>
              </thead>
              <tbody>
                {data.trigger_reliability.map((item, idx) => (
                  <tr key={idx} className="border-b hover:bg-muted/50">
                    <td className="py-3 px-3">
                      <Badge className={getGradeColor(item.grade)}>
                        {item.grade}
                      </Badge>
                    </td>
                    <td className="py-3 px-3 font-medium">{item.trigger_type}</td>
                    <td className="py-3 px-3">
                      <div className="flex flex-col items-center gap-1">
                        <span className="text-xs text-muted-foreground">
                          {item.analysis_accuracy.completed}/{item.analysis_accuracy.total_tracked} {t("insights.triggerReliability.completed")}
                        </span>
                        <Badge variant={
                          item.analysis_accuracy.win_rate_30d !== null && item.analysis_accuracy.win_rate_30d >= 0.5
                            ? "default"
                            : "secondary"
                        } className="text-xs">
                          {t("insights.triggerReliability.winRate")}: {formatPercent(item.analysis_accuracy.win_rate_30d)}
                        </Badge>
                      </div>
                    </td>
                    <td className="py-3 px-3">
                      <div className="flex flex-col items-center gap-1">
                        <span className="text-xs font-medium">
                          {item.actual_trading.count} {t("insights.triggerReliability.trades")}
                        </span>
                        <div className="flex items-center gap-2 text-xs">
                          <span className={
                            item.actual_trading.win_rate !== null && item.actual_trading.win_rate >= 0.5
                              ? "text-green-600 dark:text-green-400"
                              : "text-muted-foreground"
                          }>
                            {t("insights.triggerReliability.winRate")}: {formatPercent(item.actual_trading.win_rate)}
                          </span>
                          <Tooltip>
                            <TooltipTrigger>
                              <span className={
                                item.actual_trading.profit_factor !== null && item.actual_trading.profit_factor >= 1
                                  ? "text-green-600 dark:text-green-400 font-medium"
                                  : "text-red-600 dark:text-red-400"
                              }>
                                PF: {item.actual_trading.profit_factor !== null
                                  ? item.actual_trading.profit_factor.toFixed(2)
                                  : "-"}
                              </span>
                            </TooltipTrigger>
                            <TooltipContent>
                              <p>Profit Factor: {language === "ko" ? "총수익÷총손실" : "Total Profit ÷ Total Loss"}</p>
                            </TooltipContent>
                          </Tooltip>
                        </div>
                      </div>
                    </td>
                    <td className="py-3 px-3 text-center">
                      <Tooltip>
                        <TooltipTrigger>
                          <Badge variant="outline" className="text-xs">
                            <BookOpen className="w-3 h-3 mr-1" />
                            {item.related_principles.length}
                          </Badge>
                        </TooltipTrigger>
                        <TooltipContent className="max-w-sm">
                          {item.related_principles.length === 0 ? (
                            <p>{language === "ko" ? "관련 원칙 없음" : "No related principles"}</p>
                          ) : (
                            <div className="space-y-2 max-h-64 overflow-y-auto">
                              {item.related_principles.map((principle, i) => (
                                <div key={i} className="text-xs border-b pb-2 last:border-b-0">
                                  <p className="font-medium">{principle.action}</p>
                                  <p className="text-muted-foreground mt-1">
                                    {language === "ko" ? "신뢰도" : "Confidence"}: {(principle.confidence * 100).toFixed(0)}%
                                    ({principle.supporting_trades} {language === "ko" ? "거래" : "trades"})
                                  </p>
                                </div>
                              ))}
                            </div>
                          )}
                        </TooltipContent>
                      </Tooltip>
                    </td>
                    <td className="py-3 px-3">
                      <p className="text-xs text-muted-foreground line-clamp-2">
                        {item.recommendation}
                      </p>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </TooltipProvider>
      </CardContent>
    </Card>
  )
}
