"use client"

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Line, LineChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis, Legend } from "recharts"
import type { MarketCondition, TradingHistory, Holding, Summary } from "@/types/dashboard"

interface PerformanceChartProps {
  data: MarketCondition[]
  tradingHistory?: TradingHistory[]
  holdings?: Holding[]
  summary?: Summary
}

export function PerformanceChart({ data, tradingHistory = [], holdings = [], summary }: PerformanceChartProps) {
  const formatNumber = (value: number) => {
    return new Intl.NumberFormat("ko-KR", {
      maximumFractionDigits: 0,
    }).format(value)
  }

  const formatPercent = (value: number) => {
    return `${value >= 0 ? "+" : ""}${value.toFixed(2)}%`
  }

  // Season2 시작 금액 및 시작 시점 설정
  const season2StartAmount = 9969801
  const season2StartDate = '2025-09-29'
  
  // 데이터를 날짜 기준으로 오름차순 정렬
  const sortedData = [...data].sort((a, b) => {
    return new Date(a.date).getTime() - new Date(b.date).getTime()
  })

  // Season2 시작 시점 이후 데이터만 필터링
  const filteredData = sortedData.filter(d => d.date >= season2StartDate)
  
  if (filteredData.length === 0) {
    return (
      <Card className="border-border/50">
        <CardHeader>
          <CardTitle className="text-lg font-semibold">수익률 비교 (Season2 시작 이후)</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="flex items-center justify-center h-[300px] text-muted-foreground">
            데이터가 없습니다.
          </div>
        </CardContent>
      </Card>
    )
  }

  // 시작 시점의 지수 값
  const startKospi = filteredData[0]?.kospi_index || 0
  const startKosdaq = filteredData[0]?.kosdaq_index || 0

  // 매도종목 누적수익률 계산
  const tradingTotalProfit = tradingHistory.reduce((sum, trade) => sum + trade.profit_rate, 0)
  
  // 보유종목 평가수익률 계산
  const holdingsTotalProfit = summary?.portfolio?.total_profit || 0
  
  // 프리즘 시뮬레이터 수익률 (10개 슬롯 기준)
  const prismSimulatorReturn = (tradingTotalProfit + holdingsTotalProfit) / 10

  // 실전 투자 수익률
  const realTotalAssets = (summary?.real_trading?.total_eval_amount || 0) + 
                          (summary?.real_trading?.available_amount || 0)
  const realReturn = season2StartAmount > 0 
    ? ((realTotalAssets - season2StartAmount) / season2StartAmount) * 100 
    : 0

  // KOSPI 기준 차트 데이터
  const kospiChartData = filteredData.map((item) => {
    const kospiReturn = startKospi > 0 ? ((item.kospi_index - startKospi) / startKospi) * 100 : 0
    
    return {
      date: item.date,
      market_return: kospiReturn,
      prism_return: prismSimulatorReturn,
      real_return: realReturn,
    }
  })

  // KOSDAQ 기준 차트 데이터
  const kosdaqChartData = filteredData.map((item) => {
    const kosdaqReturn = startKosdaq > 0 ? ((item.kosdaq_index - startKosdaq) / startKosdaq) * 100 : 0
    
    return {
      date: item.date,
      market_return: kosdaqReturn,
      prism_return: prismSimulatorReturn,
      real_return: realReturn,
    }
  })

  // 최신 값 계산
  const latestKospi = kospiChartData[kospiChartData.length - 1]
  const latestKosdaq = kosdaqChartData[kosdaqChartData.length - 1]
  
  // Y축 도메인 계산
  const getAllValues = (chartData: typeof kospiChartData) => {
    return chartData.flatMap(d => [
      d.market_return, 
      d.prism_return, 
      d.real_return
    ])
  }
  
  const getYDomain = (values: number[]) => {
    const minValue = Math.min(...values)
    const maxValue = Math.max(...values)
    const padding = Math.max(Math.abs(maxValue - minValue) * 0.15, 2)
    return [Math.floor(minValue - padding), Math.ceil(maxValue + padding)]
  }

  const kospiYDomain = getYDomain(getAllValues(kospiChartData))
  const kosdaqYDomain = getYDomain(getAllValues(kosdaqChartData))

  const ComparisonChart = ({ 
    chartData, 
    title, 
    marketColor,
    yDomain,
    latestData
  }: { 
    chartData: typeof kospiChartData
    title: string
    marketColor: string
    yDomain: [number, number]
    latestData: typeof latestKospi
  }) => (
    <Card className="border-border/50">
      <CardHeader>
        <div className="flex items-center justify-between">
          <CardTitle className="text-lg font-semibold">{title}</CardTitle>
          <div className="text-right text-xs space-y-1">
            <div className="flex items-center gap-3">
              <span className="text-muted-foreground">
                시장: <span style={{ color: marketColor }} className="font-semibold">{formatPercent(latestData.market_return)}</span>
              </span>
              <span className="text-muted-foreground">
                프리즘: <span className={`font-semibold ${prismSimulatorReturn >= 0 ? 'text-purple-600 dark:text-purple-400' : 'text-destructive'}`}>{formatPercent(prismSimulatorReturn)}</span>
              </span>
              <span className="text-muted-foreground">
                실전: <span className={`font-semibold ${realReturn >= 0 ? 'text-amber-600 dark:text-amber-400' : 'text-destructive'}`}>{formatPercent(realReturn)}</span>
              </span>
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
                  market_return: `${title.includes('KOSPI') ? 'KOSPI' : 'KOSDAQ'} 수익률`,
                  prism_return: "프리즘 시뮬레이터",
                  real_return: "실전 투자"
                }
                return [formatPercent(value), labels[name] || name]
              }}
            />
            <Legend 
              wrapperStyle={{ paddingTop: "20px" }}
              formatter={(value: string) => {
                const labels: Record<string, string> = {
                  market_return: `${title.includes('KOSPI') ? 'KOSPI' : 'KOSDAQ'} 수익률`,
                  prism_return: "프리즘 시뮬레이터",
                  real_return: "실전 투자"
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
            <Line
              type="monotone"
              dataKey="real_return"
              stroke="#f59e0b"
              strokeWidth={3.5}
              dot={false}
              activeDot={{ r: 6 }}
            />
          </LineChart>
        </ResponsiveContainer>
      </CardContent>
    </Card>
  )

  return (
    <div className="space-y-4">
      {/* 수익률 비교 차트 */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <ComparisonChart
          chartData={kospiChartData}
          title="수익률 비교 (KOSPI 기준)"
          marketColor="#3b82f6"
          yDomain={kospiYDomain}
          latestData={latestKospi}
        />
        <ComparisonChart
          chartData={kosdaqChartData}
          title="수익률 비교 (KOSDAQ 기준)"
          marketColor="#10b981"
          yDomain={kosdaqYDomain}
          latestData={latestKosdaq}
        />
      </div>

      {/* 기존 지수 차트 */}
      <IndexCharts data={sortedData} />
    </div>
  )
}

// 기존 KOSPI/KOSDAQ 지수 차트를 별도 컴포넌트로 분리
function IndexCharts({ data }: { data: MarketCondition[] }) {
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

  const kospiValues = data.map(d => d.kospi_index).filter(v => v > 0)
  const kosdaqValues = data.map(d => d.kosdaq_index).filter(v => v > 0)
  
  const [kospiMin, kospiMax] = getYAxisDomain(kospiValues)
  const [kosdaqMin, kosdaqMax] = getYAxisDomain(kosdaqValues)

  // 전일 대비 변화율 계산
  const getLatestChange = (values: number[]) => {
    if (values.length < 2) return { current: 0, change: 0, changePercent: 0 }
    const current = values[values.length - 1]
    const previous = values[values.length - 2]
    const change = current - previous
    const changePercent = (change / previous) * 100
    return { current, change, changePercent }
  }

  const kospiStats = getLatestChange(kospiValues)
  const kosdaqStats = getLatestChange(kosdaqValues)

  const IndexCard = ({ 
    title, 
    dataKey, 
    color, 
    yMin,
    yMax,
    stats
  }: { 
    title: string
    dataKey: "kospi_index" | "kosdaq_index"
    color: string
    yMin: number
    yMax: number
    stats: { current: number, change: number, changePercent: number }
  }) => (
    <Card className="border-border/50">
      <CardHeader>
        <div className="flex items-center justify-between">
          <CardTitle className="text-lg font-semibold">{title} 지수</CardTitle>
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
        title="KOSPI"
        dataKey="kospi_index"
        color="#3b82f6"
        yMin={kospiMin}
        yMax={kospiMax}
        stats={kospiStats}
      />
      <IndexCard
        title="KOSDAQ"
        dataKey="kosdaq_index"
        color="#10b981"
        yMin={kosdaqMin}
        yMax={kosdaqMax}
        stats={kosdaqStats}
      />
    </div>
  )
}
