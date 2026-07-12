"use client"

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Line, LineChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis, Legend } from "recharts"
import type { MarketCondition, PrismPerformance, Holding, Summary, Market } from "@/types/dashboard"
import { useLanguage } from "@/components/language-provider"
import { getSeasonInfo } from "@/lib/currency"

interface PerformanceChartProps {
  data: MarketCondition[]
  prismPerformance?: PrismPerformance[]
  holdings?: Holding[]
  summary?: Summary
  market?: Market
}

export function PerformanceChart({ data, prismPerformance = [], holdings = [], summary, market = "KR" }: PerformanceChartProps) {
  const { t, language } = useLanguage()
  const isUSMarket = market === "US"
  const seasonInfo = getSeasonInfo(market)

  const formatNumber = (value: number) => {
    return new Intl.NumberFormat("ko-KR", {
      maximumFractionDigits: 0,
    }).format(value)
  }

  const formatPercent = (value: number) => {
    return `${value >= 0 ? "+" : ""}${value.toFixed(2)}%`
  }

  // Season start amount and date based on market
  const seasonStartAmount = seasonInfo.startAmount
  const seasonStartDate = seasonInfo.startDate

  // 데이터를 날짜 기준으로 오름차순 정렬
  const sortedData = [...data].sort((a, b) => {
    return new Date(a.date).getTime() - new Date(b.date).getTime()
  })

  // Season 시작 시점 이후 데이터만 필터링
  const filteredData = sortedData.filter(d => d.date >= seasonStartDate)

  if (filteredData.length === 0) {
    return (
      <Card className="border-border/50">
        <CardHeader>
          <CardTitle className="text-lg font-semibold">
            {language === "ko"
              ? `수익률 비교 (${seasonInfo.seasonName} 시작 이후)`
              : `Return Comparison (Since ${seasonInfo.seasonName})`}
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="flex items-center justify-center h-[300px] text-muted-foreground">
            {language === "ko" ? "데이터가 없습니다." : "No data available."}
          </div>
        </CardContent>
      </Card>
    )
  }

  // 시작 시점의 지수 값 - US or KR
  const startIndex1 = isUSMarket
    ? (filteredData[0]?.spx_index || 0)
    : (filteredData[0]?.kospi_index || 0)
  const startIndex2 = isUSMarket
    ? (filteredData[0]?.nasdaq_index || 0)
    : (filteredData[0]?.kosdaq_index || 0)

  // Index names based on market
  const index1Name = isUSMarket ? "S&P 500" : "KOSPI"
  const index2Name = isUSMarket ? "NASDAQ" : "KOSDAQ"
  const index1Color = isUSMarket ? "#8b5cf6" : "#3b82f6"  // purple for S&P, blue for KOSPI
  const index2Color = isUSMarket ? "#06b6d4" : "#10b981"  // cyan for NASDAQ, emerald for KOSDAQ

  // 프리즘 퍼포먼스 데이터를 날짜 기준으로 맵핑
  const prismPerformanceMap = new Map<string, PrismPerformance>()
  prismPerformance.forEach(p => {
    prismPerformanceMap.set(p.date, p)
  })

  // 최신 프리즘 시뮬레이터 수익률 (누적 실현 수익률만)
  const latestPrismPerformance = prismPerformance.length > 0
    ? prismPerformance[prismPerformance.length - 1]
    : null
  const latestPrismReturn = latestPrismPerformance
    ? latestPrismPerformance.prism_simulator_return
    : 0

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

  // Index1 기준 차트 데이터 (KOSPI or S&P 500)
  const index1ChartData = filteredData.map((item) => {
    const currentIndex = isUSMarket ? (item.spx_index || 0) : (item.kospi_index || 0)
    const indexReturn = startIndex1 > 0 ? ((currentIndex - startIndex1) / startIndex1) * 100 : 0

    // 해당 날짜의 프리즘 퍼포먼스 찾기
    const prismData = prismPerformanceMap.get(item.date)
    const prismReturn = prismData ? prismData.prism_simulator_return : 0

    return {
      date: item.date,
      market_return: indexReturn,
      prism_return: prismReturn,
    }
  })

  // Index2 기준 차트 데이터 (KOSDAQ or NASDAQ)
  const index2ChartData = filteredData.map((item) => {
    const currentIndex = isUSMarket ? (item.nasdaq_index || 0) : (item.kosdaq_index || 0)
    const indexReturn = startIndex2 > 0 ? ((currentIndex - startIndex2) / startIndex2) * 100 : 0

    // 해당 날짜의 프리즘 퍼포먼스 찾기
    const prismData = prismPerformanceMap.get(item.date)
    const prismReturn = prismData ? prismData.prism_simulator_return : 0

    return {
      date: item.date,
      market_return: indexReturn,
      prism_return: prismReturn,
    }
  })

  // 최신 값 계산
  const latestIndex1 = index1ChartData[index1ChartData.length - 1]
  const latestIndex2 = index2ChartData[index2ChartData.length - 1]

  // Y축 도메인 계산
  const getAllValues = (chartData: typeof index1ChartData) => {
    return chartData.flatMap(d => [
      d.market_return,
      d.prism_return
    ])
  }

  const getYDomain = (values: number[]): [number, number] => {
    const minValue = Math.min(...values)
    const maxValue = Math.max(...values)
    const padding = Math.max(Math.abs(maxValue - minValue) * 0.15, 2)
    return [Math.floor(minValue - padding), Math.ceil(maxValue + padding)]
  }

  const index1YDomain = getYDomain(getAllValues(index1ChartData))
  const index2YDomain = getYDomain(getAllValues(index2ChartData))

  const ComparisonChart = ({
    chartData,
    title,
    marketColor,
    yDomain,
    latestData,
    mdd,
    indexName
  }: {
    chartData: typeof index1ChartData
    title: string
    marketColor: string
    yDomain: [number, number]
    latestData: typeof latestIndex1
    mdd: number
    indexName: string
  }) => {
    const { t } = useLanguage()

    return (
    <Card className="border-border/50">
      <CardHeader>
        <div className="flex items-center justify-between">
          <CardTitle className="text-lg font-semibold">{title}</CardTitle>
          <div className="text-right text-xs space-y-1">
            <div className="flex items-center gap-3">
              <span className="text-muted-foreground">
                {t("chart.market")}: <span style={{ color: marketColor }} className="font-semibold">{formatPercent(latestData.market_return)}</span>
              </span>
              <span className="text-muted-foreground">
                {t("chart.prism")}: <span className={`font-semibold ${latestData.prism_return >= 0 ? 'text-purple-600 dark:text-purple-400' : 'text-destructive'}`}>{formatPercent(latestData.prism_return)}</span>
              </span>
              {mdd > 0 && (
                <span className="text-muted-foreground">
                  MDD: <span className="font-semibold text-orange-600 dark:text-orange-400">-{mdd.toFixed(2)}%</span>
                </span>
              )}
            </div>
          </div>
        </div>
      </CardHeader>
      <CardContent>
        <ResponsiveContainer width="100%" height={320}>
          <LineChart data={chartData}>
            <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" opacity={0.3} />
            <XAxis
              dataKey="date"
              stroke="hsl(var(--muted-foreground))"
              fontSize={11}
              tickLine={false}
              axisLine={false}
              tickFormatter={(value) => {
                const date = new Date(value)
                return `${date.getMonth() + 1}/${date.getDate()}`
              }}
            />
            <YAxis
              stroke="hsl(var(--muted-foreground))"
              fontSize={11}
              tickLine={false}
              axisLine={false}
              tickFormatter={(value) => `${value.toFixed(1)}%`}
              domain={yDomain}
            />
            <Tooltip
              contentStyle={{
                backgroundColor: "hsl(var(--popover))",
                border: "1px solid hsl(var(--border))",
                borderRadius: "8px",
                padding: "12px",
              }}
              labelStyle={{
                color: "hsl(var(--popover-foreground))",
                fontWeight: 600,
                marginBottom: "8px"
              }}
              formatter={(value: number, name: string) => {
                const labels: Record<string, string> = {
                  market_return: `${indexName} ${t("chart.return")}`,
                  prism_return: t("chart.prismReturn")
                }
                return [formatPercent(value), labels[name] || name]
              }}
            />
            <Legend
              wrapperStyle={{ paddingTop: "20px" }}
              formatter={(value: string) => {
                const labels: Record<string, string> = {
                  market_return: `${indexName} ${t("chart.return")}`,
                  prism_return: t("chart.prismReturn")
                }
                return labels[value] || value
              }}
            />
            <Line
              type="monotone"
              dataKey="market_return"
              stroke={marketColor}
              strokeWidth={3}
              dot={false}
              activeDot={{ r: 6 }}
            />
            <Line
              type="monotone"
              dataKey="prism_return"
              stroke="#a855f7"
              strokeWidth={3.5}
              strokeDasharray="8 4"
              dot={false}
              activeDot={{ r: 6 }}
            />
          </LineChart>
        </ResponsiveContainer>
      </CardContent>
    </Card>
    )
  }

  // Chart titles based on market
  const index1Title = isUSMarket
    ? (language === "ko" ? `S&P 500 대비 수익률 (${seasonInfo.seasonName})` : `Return vs S&P 500 (${seasonInfo.seasonName})`)
    : t("chart.returnComparisonKospi")
  const index2Title = isUSMarket
    ? (language === "ko" ? `NASDAQ 대비 수익률 (${seasonInfo.seasonName})` : `Return vs NASDAQ (${seasonInfo.seasonName})`)
    : t("chart.returnComparisonKosdaq")

  return (
    <div className="space-y-4">
      {/* 수익률 비교 차트 */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <ComparisonChart
          chartData={index1ChartData}
          title={index1Title}
          marketColor={index1Color}
          yDomain={index1YDomain}
          latestData={latestIndex1}
          mdd={prismMDD}
          indexName={index1Name}
        />
        <ComparisonChart
          chartData={index2ChartData}
          title={index2Title}
          marketColor={index2Color}
          yDomain={index2YDomain}
          latestData={latestIndex2}
          mdd={prismMDD}
          indexName={index2Name}
        />
      </div>

      {/* 기존 지수 차트 */}
      <IndexCharts data={sortedData} market={market} />
    </div>
  )
}

// 기존 KOSPI/KOSDAQ 지수 차트를 별도 컴포넌트로 분리
function IndexCharts({ data, market = "KR" }: { data: MarketCondition[], market?: Market }) {
  const { t, language } = useLanguage()
  const isUSMarket = market === "US"

  const formatNumber = (value: number) => {
    return new Intl.NumberFormat("ko-KR", {
      maximumFractionDigits: 0,
    }).format(value)
  }

  // Y축 도메인 계산 함수
  const getYAxisDomain = (values: number[]) => {
    if (values.length === 0) return [0, 3000]

    const min = Math.min(...values)
    const max = Math.max(...values)
    const padding = (max - min) * 0.05

    return [Math.floor(min - padding), Math.ceil(max + padding)]
  }

  // Get index values based on market
  const index1Values = isUSMarket
    ? data.map(d => d.spx_index || 0).filter(v => v > 0)
    : data.map(d => d.kospi_index ?? 0).filter(v => v > 0)
  const index2Values = isUSMarket
    ? data.map(d => d.nasdaq_index || 0).filter(v => v > 0)
    : data.map(d => d.kosdaq_index ?? 0).filter(v => v > 0)

  const [index1Min, index1Max] = getYAxisDomain(index1Values)
  const [index2Min, index2Max] = getYAxisDomain(index2Values)

  // 전일 대비 변화율 계산
  const getLatestChange = (values: number[]) => {
    if (values.length < 2) return { current: 0, change: 0, changePercent: 0 }
    const current = values[values.length - 1]
    const previous = values[values.length - 2]
    const change = current - previous
    const changePercent = (change / previous) * 100
    return { current, change, changePercent }
  }

  const index1Stats = getLatestChange(index1Values)
  const index2Stats = getLatestChange(index2Values)

  // Index names and colors based on market
  const index1Name = isUSMarket ? "S&P 500" : "KOSPI"
  const index2Name = isUSMarket ? "NASDAQ" : "KOSDAQ"
  const index1Color = isUSMarket ? "#8b5cf6" : "#3b82f6"  // purple for S&P, blue for KOSPI
  const index2Color = isUSMarket ? "#06b6d4" : "#10b981"  // cyan for NASDAQ, emerald for KOSDAQ
  const index1DataKey = isUSMarket ? "spx_index" : "kospi_index"
  const index2DataKey = isUSMarket ? "nasdaq_index" : "kosdaq_index"

  const IndexCard = ({
    title,
    dataKey,
    color,
    yMin,
    yMax,
    stats
  }: {
    title: string
    dataKey: "kospi_index" | "kosdaq_index" | "spx_index" | "nasdaq_index"
    color: string
    yMin: number
    yMax: number
    stats: { current: number, change: number, changePercent: number }
  }) => (
    <Card className="border-border/50">
      <CardHeader>
        <div className="flex items-center justify-between">
          <CardTitle className="text-lg font-semibold">{title} {t("chart.index")}</CardTitle>
          <div className="text-right">
            <p className="text-2xl font-bold">{formatNumber(stats.current)}</p>
            <p className={`text-sm font-medium ${stats.change >= 0 ? 'text-success' : 'text-destructive'}`}>
              {stats.change >= 0 ? '+' : ''}{formatNumber(stats.change)} ({stats.changePercent >= 0 ? '+' : ''}{stats.changePercent.toFixed(2)}%)
            </p>
          </div>
        </div>
      </CardHeader>
      <CardContent>
        <ResponsiveContainer width="100%" height={200}>
          <LineChart data={data}>
            <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" opacity={0.3} />
            <XAxis
              dataKey="date"
              stroke="hsl(var(--muted-foreground))"
              fontSize={11}
              tickLine={false}
              axisLine={false}
              tickFormatter={(value) => {
                const date = new Date(value)
                return `${date.getMonth() + 1}/${date.getDate()}`
              }}
            />
            <YAxis
              stroke="hsl(var(--muted-foreground))"
              fontSize={11}
              tickLine={false}
              axisLine={false}
              tickFormatter={formatNumber}
              domain={[yMin, yMax]}
            />
            <Tooltip
              contentStyle={{
                backgroundColor: "hsl(var(--popover))",
                border: "1px solid hsl(var(--border))",
                borderRadius: "8px",
                padding: "8px 12px",
              }}
              labelStyle={{ color: "hsl(var(--popover-foreground))", fontWeight: 600 }}
              formatter={(value: number) => [formatNumber(value), title]}
            />
            <Line
              type="monotone"
              dataKey={dataKey}
              stroke={color}
              strokeWidth={2.5}
              dot={false}
              activeDot={{ r: 5 }}
            />
          </LineChart>
        </ResponsiveContainer>
      </CardContent>
    </Card>
  )

  return (
    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
      <IndexCard
        title={index1Name}
        dataKey={index1DataKey as "kospi_index" | "kosdaq_index" | "spx_index" | "nasdaq_index"}
        color={index1Color}
        yMin={index1Min}
        yMax={index1Max}
        stats={index1Stats}
      />
      <IndexCard
        title={index2Name}
        dataKey={index2DataKey as "kospi_index" | "kosdaq_index" | "spx_index" | "nasdaq_index"}
        color={index2Color}
        yMin={index2Min}
        yMax={index2Max}
        stats={index2Stats}
      />
    </div>
  )
}
