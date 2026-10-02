"use client"

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { Badge } from "@/components/ui/badge"
import { TrendingUp, TrendingDown } from "lucide-react"
import { useLanguage } from "@/components/language-provider"
import { formatCurrency as formatCurrencyUtil, formatPercent as formatPercentUtil } from "@/lib/currency"
import type { Holding, Market } from "@/types/dashboard"

interface HoldingsTableProps {
  holdings: Holding[]
  onStockClick: (stock: Holding) => void
  title?: string
  isRealTrading?: boolean
  market?: Market
}

export function HoldingsTable({ holdings, onStockClick, title = "보유 종목", isRealTrading = false, market = "KR" }: HoldingsTableProps) {
  const { language, t } = useLanguage()

  const isUSMarket = market === "US"

  const formatCurrency = (value: number | undefined) => {
    if (value === undefined || value === null) return isUSMarket ? "$0.00" : "₩0"
    return formatCurrencyUtil(value, market, language as "ko" | "en")
  }

  const formatPercent = (value: number | undefined) => {
    if (value === undefined || value === null) return "0.00%"
    return formatPercentUtil(value, true)
  }

  const formatWeight = (value: number | undefined) => {
    if (value === undefined || value === null) return "-"
    return `${value.toFixed(2)}%`
  }

  // Market-specific styling
  const cardBorderClass = isRealTrading
    ? (isUSMarket ? 'border-emerald-500/30 bg-gradient-to-br from-emerald-50/50 to-transparent dark:from-emerald-950/20' : 'border-blue-500/30 bg-gradient-to-br from-blue-50/50 to-transparent dark:from-blue-950/20')
    : ''
  const badgeGradientClass = isUSMarket
    ? "bg-gradient-to-r from-emerald-600 to-teal-600"
    : "bg-gradient-to-r from-blue-600 to-indigo-600"
  const badgeOutlineClass = isUSMarket
    ? "border-emerald-500/50 text-emerald-600 dark:text-emerald-400"
    : "border-blue-500/50 text-blue-600 dark:text-blue-400"
  const simulatorBadgeClass = isUSMarket
    ? "border-teal-500/50 text-teal-600 dark:text-teal-400"
    : "border-purple-500/50 text-purple-600 dark:text-purple-400"

  return (
    <Card className={`border-border/50 ${cardBorderClass}`}>
      <CardHeader>
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <CardTitle className="text-lg font-semibold">{title}</CardTitle>
            {isRealTrading ? (
              <div className="flex items-center gap-2">
                <Badge variant="default" className={badgeGradientClass}>
                  {isUSMarket ? (language === "ko" ? "미국 실전투자" : "US Real") : t("badge.realTrading")}
                </Badge>
                <Badge variant="outline" className={badgeOutlineClass}>
                  {isUSMarket ? "Season 1" : t("badge.season2")}
                </Badge>
              </div>
            ) : (
              <Badge variant="outline" className={simulatorBadgeClass}>
                {isUSMarket ? (language === "ko" ? "미국 시뮬레이션" : "US Simulation") : t("badge.aiSimulation")}
              </Badge>
            )}
          </div>
        </div>
      </CardHeader>
      <CardContent>
        <div className="overflow-x-auto">
          <Table>
            <TableHeader>
              <TableRow className="hover:bg-transparent border-border/50">
                <TableHead className="font-semibold">{t("table.stockName")}</TableHead>
                {!isRealTrading && <TableHead className="font-semibold">{t("table.sector")}</TableHead>}
                {isRealTrading ? (
                  <>
                    <TableHead className="text-right font-semibold">{t("table.quantity")}</TableHead>
                    <TableHead className="text-right font-semibold">{t("table.avgPrice")}</TableHead>
                    <TableHead className="text-right font-semibold">{t("table.currentPrice")}</TableHead>
                    <TableHead className="text-right font-semibold">{t("table.totalValue")}</TableHead>
                    <TableHead className="text-right font-semibold">{t("table.profitAmount")}</TableHead>
                    <TableHead className="text-right font-semibold">{t("table.profitRate")}</TableHead>
                    <TableHead className="text-right font-semibold">{t("table.weight")}</TableHead>
                  </>
                ) : (
                  <>
                    <TableHead className="text-right font-semibold">{t("table.buyPrice")}</TableHead>
                    <TableHead className="text-right font-semibold">{t("table.currentPrice")}</TableHead>
                    <TableHead className="text-right font-semibold">{t("table.targetPrice")}</TableHead>
                    <TableHead className="text-right font-semibold">{t("table.stopLoss")}</TableHead>
                    <TableHead className="text-right font-semibold">{t("table.profitRate")}</TableHead>
                    <TableHead className="text-right font-semibold">{t("table.holdingDays")}</TableHead>
                    <TableHead className="text-right font-semibold">{t("table.period")}</TableHead>
                  </>
                )}
              </TableRow>
            </TableHeader>
            <TableBody>
              {holdings.map((holding) => {
                const stockName = holding.company_name || holding.name || t("table.unknown")
                const buyPrice = holding.buy_price || holding.avg_price || 0
                
                return (
                  <TableRow
                    key={holding.ticker}
                    className="cursor-pointer hover:bg-muted/50 transition-colors border-border/30"
                    onClick={() => onStockClick(holding)}
                  >
                    <TableCell>
                      <div>
                        <p className="font-medium text-foreground">{stockName}</p>
                        <p className="text-xs text-muted-foreground">{holding.ticker}</p>
                      </div>
                    </TableCell>
                    
                    {!isRealTrading && (
                      <TableCell>
                        <Badge variant="outline" className="text-xs">
                          {holding.sector || holding.scenario?.sector || "-"}
                        </Badge>
                      </TableCell>
                    )}
                    
                    {isRealTrading ? (
                      <>
                        <TableCell className="text-right font-medium">
                          {(holding.quantity || 0).toLocaleString()}{t("common.shares")}
                        </TableCell>
                        <TableCell className="text-right text-muted-foreground">
                          {formatCurrency(holding.avg_price)}
                        </TableCell>
                        <TableCell className="text-right font-medium">
                          {formatCurrency(holding.current_price)}
                        </TableCell>
                        <TableCell className="text-right font-semibold">
                          {formatCurrency(holding.value)}
                        </TableCell>
                        <TableCell className="text-right">
                          <span className={`font-semibold ${(holding.profit || 0) >= 0 ? "text-success" : "text-destructive"}`}>
                            {formatCurrency(holding.profit)}
                          </span>
                        </TableCell>
                        <TableCell className="text-right">
                          <div className="flex items-center justify-end gap-1">
                            {(holding.profit_rate || 0) >= 0 ? (
                              <TrendingUp className="w-3 h-3 text-success" />
                            ) : (
                              <TrendingDown className="w-3 h-3 text-destructive" />
                            )}
                            <span className={`font-semibold ${(holding.profit_rate || 0) >= 0 ? "text-success" : "text-destructive"}`}>
                              {formatPercent(holding.profit_rate)}
                            </span>
                          </div>
                        </TableCell>
                        <TableCell className="text-right text-muted-foreground">
                          {formatWeight(holding.weight)}
                        </TableCell>
                      </>
                    ) : (
                      <>
                        <TableCell className="text-right text-muted-foreground">
                          {formatCurrency(buyPrice)}
                          {(holding.allocation ?? 1) < 1 || (holding.add_count || 0) > 0 ? (
                            <div className="mt-1 flex flex-col items-end gap-0.5">
                              <Badge variant="outline" className="text-[10px] px-1.5 py-0">
                                {language === "ko"
                                  ? holding.allocation_label || `비중 ${Math.round((holding.allocation ?? 1) * 100)}%`
                                  : (holding.allocation_label || `${Math.round((holding.allocation ?? 1) * 100)}%`).replace("비중", "Alloc")}
                              </Badge>
                              {(holding.add_count || 0) > 0 && holding.average_entry ? (
                                <span className="text-[10px]">
                                  {language === "ko" ? "평균 " : "Avg "}{formatCurrency(holding.average_entry)}
                                </span>
                              ) : null}
                            </div>
                          ) : null}
                        </TableCell>
                        <TableCell className="text-right font-medium">
                          {formatCurrency(holding.current_price)}
                        </TableCell>
                        <TableCell className="text-right text-success">
                          {formatCurrency(holding.target_price)}
                        </TableCell>
                        <TableCell className="text-right text-destructive">
                          {formatCurrency(holding.stop_loss)}
                        </TableCell>
                        <TableCell className="text-right">
                          <div className="flex items-center justify-end gap-1">
                            {(holding.profit_rate || 0) >= 0 ? (
                              <TrendingUp className="w-3 h-3 text-success" />
                            ) : (
                              <TrendingDown className="w-3 h-3 text-destructive" />
                            )}
                            <span className={`font-semibold ${(holding.profit_rate || 0) >= 0 ? "text-success" : "text-destructive"}`}>
                              {formatPercent(holding.profit_rate)}
                            </span>
                          </div>
                          {(holding.allocation ?? 1) < 1 && holding.slot_profit_rate !== undefined ? (
                            <div className="text-[10px] text-muted-foreground">
                              {language === "ko" ? "슬롯 기준 " : "Slot "}{formatPercent(holding.slot_profit_rate)}
                            </div>
                          ) : null}
                        </TableCell>
                        <TableCell className="text-right text-muted-foreground">
                          {holding.holding_days || 0}{t("common.days")}
                        </TableCell>
                        <TableCell className="text-right">
                          <Badge variant={holding.investment_period === "단기" ? "secondary" : "outline"} className="text-xs">
                            {holding.investment_period || "-"}
                          </Badge>
                        </TableCell>
                      </>
                    )}
                  </TableRow>
                )
              })}
            </TableBody>
          </Table>
        </div>
      </CardContent>
    </Card>
  )
}
