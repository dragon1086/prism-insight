"use client"

import { useState } from "react"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Eye, AlertCircle, TrendingUp, Target, Brain, BarChart3, Filter, ChevronDown, ChevronUp, FileJson } from "lucide-react"
import { useLanguage } from "@/components/language-provider"
import type { WatchlistStock } from "@/types/dashboard"

interface WatchlistPageProps {
  watchlist: WatchlistStock[]
}

export function WatchlistPage({ watchlist }: WatchlistPageProps) {
  const { t, language } = useLanguage()
  const [expandedStocks, setExpandedStocks] = useState<Set<number>>(new Set())
  const [selectedScenario, setSelectedScenario] = useState<any>(null)

  const formatCurrency = (value: number) => {
    return new Intl.NumberFormat("ko-KR", {
      style: "currency",
      currency: "KRW",
      maximumFractionDigits: 0,
    }).format(value)
  }

  const formatDate = (dateString: string) => {
    const date = new Date(dateString)
    return date.toLocaleDateString(language === "ko" ? "ko-KR" : "en-US", {
      year: "numeric",
      month: "long",
      day: "numeric",
    })
  }

  const toggleExpand = (id: number) => {
    setExpandedStocks(prev => {
      const next = new Set(prev)
      if (next.has(id)) {
        next.delete(id)
      } else {
        next.add(id)
      }
      return next
    })
  }

  // 통계 계산
  const totalStocks = watchlist.length
  const highestScore = watchlist.length > 0 
    ? Math.max(...watchlist.map(s => s.buy_score)) 
    : 0
  const avgScore = watchlist.length > 0 
    ? watchlist.reduce((sum, s) => sum + s.buy_score, 0) / watchlist.length 
    : 0
  
  // 최근 7일 종목 수
  const sevenDaysAgo = new Date()
  sevenDaysAgo.setDate(sevenDaysAgo.getDate() - 7)
  const recentStocks = watchlist.filter(s => {
    const stockDate = new Date(s.analyzed_date)
    return stockDate >= sevenDaysAgo
  }).length

  // 섹터별 분포
  const sectorDistribution = watchlist.reduce((acc, stock) => {
    const sector = stock.sector || t("common.other")
    acc[sector] = (acc[sector] || 0) + 1
    return acc
  }, {} as Record<string, number>)

  const topSectors = Object.entries(sectorDistribution)
    .sort(([, a], [, b]) => b - a)
    .slice(0, 3)

  // 일별 그룹화
  const groupedByDate = watchlist.reduce((acc, stock) => {
    const date = stock.analyzed_date.split(" ")[0]
    if (!acc[date]) acc[date] = []
    acc[date].push(stock)
    return acc
  }, {} as Record<string, WatchlistStock[]>)

  const sortedDates = Object.keys(groupedByDate).sort((a, b) => b.localeCompare(a))

  return (
    <div className="space-y-6">
      {/* 헤더 */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="p-3 rounded-lg bg-gradient-to-br from-amber-500/20 to-orange-500/20">
            <Eye className="w-6 h-6 text-amber-600 dark:text-amber-400" />
          </div>
          <div>
            <h2 className="text-2xl font-bold text-foreground">{t("watchlist.title")}</h2>
            <p className="text-sm text-muted-foreground">{t("watchlist.description")}</p>
          </div>
        </div>
        <Badge variant="outline" className="text-sm">
          {t("watchlist.totalStocks")} {totalStocks}{t("watchlist.stocksUnit")}
        </Badge>
      </div>

      {/* 통계 카드 */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
        <Card className="border-border/50">
          <CardContent className="p-6">
            <div className="flex items-center gap-3 mb-2">
              <Eye className="w-5 h-5 text-primary" />
              <span className="text-sm text-muted-foreground">{t("watchlist.totalStocks")}</span>
            </div>
            <p className="text-3xl font-bold text-foreground">{totalStocks}{t("watchlist.stocksCount")}</p>
            <p className="text-xs text-muted-foreground mt-1">
              {t("watchlist.lastMonthAnalysis")}
            </p>
          </CardContent>
        </Card>

        <Card className="border-border/50">
          <CardContent className="p-6">
            <div className="flex items-center gap-3 mb-2">
              <Target className="w-5 h-5 text-success" />
              <span className="text-sm text-muted-foreground">{t("watchlist.highestScore")}</span>
            </div>
            <p className="text-3xl font-bold text-success">{highestScore}{t("watchlist.scoreUnit")}</p>
            <p className="text-xs text-muted-foreground mt-1">
              {t("watchlist.mostPromising")}
            </p>
          </CardContent>
        </Card>

        <Card className="border-border/50">
          <CardContent className="p-6">
            <div className="flex items-center gap-3 mb-2">
              <BarChart3 className="w-5 h-5 text-chart-3" />
              <span className="text-sm text-muted-foreground">{t("watchlist.avgScore")}</span>
            </div>
            <p className="text-3xl font-bold text-chart-3">{avgScore.toFixed(1)}{t("watchlist.scoreUnit")}</p>
            <p className="text-xs text-muted-foreground mt-1">
              {t("watchlist.outOf10")}
            </p>
          </CardContent>
        </Card>

        <Card className="border-border/50">
          <CardContent className="p-6">
            <div className="flex items-center gap-3 mb-2">
              <TrendingUp className="w-5 h-5 text-amber-600 dark:text-amber-400" />
              <span className="text-sm text-muted-foreground">{t("watchlist.recentWeek")}</span>
            </div>
            <p className="text-3xl font-bold text-amber-600 dark:text-amber-400">{recentStocks}{t("watchlist.stocksCount")}</p>
            <p className="text-xs text-muted-foreground mt-1">
              {t("watchlist.recentlyAnalyzed")}
            </p>
          </CardContent>
        </Card>
      </div>

      {/* 분석 인사이트 */}
      {topSectors.length > 0 && (
        <Card className="border-border/50">
          <CardHeader>
            <CardTitle className="text-base font-semibold flex items-center gap-2">
              <TrendingUp className="w-5 h-5 text-chart-3" />
              {t("watchlist.sectorDistributionTop3")}
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="space-y-3">
              {topSectors.map(([sector, count], index) => (
                <div key={sector} className="flex items-center justify-between p-3 rounded-lg bg-muted/30">
                  <div className="flex items-center gap-3">
                    <div className="flex items-center justify-center w-8 h-8 rounded-full bg-chart-3/20 text-chart-3 font-bold text-sm">
                      {index + 1}
                    </div>
                    <p className="font-medium text-foreground">{sector}</p>
                  </div>
                  <Badge variant="secondary">{count}{t("watchlist.stocksCount")}</Badge>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      )}

      {/* 일별 종목 리스트 */}
      <div className="space-y-6">
        {sortedDates.map(date => (
          <Card key={date} className="border-border/50">
            <CardHeader>
              <div className="flex items-center justify-between">
                <CardTitle className="text-lg font-semibold flex items-center gap-2">
                  {formatDate(date)}
                </CardTitle>
                <Badge variant="secondary">
                  {groupedByDate[date].length}{t("watchlist.analyses")}
                </Badge>
              </div>
            </CardHeader>
            <CardContent>
              <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
                {groupedByDate[date].map((stock) => {
                  const isExpanded = expandedStocks.has(stock.id)
                  return (
                    <Card
                      key={stock.id}
                      className="border-border/30 bg-muted/20 hover:bg-muted/30 transition-all duration-300"
                    >
                      <CardContent className="p-6">
                        <div className="space-y-4">
                          {/* 종목 헤더 */}
                          <div className="flex items-start justify-between">
                            <div className="flex-1">
                              <div className="flex items-center gap-2 mb-2">
                                <h3 className="text-lg font-bold text-foreground">{stock.company_name}</h3>
                                <Badge variant="outline" className="text-xs">{stock.ticker}</Badge>
                              </div>
                              <div className="flex items-center gap-2 flex-wrap">
                                <Badge variant="secondary" className="text-xs">{stock.sector}</Badge>
                                <Badge variant="secondary" className="text-xs">{stock.investment_period}</Badge>
                                <Badge 
                                  variant={stock.buy_score >= stock.min_score ? "default" : "outline"}
                                  className={stock.buy_score >= stock.min_score ? "bg-success/20 text-success border-success/30" : ""}
                                >
                                  {stock.buy_score}/{stock.min_score}점
                                </Badge>
                              </div>
                            </div>
                          </div>

                          {/* 가격 정보 */}
                          <div className="grid grid-cols-3 gap-3">
                            <div className="p-3 rounded-lg bg-background border border-border/50">
                              <p className="text-xs text-muted-foreground mb-1">{t("watchlist.currentPrice")}</p>
                              <p className="text-sm font-bold text-foreground">{formatCurrency(stock.current_price)}</p>
                            </div>
                            <div className="p-3 rounded-lg bg-background border border-border/50">
                              <p className="text-xs text-muted-foreground mb-1">{t("watchlist.targetPrice")}</p>
                              <p className="text-sm font-bold text-success">{formatCurrency(stock.target_price)}</p>
                            </div>
                            <div className="p-3 rounded-lg bg-background border border-border/50">
                              <p className="text-xs text-muted-foreground mb-1">{t("watchlist.stopLoss")}</p>
                              <p className="text-sm font-bold text-destructive">{formatCurrency(stock.stop_loss)}</p>
                            </div>
                          </div>

                          {/* Risk/Reward Ratio */}
                          {stock.scenario?.risk_reward_ratio && (
                            <div className="p-3 rounded-lg bg-gradient-to-r from-blue-50 to-indigo-50 dark:from-blue-950/30 dark:to-indigo-950/30 border border-blue-200/50 dark:border-blue-800/50">
                              <div className="flex items-center justify-between">
                                <p className="text-xs font-semibold text-blue-700 dark:text-blue-300">{t("modal.riskRewardRatio")}</p>
                                <Badge 
                                  variant={stock.scenario.risk_reward_ratio >= 3 ? "default" : stock.scenario.risk_reward_ratio >= 2 ? "secondary" : "destructive"}
                                  className="text-xs"
                                >
                                  {stock.scenario.risk_reward_ratio.toFixed(1)}
                                </Badge>
                              </div>
                              <div className="grid grid-cols-2 gap-2 mt-2">
                                {stock.scenario.expected_return_pct && (
                                  <div>
                                    <p className="text-xs text-muted-foreground">{t("modal.expectedReturn")}</p>
                                    <p className="text-xs font-semibold text-success">+{stock.scenario.expected_return_pct.toFixed(1)}%</p>
                                  </div>
                                )}
                                {stock.scenario.expected_loss_pct && (
                                  <div>
                                    <p className="text-xs text-muted-foreground">{t("modal.expectedLoss")}</p>
                                    <p className="text-xs font-semibold text-destructive">-{stock.scenario.expected_loss_pct.toFixed(1)}%</p>
                                  </div>
                                )}
                              </div>
                            </div>
                          )}

                          {/* 결정 & 사유 */}
                          <div className={`p-4 rounded-lg border ${
                            stock.decision === t("watchlist.entry")
                              ? "bg-success/10 border-success/20"
                              : "bg-amber-500/10 border-amber-500/20"
                          }`}>
                            <div className="flex items-start gap-2">
                              <AlertCircle className={`w-4 h-4 mt-0.5 flex-shrink-0 ${
                                stock.decision === t("watchlist.entry") ? "text-success" : "text-amber-600 dark:text-amber-400"
                              }`} />
                              <div className="flex-1">
                                <p className={`text-sm font-semibold mb-1 ${
                                  stock.decision === t("watchlist.entry") ? "text-success" : "text-amber-600 dark:text-amber-400"
                                }`}>
                                  {t("watchlist.decision")}: {stock.decision}
                                </p>
                                <p className="text-sm text-foreground leading-relaxed">{stock.skip_reason}</p>
                              </div>
                            </div>
                          </div>

                          {/* AI 분석 상세 (확장 가능) */}
                          <div className="flex gap-2">
                            <Button
                              variant="ghost"
                              size="sm"
                              onClick={() => toggleExpand(stock.id)}
                              className="flex-1"
                            >
                              <Brain className="w-4 h-4 mr-2" />
                              {t("watchlist.aiAnalysisDetails")}
                              {isExpanded ? <ChevronUp className="w-4 h-4 ml-2" /> : <ChevronDown className="w-4 h-4 ml-2" />}
                            </Button>
                            
                            {stock.full_json_data && (
                              <Button
                                variant="outline"
                                size="sm"
                                onClick={() => setSelectedScenario(stock.full_json_data)}
                              >
                                <FileJson className="w-4 h-4 mr-2" />
                                JSON
                              </Button>
                            )}
                          </div>

                          {isExpanded && (
                            <div className="space-y-3 pt-2 border-t border-border/30">
                                  {stock.rationale && (
                                    <div className="p-4 rounded-lg bg-primary/10 border border-primary/20">
                                      <p className="text-xs font-semibold text-primary mb-2">{t("watchlist.tradingRationale")}</p>
                                      <p className="text-sm text-foreground leading-relaxed">{stock.rationale}</p>
                                    </div>
                                  )}

                                  {(stock.scenario?.max_portfolio_size || stock.max_portfolio_size) && (
                                    <div className="p-4 rounded-lg bg-indigo-500/10 border border-indigo-500/20">
                                      <p className="text-xs font-semibold text-indigo-600 dark:text-indigo-400 mb-2">{t("modal.maxPortfolioSize")}</p>
                                      <p className="text-sm text-foreground leading-relaxed">
                                        {stock.scenario?.max_portfolio_size || stock.max_portfolio_size}{t("watchlist.stocksUnit")}
                                      </p>
                                    </div>
                                  )}

                                  {stock.portfolio_analysis && (
                                    <div className="p-4 rounded-lg bg-muted/50 border border-border/30">
                                      <p className="text-xs font-semibold text-muted-foreground mb-2">{t("modal.portfolioAnalysis")}</p>
                                      <p className="text-sm text-foreground leading-relaxed">{stock.portfolio_analysis}</p>
                                    </div>
                                  )}

                                  {stock.valuation_analysis && (
                                    <div className="p-4 rounded-lg bg-muted/50 border border-border/30">
                                      <p className="text-xs font-semibold text-muted-foreground mb-2">{t("modal.valuationAnalysis")}</p>
                                      <p className="text-sm text-foreground leading-relaxed">{stock.valuation_analysis}</p>
                                    </div>
                                  )}

                                  {stock.sector_outlook && (
                                    <div className="p-4 rounded-lg bg-muted/50 border border-border/30">
                                      <p className="text-xs font-semibold text-muted-foreground mb-2">{t("modal.sectorOutlook")}</p>
                                      <p className="text-sm text-foreground leading-relaxed">{stock.sector_outlook}</p>
                                    </div>
                                  )}

                                  {stock.market_condition && (
                                    <div className="p-4 rounded-lg bg-muted/50 border border-border/30">
                                      <p className="text-xs font-semibold text-muted-foreground mb-2">{t("modal.marketCondition")}</p>
                                      <p className="text-sm text-foreground leading-relaxed">{stock.market_condition}</p>
                                    </div>
                                  )}

                                  {(stock.scenario?.trading_scenarios || stock.trading_scenarios) && (
                                    <div className="p-4 rounded-lg bg-blue-500/10 border border-blue-500/20">
                                      <p className="text-xs font-semibold text-blue-600 dark:text-blue-400 mb-3">{t("modal.tradingScenarios")}</p>
                                      <div className="space-y-3">
                                        {(() => {
                                          const ts = stock.scenario?.trading_scenarios || stock.trading_scenarios
                                          if (!ts) return null

                                          return (
                                            <>
                                              {ts.key_levels && typeof ts.key_levels === 'object' && (
                                                <div>
                                                  <p className="text-xs font-medium text-muted-foreground mb-2">{t("modal.keyPriceLevels")}</p>
                                                  <div className="grid grid-cols-2 gap-2">
                                                    {Object.entries(ts.key_levels).map(([key, value]) => {
                                                      const labelMap: Record<string, string> = {
                                                        'primary_support': t("modal.primarySupport"),
                                                        'secondary_support': t("modal.secondarySupport"),
                                                        'primary_resistance': t("modal.primaryResistance"),
                                                        'secondary_resistance': t("modal.secondaryResistance"),
                                                        'volume_baseline': t("modal.volumeBaseline")
                                                      }
                                                      const label = labelMap[key] || key
                                                      
                                                      return (
                                                        <div key={key} className="p-2 rounded bg-background/50 border border-border/30">
                                                          <p className="text-xs text-muted-foreground">{label}</p>
                                                          <p className="text-sm font-semibold text-foreground">
                                                            {String(value)}
                                                          </p>
                                                        </div>
                                                      )
                                                    })}
                                                  </div>
                                                </div>
                                              )}
                                              
                                              {ts.sell_triggers && Array.isArray(ts.sell_triggers) && ts.sell_triggers.length > 0 && (
                                                <div>
                                                  <p className="text-xs font-medium text-muted-foreground mb-2">{t("modal.sellTriggers")}</p>
                                                  <ul className="space-y-1">
                                                    {ts.sell_triggers.map((trigger, idx) => (
                                                      <li key={idx} className="text-sm text-foreground flex items-start gap-2">
                                                        <span className="text-destructive mt-1">•</span>
                                                        <span className="flex-1">{trigger}</span>
                                                      </li>
                                                    ))}
                                                  </ul>
                                                </div>
                                              )}

                                              {ts.hold_conditions && Array.isArray(ts.hold_conditions) && ts.hold_conditions.length > 0 && (
                                                <div>
                                                  <p className="text-xs font-medium text-muted-foreground mb-2">{t("modal.holdConditions")}</p>
                                                  <ul className="space-y-1">
                                                    {ts.hold_conditions.map((condition, idx) => (
                                                      <li key={idx} className="text-sm text-foreground flex items-start gap-2">
                                                        <span className="text-success mt-1">•</span>
                                                        <span className="flex-1">{condition}</span>
                                                      </li>
                                                    ))}
                                                  </ul>
                                                </div>
                                              )}

                                              {ts.portfolio_context && (
                                                <div>
                                                  <p className="text-xs font-medium text-muted-foreground mb-2">{t("modal.portfolioContext")}</p>
                                                  <p className="text-sm text-foreground leading-relaxed">
                                                    {ts.portfolio_context}
                                                  </p>
                                                </div>
                                              )}
                                            </>
                                          )
                                        })()}
                                      </div>
                                    </div>
                                  )}
                                </div>
                              )}
                        </div>
                      </CardContent>
                    </Card>
                  )
                })}
              </div>
            </CardContent>
          </Card>
        ))}
      </div>

      {watchlist.length === 0 && (
        <Card className="border-border/50">
          <CardContent className="p-12 text-center">
            <Eye className="w-12 h-12 text-muted-foreground mx-auto mb-4 opacity-50" />
            <p className="text-muted-foreground">{t("watchlist.noData")}</p>
          </CardContent>
        </Card>
      )}

      {/* Scenario JSON 모달 */}
      <Dialog open={!!selectedScenario} onOpenChange={(open) => !open && setSelectedScenario(null)}>
        <DialogContent className="max-w-4xl max-h-[80vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <FileJson className="w-5 h-5" />
              Scenario JSON Data
            </DialogTitle>
          </DialogHeader>
          <div className="mt-4">
            <div className="p-4 rounded-lg bg-muted/30 border border-border/50">
              <pre className="text-xs font-mono overflow-x-auto">
                {JSON.stringify(selectedScenario, null, 2)}
              </pre>
            </div>
            <div className="mt-4 flex justify-end gap-2">
              <Button
                variant="outline"
                size="sm"
                onClick={() => {
                  navigator.clipboard.writeText(JSON.stringify(selectedScenario, null, 2))
                }}
              >
                {t("watchlist.copyToClipboard")}
              </Button>
              <Button
                variant="default"
                size="sm"
                onClick={() => setSelectedScenario(null)}
              >
                {t("modal.close")}
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  )
}
