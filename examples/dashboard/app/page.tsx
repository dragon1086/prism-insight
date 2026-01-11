"use client"

import { useState, useEffect, Suspense } from "react"
import { useSearchParams, useRouter } from "next/navigation"
import { DashboardHeader } from "@/components/dashboard-header"
import { OperatingCostsCard } from "@/components/operating-costs-card"
import { MetricsCards } from "@/components/metrics-cards"
import { HoldingsTable } from "@/components/holdings-table"
import { PerformanceChart } from "@/components/performance-chart"
import { AIDecisionsPage } from "@/components/ai-decisions-page"
import { TradingHistoryPage } from "@/components/trading-history-page"
import { WatchlistPage } from "@/components/watchlist-page"
import { TradingInsightsPage } from "@/components/trading-insights-page"
import { JeoninguLabPage } from "@/components/jeoningu-lab-page"
import { StockDetailModal } from "@/components/stock-detail-modal"
import { ProjectFooter } from "@/components/project-footer"
import { useLanguage } from "@/components/language-provider"
import type { DashboardData, Holding } from "@/types/dashboard"

type TabType = "dashboard" | "ai-decisions" | "trading" | "watchlist" | "insights" | "jeoningu-lab"
const VALID_TABS: TabType[] = ["dashboard", "ai-decisions", "trading", "watchlist", "insights", "jeoningu-lab"]

// Suspense 경계를 위한 로딩 컴포넌트
function LoadingSpinner() {
  return (
    <div className="flex items-center justify-center min-h-screen">
      <div className="text-center">
        <div className="w-16 h-16 border-4 border-primary border-t-transparent rounded-full animate-spin mx-auto mb-4" />
        <p className="text-muted-foreground">Loading...</p>
      </div>
    </div>
  )
}

// 메인 대시보드 컴포넌트 (useSearchParams 사용)
function DashboardContent() {
  const { language, t } = useLanguage()
  const searchParams = useSearchParams()
  const router = useRouter()
  const [data, setData] = useState<DashboardData | null>(null)
  const [selectedStock, setSelectedStock] = useState<Holding | null>(null)
  const [isRealTrading, setIsRealTrading] = useState(false)

  // URL에서 탭 파라미터 읽기
  const tabParam = searchParams.get("tab") as TabType | null
  const activeTab: TabType = tabParam && VALID_TABS.includes(tabParam) ? tabParam : "dashboard"

  // 탭 변경 시 URL 업데이트
  const handleTabChange = (tab: TabType) => {
    const params = new URLSearchParams(searchParams.toString())
    if (tab === "dashboard") {
      params.delete("tab")
    } else {
      params.set("tab", tab)
    }
    const queryString = params.toString()
    router.push(queryString ? `?${queryString}` : "/", { scroll: false })
  }

  useEffect(() => {
    const fetchData = async () => {
      try {
        const dataFile = language === "en" ? "/dashboard_data_en.json" : "/dashboard_data.json"
        const response = await fetch(dataFile)
        const jsonData = await response.json()
        setData(jsonData)
      } catch (error) {
        console.error("[v0] Failed to fetch dashboard data:", error)
      }
    }

    fetchData()
    const interval = setInterval(fetchData, 5 * 60 * 1000) // 5분마다 갱신

    return () => clearInterval(interval)
  }, [language])

  const handleStockClick = (stock: Holding, isReal: boolean) => {
    setSelectedStock(stock)
    setIsRealTrading(isReal)
  }

  if (!data) {
    return (
      <div className="flex items-center justify-center min-h-screen">
        <div className="text-center">
          <div className="w-16 h-16 border-4 border-primary border-t-transparent rounded-full animate-spin mx-auto mb-4" />
          <p className="text-muted-foreground">{t("loading.text")}</p>
        </div>
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-background">
      <DashboardHeader activeTab={activeTab} onTabChange={handleTabChange} lastUpdated={data.generated_at} />

      <main className="container mx-auto px-4 py-6 max-w-[1600px]">
        {activeTab === "dashboard" && (
          <div className="space-y-6">
            {/* 운영 비용 카드 - 최상단 배치 */}
            <OperatingCostsCard costs={data.operating_costs} />

            {/* 핵심 지표 카드 */}
            <MetricsCards
              summary={data.summary}
              realPortfolio={data.real_portfolio || []}
              tradingHistoryCount={data.trading_history?.length || 0}
              tradingHistoryTotalProfit={
                data.trading_history?.reduce((sum, trade) => sum + trade.profit_rate, 0) || 0
              }
              tradingHistoryAvgProfit={
                data.trading_history?.length > 0
                  ? data.trading_history.reduce((sum, trade) => sum + trade.profit_rate, 0) / data.trading_history.length
                  : 0
              }
              tradingHistoryAvgDays={
                data.trading_history?.length > 0
                  ? data.trading_history.reduce((sum, trade) => sum + trade.holding_days, 0) / data.trading_history.length
                  : 0
              }
              tradingHistoryWinRate={
                data.trading_history?.length > 0
                  ? (data.trading_history.filter(t => t.profit_rate > 0).length / data.trading_history.length) * 100
                  : 0
              }
              tradingHistoryWinCount={
                data.trading_history?.filter(t => t.profit_rate > 0).length || 0
              }
              tradingHistoryLossCount={
                data.trading_history?.filter(t => t.profit_rate <= 0).length || 0
              }
            />

            {/* 실전투자 포트폴리오 - 최우선 표시 */}
            {data.real_portfolio && data.real_portfolio.length > 0 && (
              <HoldingsTable
                holdings={data.real_portfolio}
                onStockClick={(stock) => handleStockClick(stock, true)}
                title={t("table.realPortfolio")}
                isRealTrading={true}
              />
            )}

            {/* 프리즘 시뮬레이터 */}
            <HoldingsTable
              holdings={data.holdings}
              onStockClick={(stock) => handleStockClick(stock, false)}
              title={t("table.simulator")}
              isRealTrading={false}
            />

            {/* 시장 지수 차트 - 하단 배치 */}
            <PerformanceChart
              data={data.market_condition}
              prismPerformance={data.prism_performance}
              holdings={data.holdings}
              summary={data.summary}
            />
          </div>
        )}

        {activeTab === "ai-decisions" && <AIDecisionsPage data={data} />}

        {activeTab === "trading" && <TradingHistoryPage history={data.trading_history} summary={data.summary} prismPerformance={data.prism_performance} marketCondition={data.market_condition} />}

        {activeTab === "watchlist" && <WatchlistPage watchlist={data.watchlist} />}

        {activeTab === "insights" && data.trading_insights && <TradingInsightsPage data={data.trading_insights} />}

        {activeTab === "jeoningu-lab" && data.jeoningu_lab && <JeoninguLabPage data={data.jeoningu_lab} />}
      </main>

      {/* 프로젝트 소개 Footer */}
      <ProjectFooter />

      {selectedStock && (
        <StockDetailModal 
          stock={selectedStock} 
          onClose={() => setSelectedStock(null)} 
          isRealTrading={isRealTrading}
        />
      )}
    </div>
  )
}

// 메인 페이지 컴포넌트 - Suspense 경계로 래핑
export default function Page() {
  return (
    <Suspense fallback={<LoadingSpinner />}>
      <DashboardContent />
    </Suspense>
  )
}