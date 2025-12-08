"use client"

import { X, TrendingUp, TrendingDown, Activity, DollarSign, Brain, Target, AlertTriangle, Calendar, Zap } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Separator } from "@/components/ui/separator"
import { useLanguage } from "@/components/language-provider"
import type { Holding } from "@/types/dashboard"

interface StockDetailModalProps {
  stock: Holding
  onClose: () => void
  isRealTrading?: boolean
}

export function StockDetailModal({ stock, onClose, isRealTrading = false }: StockDetailModalProps) {
  const { t, language } = useLanguage()

  const formatCurrency = (value: number) => {
    return new Intl.NumberFormat("ko-KR", {
      style: "currency",
      currency: "KRW",
      maximumFractionDigits: 0,
    }).format(value ?? 0)
  }

  const formatPercent = (value: number) => {
    const safeValue = value ?? 0
    return `${safeValue >= 0 ? "+" : ""}${safeValue.toFixed(2)}%`
  }

  const formatDate = (dateString?: string) => {
    if (!dateString) return "-"
    const date = new Date(dateString)
    return date.toLocaleDateString(language === "ko" ? "ko-KR" : "en-US", {
      year: "numeric",
      month: "long",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit"
    })
  }

  const stockName = stock.company_name || stock.name || t("table.unknown")
  const scenario = stock.scenario
  
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-background/80 backdrop-blur-sm">
      <Card className="w-full max-w-4xl max-h-[90vh] overflow-y-auto border-border/50 shadow-2xl">
        <CardContent className="p-6">
          {/* 헤더 */}
          <div className="flex items-start justify-between mb-6">
            <div className="flex-1">
              <div className="flex items-center gap-3 mb-2">
                <h2 className="text-2xl font-bold text-foreground">{stockName}</h2>
                {isRealTrading ? (
                  <Badge variant="default" className="bg-gradient-to-r from-blue-600 to-indigo-600">
                    <DollarSign className="w-3 h-3 mr-1" />
                    {t("badge.realTrading")}
                  </Badge>
                ) : (
                  <>
                    <Badge variant="default" className="bg-gradient-to-r from-purple-600 to-pink-600">
                      <Brain className="w-3 h-3 mr-1" />
                      {t("badge.aiSimulation")}
                    </Badge>
                    {scenario?.sector && (
                      <Badge variant="outline">{scenario.sector}</Badge>
                    )}
                  </>
                )}
              </div>
              <div className="flex items-center gap-2 flex-wrap">
                <p className="text-sm text-muted-foreground">{stock.ticker}</p>
                {isRealTrading && (
                  <Badge variant="secondary" className="text-xs">
                    <Activity className="w-3 h-3 mr-1" />
                    {t("badge.koreanInvestment")}
                  </Badge>
                )}
                {!isRealTrading && scenario?.investment_period && (
                  <Badge variant="secondary" className="text-xs">
                    <Calendar className="w-3 h-3 mr-1" />
                    {scenario.investment_period}
                  </Badge>
                )}
                {!isRealTrading && scenario?.buy_score && (
                  <Badge variant="secondary" className="text-xs">
                    <Zap className="w-3 h-3 mr-1" />
                    {t("modal.buyScore")} {scenario.buy_score}/10
                  </Badge>
                )}
              </div>
            </div>
            <Button variant="ghost" size="icon" onClick={onClose} className="rounded-full">
              <X className="w-5 h-5" />
            </Button>
          </div>

          {/* 현재가 및 수익률 */}
          <div className="grid grid-cols-2 gap-4 mb-6">
            <div className="p-4 rounded-lg bg-muted/30 border border-border/30">
              <p className="text-sm text-muted-foreground mb-1">{t("modal.currentPrice")}</p>
              <p className="text-2xl font-bold text-foreground">{formatCurrency(stock.current_price ?? 0)}</p>
            </div>
            <div className="p-4 rounded-lg bg-muted/30 border border-border/30">
              <p className="text-sm text-muted-foreground mb-1">{t("modal.profitRate")}</p>
              <div className="flex items-center gap-2">
                {(stock.profit_rate ?? 0) >= 0 ? (
                  <TrendingUp className="w-5 h-5 text-success" />
                ) : (
                  <TrendingDown className="w-5 h-5 text-destructive" />
                )}
                <p className={`text-2xl font-bold ${(stock.profit_rate ?? 0) >= 0 ? "text-success" : "text-destructive"}`}>
                  {formatPercent(stock.profit_rate ?? 0)}
                </p>
              </div>
            </div>
          </div>

          {/* 실전투자 상세 정보 */}
          {isRealTrading && (
            <>
              <div className="space-y-3 mb-6">
                <h3 className="text-sm font-semibold text-foreground mb-3">{t("modal.holdingInfo")}</h3>
                <div className="flex justify-between items-center py-3 border-b border-border/30">
                  <span className="text-sm text-muted-foreground">{t("modal.holdingQuantity")}</span>
                  <span className="font-medium text-foreground">{(stock.quantity ?? 0).toLocaleString()}{t("common.shares")}</span>
                </div>
                <div className="flex justify-between items-center py-3 border-b border-border/30">
                  <span className="text-sm text-muted-foreground">{t("modal.avgBuyPrice")}</span>
                  <span className="font-medium text-foreground">{formatCurrency(stock.avg_price ?? 0)}</span>
                </div>
                <div className="flex justify-between items-center py-3 border-b border-border/30">
                  <span className="text-sm text-muted-foreground">{t("modal.evaluationAmount")}</span>
                  <span className="font-medium text-foreground">{formatCurrency(stock.value ?? 0)}</span>
                </div>
                <div className="flex justify-between items-center py-3 border-b border-border/30">
                  <span className="text-sm text-muted-foreground">{t("modal.evaluationPL")}</span>
                  <span className={`font-semibold ${(stock.profit ?? 0) >= 0 ? "text-success" : "text-destructive"}`}>
                    {formatCurrency(stock.profit ?? 0)}
                  </span>
                </div>
                {stock.weight !== undefined && stock.weight > 0 && (
                  <div className="flex justify-between items-center py-3">
                    <span className="text-sm text-muted-foreground">{t("modal.portfolioWeight")}</span>
                    <span className="font-medium text-foreground">{(stock.weight ?? 0).toFixed(2)}%</span>
                  </div>
                )}
              </div>
            </>
          )}

          {/* 시뮬레이터 상세 정보 */}
          {!isRealTrading && (
            <>
              <div className="space-y-3 mb-6">
                <h3 className="text-sm font-semibold text-foreground mb-3">{t("modal.tradingInfo")}</h3>
                <div className="flex justify-between items-center py-3 border-b border-border/30">
                  <span className="text-sm text-muted-foreground">{t("modal.buyDate")}</span>
                  <span className="font-medium text-foreground">{formatDate(stock.buy_date)}</span>
                </div>
                <div className="flex justify-between items-center py-3 border-b border-border/30">
                  <span className="text-sm text-muted-foreground">{t("modal.buyPrice")}</span>
                  <span className="font-medium text-foreground">{formatCurrency(stock.buy_price ?? 0)}</span>
                </div>
                <div className="flex justify-between items-center py-3 border-b border-border/30">
                  <span className="text-sm text-muted-foreground">{t("modal.targetPrice")}</span>
                  <span className="font-medium text-success">{formatCurrency(stock.target_price ?? 0)}</span>
                </div>
                <div className="flex justify-between items-center py-3 border-b border-border/30">
                  <span className="text-sm text-muted-foreground">{t("modal.stopLossPrice")}</span>
                  <span className="font-medium text-destructive">{formatCurrency(stock.stop_loss ?? 0)}</span>
                </div>
                <div className="flex justify-between items-center py-3 border-b border-border/30">
                  <span className="text-sm text-muted-foreground">{t("modal.holdingDays")}</span>
                  <span className="font-medium text-foreground">{stock.holding_days ?? 0}{t("common.days")}</span>
                </div>
                <div className="flex justify-between items-center py-3">
                  <span className="text-sm text-muted-foreground">{t("modal.lastUpdate")}</span>
                  <span className="font-medium text-foreground text-xs">{formatDate(stock.last_updated)}</span>
                </div>
              </div>
            </>
          )}

          {/* AI 매매 시나리오 (공통) */}
          {scenario && (
            <>
              <Separator className="my-6" />
              
              <div className="space-y-4">
                <div className="flex items-center gap-2 mb-4">
                  <Brain className="w-5 h-5 text-primary" />
                  <h3 className="text-lg font-semibold text-foreground">{t("modal.aiTradingScenario")}</h3>
                </div>

                {/* 매수 결정 */}
                {scenario.decision && (
                  <div className="p-4 rounded-lg bg-primary/10 border border-primary/20">
                    <div className="flex items-center justify-between mb-2">
                      <p className="text-sm font-semibold text-primary">{t("modal.buyDecision")}</p>
                      <Badge variant={scenario.decision === t("watchlist.entry") ? "default" : "secondary"}>
                        {scenario.decision}
                      </Badge>
                    </div>
                    {scenario.rationale && (
                      <p className="text-sm text-muted-foreground leading-relaxed">{scenario.rationale}</p>
                    )}
                  </div>
                )}

                {/* 목표가/손절가 */}
                {(scenario.target_price || scenario.stop_loss) && (
                  <div className="grid grid-cols-2 gap-3">
                    {scenario.target_price && (
                      <div className="p-3 rounded-lg bg-success/10 border border-success/20">
                        <div className="flex items-center gap-2 mb-1">
                          <Target className="w-4 h-4 text-success" />
                          <p className="text-xs font-semibold text-success">{t("modal.targetPrice")}</p>
                        </div>
                        <p className="text-lg font-bold text-foreground">{formatCurrency(scenario.target_price)}</p>
                      </div>
                    )}
                    {scenario.stop_loss && (
                      <div className="p-3 rounded-lg bg-destructive/10 border border-destructive/20">
                        <div className="flex items-center gap-2 mb-1">
                          <AlertTriangle className="w-4 h-4 text-destructive" />
                          <p className="text-xs font-semibold text-destructive">{t("modal.stopLossPrice")}</p>
                        </div>
                        <p className="text-lg font-bold text-foreground">{formatCurrency(scenario.stop_loss)}</p>
                      </div>
                    )}
                  </div>
                )}

                {/* Risk/Reward Ratio */}
                {scenario.risk_reward_ratio && (
                  <div className="p-4 rounded-lg bg-gradient-to-r from-blue-50 to-indigo-50 dark:from-blue-950/30 dark:to-indigo-950/30 border border-blue-200/50 dark:border-blue-800/50">
                    <div className="flex items-center justify-between mb-2">
                      <div className="flex items-center gap-2">
                        <TrendingUp className="w-4 h-4 text-blue-600 dark:text-blue-400" />
                        <p className="text-sm font-semibold text-blue-700 dark:text-blue-300">{t("modal.riskRewardRatio")}</p>
                      </div>
                      <Badge 
                        variant={scenario.risk_reward_ratio >= 3 ? "default" : scenario.risk_reward_ratio >= 2 ? "secondary" : "destructive"}
                        className="text-xs"
                      >
                        {scenario.risk_reward_ratio.toFixed(1)}
                      </Badge>
                    </div>
                    <div className="grid grid-cols-2 gap-3 mt-2">
                      {scenario.expected_return_pct && (
                        <div>
                          <p className="text-xs text-muted-foreground">{t("modal.expectedReturn")}</p>
                          <p className="text-sm font-semibold text-success">+{scenario.expected_return_pct.toFixed(1)}%</p>
                        </div>
                      )}
                      {scenario.expected_loss_pct && (
                        <div>
                          <p className="text-xs text-muted-foreground">{t("modal.expectedLoss")}</p>
                          <p className="text-sm font-semibold text-destructive">-{scenario.expected_loss_pct.toFixed(1)}%</p>
                        </div>
                      )}
                    </div>
                  </div>
                )}

                {/* 포트폴리오 분석 */}
                {scenario.portfolio_analysis && (
                  <div className="p-4 rounded-lg bg-muted/30 border border-border/30">
                    <p className="text-xs font-semibold text-muted-foreground mb-2">{t("modal.portfolioAnalysis")}</p>
                    <p className="text-sm text-foreground leading-relaxed">{scenario.portfolio_analysis}</p>
                  </div>
                )}

                {/* 밸류에이션 분석 */}
                {scenario.valuation_analysis && (
                  <div className="p-4 rounded-lg bg-muted/30 border border-border/30">
                    <p className="text-xs font-semibold text-muted-foreground mb-2">{t("modal.valuationAnalysis")}</p>
                    <p className="text-sm text-foreground leading-relaxed">{scenario.valuation_analysis}</p>
                  </div>
                )}

                {/* 섹터 전망 */}
                {scenario.sector_outlook && (
                  <div className="p-4 rounded-lg bg-muted/30 border border-border/30">
                    <p className="text-xs font-semibold text-muted-foreground mb-2">{t("modal.sectorOutlook")}</p>
                    <p className="text-sm text-foreground leading-relaxed">{scenario.sector_outlook}</p>
                  </div>
                )}

                {/* 시장 상황 */}
                {scenario.market_condition && (
                  <div className="p-4 rounded-lg bg-muted/30 border border-border/30">
                    <p className="text-xs font-semibold text-muted-foreground mb-2">{t("modal.marketCondition")}</p>
                    <p className="text-sm text-foreground leading-relaxed">{scenario.market_condition}</p>
                  </div>
                )}

                {/* 최대 포트폴리오 규모 */}
                {scenario.max_portfolio_size && (
                  <div className="p-4 rounded-lg bg-indigo-500/10 border border-indigo-500/20">
                    <p className="text-xs font-semibold text-indigo-600 dark:text-indigo-400 mb-2">{t("modal.maxPortfolioSize")}</p>
                    <p className="text-sm text-foreground leading-relaxed">{scenario.max_portfolio_size}</p>
                  </div>
                )}

                {/* 주요 가격대 */}
                {scenario.trading_scenarios?.key_levels && Object.keys(scenario.trading_scenarios.key_levels).length > 0 && (
                  <div className="p-4 rounded-lg bg-blue-500/10 border border-blue-500/20">
                    <p className="text-xs font-semibold text-blue-600 dark:text-blue-400 mb-3">{t("modal.keyPriceLevels")}</p>
                    <div className="grid grid-cols-2 gap-2">
                      {Object.entries(scenario.trading_scenarios.key_levels).map(([key, value]) => {
                        // 한글 레이블 매핑
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
                              {typeof value === 'number' ? formatCurrency(value) : String(value)}
                            </p>
                          </div>
                        )
                      })}
                    </div>
                  </div>
                )}

                {/* 매도 트리거 */}
                {scenario.trading_scenarios?.sell_triggers && scenario.trading_scenarios.sell_triggers.length > 0 && (
                  <div className="p-4 rounded-lg bg-destructive/10 border border-destructive/20">
                    <p className="text-xs font-semibold text-destructive mb-3">{t("modal.sellTriggers")}</p>
                    <ul className="space-y-2">
                      {scenario.trading_scenarios.sell_triggers.map((trigger, index) => (
                        <li key={index} className="text-sm text-foreground flex items-start gap-2">
                          <span className="text-destructive mt-0.5">•</span>
                          <span className="flex-1">{trigger}</span>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}

                {/* 보유 조건 */}
                {scenario.trading_scenarios?.hold_conditions && scenario.trading_scenarios.hold_conditions.length > 0 && (
                  <div className="p-4 rounded-lg bg-success/10 border border-success/20">
                    <p className="text-xs font-semibold text-success mb-3">{t("modal.holdConditions")}</p>
                    <ul className="space-y-2">
                      {scenario.trading_scenarios.hold_conditions.map((condition, index) => (
                        <li key={index} className="text-sm text-foreground flex items-start gap-2">
                          <span className="text-success mt-0.5">•</span>
                          <span className="flex-1">{condition}</span>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}

                {/* 포트폴리오 맥락 */}
                {scenario.trading_scenarios?.portfolio_context && (
                  <div className="p-4 rounded-lg bg-purple-500/10 border border-purple-500/20">
                    <p className="text-xs font-semibold text-purple-600 dark:text-purple-400 mb-2">{t("modal.portfolioContext")}</p>
                    <p className="text-sm text-foreground leading-relaxed">{scenario.trading_scenarios.portfolio_context}</p>
                  </div>
                )}
              </div>
            </>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
