"use client"

import { useState, type ReactNode } from "react"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { ChevronDown, ChevronUp } from "lucide-react"
import { ReentryWatchCard } from "@/components/reentry-watch-card"
import { useLanguage } from "@/components/language-provider"
import { formatCurrency as formatCurrencyUtil } from "@/lib/currency"
import type { WatchlistStock, Market } from "@/types/dashboard"

interface WatchlistPageProps {
  watchlist: WatchlistStock[]
  market?: Market
}

const CARD = "gap-0 py-0 rounded-lg shadow-none"
const META_BADGE = "text-[10px] font-normal text-muted-foreground"
const NUM = "font-mono tabular-nums text-[13px]"

// Labeled block of AI text: muted label, readable body capped near 70ch.
function TextBlock({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="max-w-[70ch]">
      <p className="text-xs text-muted-foreground mb-1">{label}</p>
      <div className="text-[13px] text-foreground/90 leading-relaxed">{children}</div>
    </div>
  )
}

export function WatchlistPage({ watchlist, market = "KR" }: WatchlistPageProps) {
  const { t, language } = useLanguage()
  const [expandedStocks, setExpandedStocks] = useState<Set<number>>(new Set())
  const [selectedScenario, setSelectedScenario] = useState<any>(null)

  const formatCurrency = (value: number) => {
    return formatCurrencyUtil(value, market, language as "ko" | "en")
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

  const kpis = [
    { label: t("watchlist.totalStocks"), value: `${totalStocks}${t("watchlist.stocksCount")}`, sub: t("watchlist.lastMonthAnalysis") },
    { label: t("watchlist.highestScore"), value: `${highestScore}${t("watchlist.scoreUnit")}`, sub: t("watchlist.mostPromising") },
    { label: t("watchlist.avgScore"), value: `${avgScore.toFixed(1)}${t("watchlist.scoreUnit")}`, sub: t("watchlist.outOf10") },
    { label: t("watchlist.recentWeek"), value: `${recentStocks}${t("watchlist.stocksCount")}`, sub: t("watchlist.recentlyAnalyzed") },
  ]

  return (
    <div className="space-y-6">
      {/* 헤더 */}
      <div className="flex flex-col gap-0.5 sm:flex-row sm:items-baseline sm:justify-between sm:gap-4">
        <div>
          <h2 className="text-lg font-semibold tracking-tight text-foreground">{t("watchlist.title")}</h2>
          <p className="text-sm text-muted-foreground">{t("watchlist.description")}</p>
        </div>
        <span className="text-xs text-muted-foreground font-mono tabular-nums">
          {t("watchlist.totalStocks")} {totalStocks}{t("watchlist.stocksUnit")}
        </span>
      </div>

      {/* 재진입 감시 */}
      <ReentryWatchCard market={market} />

      {/* 통계 카드 */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        {kpis.map((kpi) => (
          <Card key={kpi.label} className={CARD}>
            <CardContent className="p-4 sm:p-5 space-y-2.5 sm:space-y-3">
              <span className="text-[13px] text-muted-foreground block">{kpi.label}</span>
              <p className="font-serif text-[1.75rem] sm:text-[2rem] leading-none tracking-tight tabular-nums text-foreground">
                {kpi.value}
              </p>
              <p className="text-xs text-muted-foreground">{kpi.sub}</p>
            </CardContent>
          </Card>
        ))}
      </div>

      {/* 분석 인사이트 */}
      {topSectors.length > 0 && (
        <Card className={CARD}>
          <CardHeader className="px-5 pt-4 pb-3">
            <CardTitle className="text-sm font-medium">{t("watchlist.sectorDistributionTop3")}</CardTitle>
          </CardHeader>
          <CardContent className="px-5 pb-2">
            <ol className="divide-y">
              {topSectors.map(([sector, count], index) => (
                <li key={sector} className="flex items-center justify-between gap-3 py-2.5">
                  <div className="flex items-center gap-3 min-w-0">
                    <span className={`${NUM} w-4 text-muted-foreground`}>{index + 1}</span>
                    <span className="text-sm text-foreground truncate">{sector}</span>
                  </div>
                  <span className={`${NUM} text-muted-foreground`}>{count}{t("watchlist.stocksCount")}</span>
                </li>
              ))}
            </ol>
          </CardContent>
        </Card>
      )}

      {/* 일별 종목 리스트 */}
      <div className="space-y-4">
        {sortedDates.map(date => (
          <Card key={date} className={CARD}>
            <CardHeader className="px-5 pt-4 pb-3">
              <div className="flex items-baseline justify-between gap-2">
                <CardTitle className="text-sm font-medium">{formatDate(date)}</CardTitle>
                <span className="text-xs text-muted-foreground font-mono tabular-nums">
                  {groupedByDate[date].length}{t("watchlist.analyses")}
                </span>
              </div>
            </CardHeader>
            <CardContent className="px-4 pb-4 sm:px-5 sm:pb-5">
              <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
                {groupedByDate[date].map((stock) => {
                  const isExpanded = expandedStocks.has(stock.id)
                  const isEntry = stock.decision === t("watchlist.entry")
                  const passed = stock.buy_score >= stock.min_score
                  const rr = stock.scenario?.risk_reward_ratio
                  const ts = stock.scenario?.trading_scenarios || stock.trading_scenarios
                  return (
                    <div
                      key={stock.id}
                      className="min-w-0 rounded-md border p-4 space-y-4 transition-colors hover:bg-muted/50"
                    >
                      {/* 종목 헤더 */}
                      <div className="space-y-1.5">
                        <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                          <h3 className="text-base font-semibold text-foreground">{stock.company_name}</h3>
                          <Badge variant="outline" className={META_BADGE}>{stock.ticker}</Badge>
                        </div>
                        <div className="flex flex-wrap items-center gap-1.5">
                          {stock.sector && <Badge variant="outline" className={META_BADGE}>{stock.sector}</Badge>}
                          {stock.investment_period && <Badge variant="outline" className={META_BADGE}>{stock.investment_period}</Badge>}
                          <Badge
                            variant="outline"
                            className={`text-[10px] font-normal font-mono tabular-nums ${passed ? "text-success" : "text-muted-foreground"}`}
                          >
                            {stock.buy_score}/{stock.min_score}점
                          </Badge>
                        </div>
                      </div>

                      {/* 가격 정보 */}
                      <dl className="grid grid-cols-3 gap-3 rounded-md border px-3 py-2.5">
                        <div className="min-w-0">
                          <dt className="text-xs text-muted-foreground">{t("watchlist.currentPrice")}</dt>
                          <dd className={`${NUM} mt-0.5 font-medium text-foreground`}>{formatCurrency(stock.current_price)}</dd>
                        </div>
                        <div className="min-w-0">
                          <dt className="text-xs text-muted-foreground">{t("watchlist.targetPrice")}</dt>
                          <dd className={`${NUM} mt-0.5 font-medium text-success`}>{formatCurrency(stock.target_price)}</dd>
                        </div>
                        <div className="min-w-0">
                          <dt className="text-xs text-muted-foreground">{t("watchlist.stopLoss")}</dt>
                          <dd className={`${NUM} mt-0.5 font-medium text-destructive`}>{formatCurrency(stock.stop_loss)}</dd>
                        </div>
                      </dl>

                      {/* Risk/Reward Ratio */}
                      {rr && (
                        <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1 text-xs">
                          <span className="text-muted-foreground">
                            {t("modal.riskRewardRatio")}{" "}
                            <span className={`${NUM} font-medium ${rr >= 3 ? "text-success" : rr >= 2 ? "text-foreground" : "text-destructive"}`}>
                              {rr.toFixed(1)}
                            </span>
                          </span>
                          {stock.scenario?.expected_return_pct && (
                            <span className="text-muted-foreground">
                              {t("modal.expectedReturn")}{" "}
                              <span className={`${NUM} text-success`}>+{stock.scenario.expected_return_pct.toFixed(1)}%</span>
                            </span>
                          )}
                          {stock.scenario?.expected_loss_pct && (
                            <span className="text-muted-foreground">
                              {t("modal.expectedLoss")}{" "}
                              <span className={`${NUM} text-destructive`}>-{stock.scenario.expected_loss_pct.toFixed(1)}%</span>
                            </span>
                          )}
                        </div>
                      )}

                      {/* 결정 & 사유 */}
                      <div className={`border-l-2 pl-3 ${isEntry ? "border-success/60" : "border-border"}`}>
                        <p className="text-[13px] mb-1">
                          <span className="text-muted-foreground">{t("watchlist.decision")}: </span>
                          <span className={`font-medium ${isEntry ? "text-success" : "text-foreground"}`}>{stock.decision}</span>
                          {stock.scenario?.entry_checklist_passed !== undefined && (
                            <span className="ml-2 text-xs text-muted-foreground font-mono tabular-nums">
                              ({t("watchlist.entryChecklist")}: {stock.scenario.entry_checklist_passed}/6)
                            </span>
                          )}
                        </p>
                        <p className="text-[13px] text-foreground/90 leading-relaxed max-w-[70ch]">{stock.skip_reason}</p>
                        {stock.scenario?.rejection_reason && !isEntry && (
                          <p className="text-xs text-muted-foreground mt-1.5 max-w-[70ch]">
                            {t("watchlist.rejectionReason")}: {stock.scenario.rejection_reason}
                          </p>
                        )}
                      </div>

                      {/* AI 분석 상세 (확장 가능) */}
                      <div className="flex gap-2">
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => toggleExpand(stock.id)}
                          aria-expanded={isExpanded}
                          className="flex-1 justify-between text-muted-foreground hover:text-foreground"
                        >
                          {t("watchlist.aiAnalysisDetails")}
                          {isExpanded ? <ChevronUp className="w-3.5 h-3.5" /> : <ChevronDown className="w-3.5 h-3.5" />}
                        </Button>

                        {stock.full_json_data && (
                          <Button
                            variant="outline"
                            size="sm"
                            className="font-mono text-xs"
                            onClick={() => setSelectedScenario(stock.full_json_data)}
                          >
                            JSON
                          </Button>
                        )}
                      </div>

                      {isExpanded && (
                        <div className="space-y-4 pt-3 border-t">
                          {stock.rationale && (
                            <TextBlock label={t("watchlist.tradingRationale")}>{stock.rationale}</TextBlock>
                          )}

                          {(stock.scenario?.max_portfolio_size || stock.max_portfolio_size) && (
                            <TextBlock label={t("modal.maxPortfolioSize")}>
                              <span className={NUM}>
                                {stock.scenario?.max_portfolio_size || stock.max_portfolio_size}{t("watchlist.stocksUnit")}
                              </span>
                            </TextBlock>
                          )}

                          {stock.portfolio_analysis && (
                            <TextBlock label={t("modal.portfolioAnalysis")}>{stock.portfolio_analysis}</TextBlock>
                          )}

                          {stock.valuation_analysis && (
                            <TextBlock label={t("modal.valuationAnalysis")}>{stock.valuation_analysis}</TextBlock>
                          )}

                          {stock.sector_outlook && (
                            <TextBlock label={t("modal.sectorOutlook")}>{stock.sector_outlook}</TextBlock>
                          )}

                          {stock.market_condition && (
                            <TextBlock label={t("modal.marketCondition")}>{stock.market_condition}</TextBlock>
                          )}

                          {ts && (
                            <div className="space-y-3 rounded-md border p-3">
                              <p className="text-xs font-medium text-foreground">{t("modal.tradingScenarios")}</p>

                              {ts.key_levels && typeof ts.key_levels === 'object' && (
                                <div>
                                  <p className="text-xs text-muted-foreground mb-1.5">{t("modal.keyPriceLevels")}</p>
                                  <dl className="grid grid-cols-2 gap-x-4 gap-y-2">
                                    {Object.entries(ts.key_levels).map(([key, value]) => {
                                      const labelMap: Record<string, string> = {
                                        'primary_support': t("modal.primarySupport"),
                                        'secondary_support': t("modal.secondarySupport"),
                                        'primary_resistance': t("modal.primaryResistance"),
                                        'secondary_resistance': t("modal.secondaryResistance"),
                                        'volume_baseline': t("modal.volumeBaseline")
                                      }
                                      const label = labelMap[key] || key
                                      const text = String(value)
                                      // Prose values (e.g. volume baseline notes) read better in sans across the full row.
                                      const isProse = text.length > 24

                                      return (
                                        <div key={key} className={`min-w-0 ${isProse ? "col-span-2" : ""}`}>
                                          <dt className="text-[11px] text-muted-foreground">{label}</dt>
                                          <dd className={`text-[13px] text-foreground break-words ${isProse ? "leading-relaxed max-w-[70ch]" : "font-mono tabular-nums"}`}>{text}</dd>
                                        </div>
                                      )
                                    })}
                                  </dl>
                                </div>
                              )}

                              {ts.sell_triggers && Array.isArray(ts.sell_triggers) && ts.sell_triggers.length > 0 && (
                                <div>
                                  <p className="text-xs text-muted-foreground mb-1.5">{t("modal.sellTriggers")}</p>
                                  <ul className="space-y-1 max-w-[70ch]">
                                    {ts.sell_triggers.map((trigger, idx) => (
                                      <li key={idx} className="text-[13px] text-foreground/90 flex items-start gap-2">
                                        <span className="text-destructive" aria-hidden="true">•</span>
                                        <span className="flex-1">{trigger}</span>
                                      </li>
                                    ))}
                                  </ul>
                                </div>
                              )}

                              {ts.hold_conditions && Array.isArray(ts.hold_conditions) && ts.hold_conditions.length > 0 && (
                                <div>
                                  <p className="text-xs text-muted-foreground mb-1.5">{t("modal.holdConditions")}</p>
                                  <ul className="space-y-1 max-w-[70ch]">
                                    {ts.hold_conditions.map((condition, idx) => (
                                      <li key={idx} className="text-[13px] text-foreground/90 flex items-start gap-2">
                                        <span className="text-success" aria-hidden="true">•</span>
                                        <span className="flex-1">{condition}</span>
                                      </li>
                                    ))}
                                  </ul>
                                </div>
                              )}

                              {ts.portfolio_context && (
                                <TextBlock label={t("modal.portfolioContext")}>{ts.portfolio_context}</TextBlock>
                              )}
                            </div>
                          )}
                        </div>
                      )}
                    </div>
                  )
                })}
              </div>
            </CardContent>
          </Card>
        ))}
      </div>

      {watchlist.length === 0 && (
        <Card className={CARD}>
          <CardContent className="p-4 sm:p-5">
            <p className="text-sm text-muted-foreground">
              <span className="font-medium text-foreground">{t("watchlist.noData")}</span>{" "}
              {language === "ko"
                ? "AI가 분석했지만 진입하지 않은 종목이 여기에 표시됩니다."
                : "Stocks the AI analyzed but did not enter will appear here."}
            </p>
          </CardContent>
        </Card>
      )}

      {/* Scenario JSON 모달 */}
      <Dialog open={!!selectedScenario} onOpenChange={(open) => !open && setSelectedScenario(null)}>
        <DialogContent className="max-w-4xl max-h-[80vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle className="text-sm font-medium">Scenario JSON Data</DialogTitle>
          </DialogHeader>
          <div className="mt-2">
            <div className="p-4 rounded-md bg-muted/40 border">
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
