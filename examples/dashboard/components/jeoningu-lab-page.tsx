"use client"

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { TrendingUp, TrendingDown, Minus, ExternalLink, AlertCircle } from "lucide-react"
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Legend } from "recharts"

interface JeoninguLabData {
    enabled: boolean
    message?: string
    summary?: {
        total_trades: number
        winning_trades: number
        losing_trades: number
        win_rate: number
        cumulative_return: number
        realized_pl?: number
        unrealized_pl?: number
        total_pl?: number
        total_assets?: number
        avg_return_per_trade: number
        initial_capital: number
        current_balance: number
    }
    current_position?: {
        stock_code: string
        stock_name: string
        quantity: number
        buy_price: number
        buy_amount: number
        current_price?: number
        current_value?: number
        unrealized_pl?: number
        unrealized_pl_pct?: number
        buy_date: string
        video_id: string
        video_title: string
    } | null
    timeline?: Array<{
        video_id: string
        video_title: string
        video_date: string
        video_url: string
        analyzed_date: string
        jeon_sentiment: string
        jeon_reasoning: string
        contrarian_action: string
        trade_type: string
        stock_code: string | null
        stock_name: string | null
        notes: string
        profit_loss: number | null
        profit_loss_pct: number | null
    }>
    cumulative_chart?: Array<{
        date: string
        cumulative_return: number
        balance: number
    }>
    trade_history?: any[]
}

interface JeoninguLabPageProps {
    data: JeoninguLabData
}

export function JeoninguLabPage({ data }: JeoninguLabPageProps) {
    // 데이터가 없는 경우 체크
    const hasNoData = !data.enabled || !data.timeline || data.timeline.length === 0
    const summary = data.summary
    const current_position = data.current_position
    const timeline = data.timeline || []
    const cumulative_chart = data.cumulative_chart || []

    const getSentimentIcon = (sentiment: string) => {
        switch (sentiment) {
            case "상승":
                return <TrendingUp className="w-4 h-4 text-red-500" />
            case "하락":
                return <TrendingDown className="w-4 h-4 text-blue-500" />
            case "중립":
                return <Minus className="w-4 h-4 text-gray-500" />
            default:
                return null
        }
    }

    const getSentimentColor = (sentiment: string) => {
        switch (sentiment) {
            case "상승":
                return "text-red-500 bg-red-50 border-red-200"
            case "하락":
                return "text-blue-500 bg-blue-50 border-blue-200"
            case "중립":
                return "text-gray-500 bg-gray-50 border-gray-200"
            default:
                return "text-gray-500"
        }
    }

    const getTradeTypeColor = (tradeType: string) => {
        switch (tradeType) {
            case "BUY":
                return "text-green-600 bg-green-50 border-green-200"
            case "SELL":
                return "text-orange-600 bg-orange-50 border-orange-200"
            case "HOLD":
                return "text-gray-600 bg-gray-50 border-gray-200"
            default:
                return "text-gray-600"
        }
    }

    // Format chart data
    const chartData = cumulative_chart.map((item) => ({
        date: new Date(item.date).toLocaleDateString("ko-KR", { month: "short", day: "numeric" }),
        수익률: item.cumulative_return,
        잔액: item.balance / 1000000, // 백만원 단위
    }))

    return (
        <div className="space-y-6">
            {/* Banner Image Section - Separate at top */}
            <div className="relative overflow-hidden rounded-lg shadow-lg">
                <img
                    src="/jeoningu_banner.png"
                    alt="전인구 실험실 배너"
                    className="w-full h-auto object-cover"
                />
            </div>

            {/* Info Banner */}
            <div className="relative overflow-hidden rounded-lg bg-gradient-to-r from-purple-600 to-indigo-600 p-8 text-white shadow-lg">
                <div className="absolute inset-0 bg-black opacity-10" />
                <div className="relative z-10">
                    <h1 className="text-3xl font-bold mb-2">🧪 전인구 역발상 투자 실험실</h1>
                    <p className="text-purple-100">
                        전인구경제연구소의 예측과 정반대로 베팅하는 실험 | 레버리지 2X 전략
                    </p>
                    <p className="text-purple-100 text-sm font-medium">
                        ⚠️ 이 실험은 실계좌 매매가 아닌, 단순 시뮬레이션입니다.
                    </p>

                    {/* Background Section */}
                    <div className="mt-6 p-4 bg-white/10 rounded-lg backdrop-blur-sm border border-white/20">
                        <h3 className="text-lg font-semibold mb-2 flex items-center gap-2">
                            <span>📖</span>
                            <span>실험 배경</span>
                        </h3>
                        <p className="text-sm text-purple-50 leading-relaxed">
                            예전부터 커뮤니티에서는 전인구경제연구소 소장님의 말을 반대로 투자하면 돈을 번다는,
                            이른바 '전반꿀'이 유행하고 있습니다. 어떤 사람은 이 말을 정말로 믿는 것 같고,
                            어떤 사람은 이 말을 믿고 투자하면 망한다고 합니다.
                            <strong className="text-white"> 과연 어느쪽이 진실에 더 가까울까요? 전인구는 투자의 신이 맞을까요? 인간일까요?</strong>
                            <br />
                            이 가상실험을 통해 전반꿀의 실제 투자효과를 증명하겠습니다.
                        </p>
                    </div>
                </div>
                <div className="absolute right-0 bottom-0 opacity-10">
                    <svg width="200" height="200" viewBox="0 0 200 200" className="text-white">
                        <circle cx="100" cy="100" r="80" fill="currentColor" />
                    </svg>
                </div>
            </div>

            {/* 데이터 없음 안내 */}
            {hasNoData && (
                <Card className="border-yellow-200 bg-yellow-50">
                    <CardContent className="pt-6">
                        <div className="flex items-start gap-3">
                            <AlertCircle className="w-5 h-5 text-yellow-600 mt-0.5 flex-shrink-0" />
                            <div className="space-y-2">
                                <h3 className="font-semibold text-yellow-900">실험실 데이터 준비 중</h3>
                                <p className="text-sm text-yellow-800">
                                    전인구경제연구소의 새 영상이 업로드되면 자동으로 분석하여 데이터가 생성됩니다.
                                </p>
                                <div className="text-xs text-yellow-700 space-y-1 mt-3">
                                    <p>📺 모니터링 중: 전인구경제연구소 YouTube 채널</p>
                                    <p>🤖 자동 분석: 신규 영상 감지 시 AI 분석 및 거래 시뮬레이션</p>
                                    <p>⏰ 업데이트: 영상 업로드 후 약 5-10분 소요</p>
                                </div>
                            </div>
                        </div>
                    </CardContent>
                </Card>
            )}

            {/* Stats Cards - 기본값으로 표시 */}
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
                <Card className="border-purple-200 bg-gradient-to-br from-purple-50 to-white">
                    <CardHeader className="pb-2">
                        <CardTitle className="text-sm font-medium text-muted-foreground">총 수익률</CardTitle>
                    </CardHeader>
                    <CardContent>
                        <div className="flex items-center justify-between">
                            <div className={`text-2xl font-bold ${(summary?.cumulative_return || 0) >= 0 ? "text-green-600" : "text-red-600"}`}>
                                {(summary?.cumulative_return || 0) >= 0 ? "+" : ""}
                                {(summary?.cumulative_return || 0).toFixed(2)}%
                            </div>
                            <div className="text-3xl">📈</div>
                        </div>
                        <div className="text-xs text-muted-foreground mt-1">
                            {summary?.total_pl !== undefined 
                                ? `${(summary.total_pl || 0) >= 0 ? "+" : ""}${(summary.total_pl || 0).toLocaleString()}원`
                                : "데이터 대기 중"}
                        </div>
                    </CardContent>
                </Card>

                <Card className="border-green-200 bg-gradient-to-br from-green-50 to-white">
                    <CardHeader className="pb-2">
                        <CardTitle className="text-sm font-medium text-muted-foreground">승률</CardTitle>
                    </CardHeader>
                    <CardContent>
                        <div className="flex items-center justify-between">
                            <div className="text-2xl font-bold text-green-600">{(summary?.win_rate || 0).toFixed(1)}%</div>
                            <div className="text-3xl">🎯</div>
                        </div>
                        <div className="text-xs text-muted-foreground mt-1">
                            {summary && summary.total_trades > 0 
                                ? `${summary.winning_trades}승 ${summary.losing_trades}패 (${summary.total_trades}건)` 
                                : "청산 거래 없음"}
                        </div>
                    </CardContent>
                </Card>

                <Card className="border-blue-200 bg-gradient-to-br from-blue-50 to-white">
                    <CardHeader className="pb-2">
                        <CardTitle className="text-sm font-medium text-muted-foreground">손익 현황</CardTitle>
                    </CardHeader>
                    <CardContent>
                        <div className="space-y-1">
                            <div className="flex items-center justify-between">
                                <span className="text-xs text-muted-foreground">실현</span>
                                <span className={`text-sm font-semibold ${(summary?.realized_pl || 0) >= 0 ? "text-green-600" : "text-red-600"}`}>
                                    {(summary?.realized_pl || 0) >= 0 ? "+" : ""}{(summary?.realized_pl || 0).toLocaleString()}원
                                </span>
                            </div>
                            <div className="flex items-center justify-between">
                                <span className="text-xs text-muted-foreground">미실현</span>
                                <span className={`text-sm font-semibold ${(summary?.unrealized_pl || 0) >= 0 ? "text-green-600" : "text-red-600"}`}>
                                    {(summary?.unrealized_pl || 0) >= 0 ? "+" : ""}{(summary?.unrealized_pl || 0).toLocaleString()}원
                                </span>
                            </div>
                        </div>
                        <div className="text-3xl absolute top-4 right-4">💹</div>
                    </CardContent>
                </Card>

                <Card className="border-yellow-200 bg-gradient-to-br from-yellow-50 to-white">
                    <CardHeader className="pb-2">
                        <CardTitle className="text-sm font-medium text-muted-foreground">총 자산</CardTitle>
                    </CardHeader>
                    <CardContent>
                        <div className="flex items-center justify-between">
                            <div className="text-2xl font-bold text-yellow-600">
                                {summary?.total_assets 
                                    ? `${(summary.total_assets / 10000).toFixed(0)}만원`
                                    : "1,000만원"}
                            </div>
                            <div className="text-3xl">💰</div>
                        </div>
                        <div className="text-xs text-muted-foreground mt-1">초기 1,000만원</div>
                    </CardContent>
                </Card>
            </div>

            {/* 데이터가 있을 때만 나머지 컴포넌트 표시 */}
            {!hasNoData && (
                <>
                    {/* Current Position */}
                    <Card>
                        <CardHeader>
                            <CardTitle>💼 현재 포지션</CardTitle>
                        </CardHeader>
                        <CardContent>
                            {current_position ? (
                                <div className="space-y-3">
                                    <div className="flex items-center justify-between">
                                        <div>
                                            <div className="text-xl font-bold">{current_position.stock_name}</div>
                                            <div className="text-sm text-muted-foreground">({current_position.stock_code})</div>
                                        </div>
                                        <div className="text-right">
                                            <div className="text-lg font-semibold">{current_position.quantity.toLocaleString()}주</div>
                                            <div className="text-sm text-muted-foreground">
                                                매수 @ {current_position.buy_price.toLocaleString()}원
                                            </div>
                                        </div>
                                    </div>
                                    
                                    {/* 실시간 평가 정보 */}
                                    <div className="p-4 rounded-lg bg-gradient-to-r from-purple-50 to-indigo-50 border border-purple-100">
                                        <div className="grid grid-cols-2 gap-4">
                                            <div>
                                                <div className="text-xs text-muted-foreground">현재가</div>
                                                <div className="text-lg font-bold text-purple-700">
                                                    {(current_position.current_price || current_position.buy_price).toLocaleString()}원
                                                </div>
                                            </div>
                                            <div>
                                                <div className="text-xs text-muted-foreground">평가금액</div>
                                                <div className="text-lg font-bold text-purple-700">
                                                    {(current_position.current_value || current_position.buy_amount).toLocaleString()}원
                                                </div>
                                            </div>
                                        </div>
                                        <div className="mt-3 pt-3 border-t border-purple-200">
                                            <div className="flex items-center justify-between">
                                                <span className="text-sm text-muted-foreground">평가손익</span>
                                                <div className={`text-xl font-bold ${
                                                    (current_position.unrealized_pl || 0) >= 0 ? "text-green-600" : "text-red-600"
                                                }`}>
                                                    {(current_position.unrealized_pl || 0) >= 0 ? "+" : ""}
                                                    {(current_position.unrealized_pl || 0).toLocaleString()}원
                                                    <span className="text-sm ml-1">
                                                        ({(current_position.unrealized_pl_pct || 0) >= 0 ? "+" : ""}
                                                        {(current_position.unrealized_pl_pct || 0).toFixed(2)}%)
                                                    </span>
                                                </div>
                                            </div>
                                        </div>
                                    </div>

                                    <div className="grid grid-cols-2 gap-4 pt-3 border-t">
                                        <div>
                                            <div className="text-xs text-muted-foreground">매수금액</div>
                                            <div className="text-sm font-medium">
                                                {current_position.buy_amount.toLocaleString()}원
                                            </div>
                                        </div>
                                        <div>
                                            <div className="text-xs text-muted-foreground">매수일</div>
                                            <div className="text-sm font-medium">
                                                {new Date(current_position.buy_date).toLocaleDateString("ko-KR")}
                                            </div>
                                        </div>
                                    </div>
                                    <div className="pt-3 border-t">
                                        <div className="text-xs text-muted-foreground mb-1">관련 영상</div>
                                        <div className="text-sm">{current_position.video_title}</div>
                                    </div>
                                </div>
                            ) : (
                                <div className="text-center py-8 text-muted-foreground">
                                    <div className="text-4xl mb-2">💵</div>
                                    <div>현금 보유 중 (보유 종목 없음)</div>
                                </div>
                            )}
                        </CardContent>
                    </Card>

                    {/* Cumulative Return Chart */}
                    {chartData.length > 0 && (
                        <Card>
                            <CardHeader>
                                <CardTitle>📈 누적 수익률 추이</CardTitle>
                            </CardHeader>
                            <CardContent>
                                <div className="h-[400px]">
                                    <ResponsiveContainer width="100%" height="100%">
                                        <LineChart data={chartData}>
                                            <CartesianGrid strokeDasharray="3 3" />
                                            <XAxis dataKey="date" />
                                            <YAxis yAxisId="left" label={{ value: "수익률 (%)", angle: -90, position: "insideLeft" }} />
                                            <YAxis
                                                yAxisId="right"
                                                orientation="right"
                                                label={{ value: "잔액 (백만원)", angle: 90, position: "insideRight" }}
                                            />
                                            <Tooltip />
                                            <Legend />
                                            <Line
                                                yAxisId="left"
                                                type="monotone"
                                                dataKey="수익률"
                                                stroke="#8b5cf6"
                                                strokeWidth={2}
                                                dot={{ fill: "#8b5cf6" }}
                                            />
                                            <Line
                                                yAxisId="right"
                                                type="monotone"
                                                dataKey="잔액"
                                                stroke="#f59e0b"
                                                strokeWidth={2}
                                                dot={{ fill: "#f59e0b" }}
                                            />
                                        </LineChart>
                                    </ResponsiveContainer>
                                </div>
                            </CardContent>
                        </Card>
                    )}

                    {/* Timeline */}
                    <Card>
                        <CardHeader>
                            <CardTitle>📅 영상별 투자 타임라인</CardTitle>
                        </CardHeader>
                        <CardContent>
                            <div className="space-y-6">
                                {timeline.map((entry) => (
                                    <div key={entry.video_id} className="relative pl-8 pb-6 border-l-2 border-purple-200 last:border-l-0">
                                        {/* Timeline marker */}
                                        <div className="absolute left-[-9px] top-0">
                                            <div
                                                className={`w-4 h-4 rounded-full border-2 ${
                                                    entry.jeon_sentiment === "상승"
                                                        ? "bg-red-100 border-red-400"
                                                        : entry.jeon_sentiment === "하락"
                                                            ? "bg-blue-100 border-blue-400"
                                                            : "bg-gray-100 border-gray-400"
                                                }`}
                                            />
                                        </div>

                                        {/* Entry content */}
                                        <div className="space-y-3">
                                            {/* Header */}
                                            <div className="flex items-start justify-between gap-4">
                                                <div className="flex-1">
                                                    <a
                                                        href={entry.video_url}
                                                        target="_blank"
                                                        rel="noopener noreferrer"
                                                        className="text-base font-semibold hover:text-purple-600 flex items-center gap-2"
                                                    >
                                                        {entry.video_title}
                                                        <ExternalLink className="w-4 h-4" />
                                                    </a>
                                                    <div className="text-xs text-muted-foreground mt-1">
                                                        {new Date(entry.analyzed_date).toLocaleString("ko-KR")}
                                                    </div>
                                                </div>
                                            </div>

                                            {/* Sentiment & Action */}
                                            <div className="flex items-center gap-3 flex-wrap">
                                                <div className="flex items-center gap-2">
                                                    <span className="text-xs text-muted-foreground">전인구 기조:</span>
                                                    <span
                                                        className={`inline-flex items-center gap-1 px-2 py-1 rounded-md text-xs font-medium border ${getSentimentColor(entry.jeon_sentiment)}`}
                                                    >
                            {getSentimentIcon(entry.jeon_sentiment)}
                                                        {entry.jeon_sentiment}
                          </span>
                                                </div>
                                                <span className="text-muted-foreground">→</span>
                                                <div className="flex items-center gap-2">
                                                    <span className="text-xs text-muted-foreground">역발상 액션:</span>
                                                    <span className="px-2 py-1 rounded-md text-xs font-medium bg-purple-100 text-purple-700 border border-purple-200">
                            {entry.contrarian_action}
                          </span>
                                                </div>
                                            </div>

                                            {/* Trade Info */}
                                            <div className="flex items-center gap-3 flex-wrap">
                        <span
                            className={`inline-flex items-center px-2 py-1 rounded-md text-xs font-medium border ${getTradeTypeColor(entry.trade_type)}`}
                        >
                          {entry.trade_type}
                        </span>
                                                {entry.stock_name && (
                                                    <span className="text-sm font-medium text-gray-700">{entry.stock_name}</span>
                                                )}
                                                {entry.profit_loss !== null && (
                                                    <span
                                                        className={`text-sm font-semibold ${entry.profit_loss >= 0 ? "text-green-600" : "text-red-600"}`}
                                                    >
                            {entry.profit_loss >= 0 ? "+" : ""}
                                                        {entry.profit_loss.toLocaleString()}원 ({entry.profit_loss_pct! >= 0 ? "+" : ""}
                                                        {entry.profit_loss_pct!.toFixed(2)}%)
                          </span>
                                                )}
                                            </div>

                                            {/* Reasoning */}
                                            {entry.jeon_reasoning && (
                                                <div className="text-sm text-muted-foreground bg-gray-50 p-3 rounded-md border">
                                                    {entry.jeon_reasoning}
                                                </div>
                                            )}

                                            {/* Notes */}
                                            {entry.notes && (
                                                <div className="text-xs text-muted-foreground italic">💡 {entry.notes}</div>
                                            )}
                                        </div>
                                    </div>
                                ))}
                            </div>
                        </CardContent>
                    </Card>

                    {/* Trade History Table */}
                    <Card>
                        <CardHeader>
                            <CardTitle>💼 전체 거래 이력</CardTitle>
                        </CardHeader>
                        <CardContent>
                            <div className="overflow-x-auto">
                                <table className="w-full text-sm">
                                    <thead>
                                    <tr className="border-b">
                                        <th className="text-left p-2">날짜</th>
                                        <th className="text-left p-2">영상</th>
                                        <th className="text-center p-2">전인구 기조</th>
                                        <th className="text-center p-2">거래</th>
                                        <th className="text-left p-2">종목</th>
                                        <th className="text-right p-2">손익</th>
                                        <th className="text-right p-2">수익률</th>
                                    </tr>
                                    </thead>
                                    <tbody>
                                    {timeline.map((trade) => (
                                        <tr key={trade.video_id} className="border-b hover:bg-gray-50">
                                            <td className="p-2 whitespace-nowrap">
                                                {new Date(trade.analyzed_date).toLocaleDateString("ko-KR")}
                                            </td>
                                            <td className="p-2">
                                                <a
                                                    href={trade.video_url}
                                                    target="_blank"
                                                    rel="noopener noreferrer"
                                                    className="hover:text-purple-600 flex items-center gap-1"
                                                >
                                                    <span className="truncate max-w-[200px]">{trade.video_title}</span>
                                                    <ExternalLink className="w-3 h-3 flex-shrink-0" />
                                                </a>
                                            </td>
                                            <td className="p-2 text-center">
                          <span
                              className={`inline-flex items-center gap-1 px-2 py-1 rounded text-xs ${getSentimentColor(trade.jeon_sentiment).replace("border", "")}`}
                          >
                            {trade.jeon_sentiment}
                          </span>
                                            </td>
                                            <td className="p-2 text-center">
                          <span className={`px-2 py-1 rounded text-xs ${getTradeTypeColor(trade.trade_type).replace("border", "")}`}>
                            {trade.trade_type}
                          </span>
                                            </td>
                                            <td className="p-2">{trade.stock_name || "-"}</td>
                                            <td className={`p-2 text-right ${trade.profit_loss && trade.profit_loss >= 0 ? "text-green-600" : "text-red-600"}`}>
                                                {trade.profit_loss ? `${trade.profit_loss >= 0 ? "+" : ""}${trade.profit_loss.toLocaleString()}원` : "-"}
                                            </td>
                                            <td className={`p-2 text-right ${trade.profit_loss_pct && trade.profit_loss_pct >= 0 ? "text-green-600" : "text-red-600"}`}>
                                                {trade.profit_loss_pct
                                                    ? `${trade.profit_loss_pct >= 0 ? "+" : ""}${trade.profit_loss_pct.toFixed(2)}%`
                                                    : "-"}
                                            </td>
                                        </tr>
                                    ))}
                                    </tbody>
                                </table>
                            </div>
                        </CardContent>
                    </Card>
                </>
            )}
        </div>
    )
}