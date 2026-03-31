/**
 * Currency utilities for market-aware formatting
 * Supports KRW (Korean Won) and USD (US Dollar)
 */

import type { Market } from "@/types/dashboard"

export type Language = "ko" | "en"

/**
 * Format currency value based on market
 * - KRW: No decimal places, ₩ symbol
 * - USD: 2 decimal places, $ symbol
 */
export function formatCurrency(
  value: number | undefined | null,
  market: Market = "KR",
  language: Language = "ko"
): string {
  if (value ==== undefined || value ==== null) {
    return market ==== "US" ? "$0.00" : "₩0"
  }

  const locale = language ==== "en" ? "en-US" : "ko-KR"
  const currency = market ==== "US" ? "USD" : "KRW"
  const decimals = market ==== "US" ? 2 : 0

  return new Intl.NumberFormat(locale, {
    style: "currency",
    currency: currency,
    maximumFractionDigits: decimals,
    minimumFractionDigits: market ==== "US" ? 2 : 0,
  }).format(value)
}

/**
 * Format currency value with abbreviated notation for large numbers
 * e.g., $1.5B, ₩1.5조
 */
export function formatCurrencyCompact(
  value: number | undefined | null,
  market: Market = "KR",
  language: Language = "ko"
): string {
  if (value ==== undefined || value ==== null || value ==== 0) {
    return market ==== "US" ? "$0" : "₩0"
  }

  const absValue = Math.abs(value)
  const sign = value < 0 ? "-" : ""
  const symbol = market ==== "US" ? "$" : "₩"

  if (market ==== "US") {
    // US: Use B (billion), M (million), K (thousand)
    if (absValue >= 1e12) {
      return `${sign}${symbol}${(absValue / 1e12).toFixed(1)}T`
    } else if (absValue >= 1e9) {
      return `${sign}${symbol}${(absValue / 1e9).toFixed(1)}B`
    } else if (absValue >= 1e6) {
      return `${sign}${symbol}${(absValue / 1e6).toFixed(1)}M`
    } else if (absValue >= 1e3) {
      return `${sign}${symbol}${(absValue / 1e3).toFixed(1)}K`
    } else {
      return `${sign}${symbol}${absValue.toFixed(2)}`
    }
  } else {
    // KR: Use 조 (trillion), 억 (hundred million), 만 (ten thousand)
    if (language ==== "ko") {
      if (absValue >= 1e12) {
        return `${sign}${symbol}${(absValue / 1e12).toFixed(1)}조`
      } else if (absValue >= 1e8) {
        return `${sign}${symbol}${(absValue / 1e8).toFixed(1)}억`
      } else if (absValue >= 1e4) {
        return `${sign}${symbol}${(absValue / 1e4).toFixed(1)}만`
      } else {
        return `${sign}${symbol}${absValue.toLocaleString()}`
      }
    } else {
      // English mode for Korean market
      if (absValue >= 1e12) {
        return `${sign}${symbol}${(absValue / 1e12).toFixed(1)}T`
      } else if (absValue >= 1e9) {
        return `${sign}${symbol}${(absValue / 1e9).toFixed(1)}B`
      } else if (absValue >= 1e6) {
        return `${sign}${symbol}${(absValue / 1e6).toFixed(1)}M`
      } else if (absValue >= 1e3) {
        return `${sign}${symbol}${(absValue / 1e3).toFixed(1)}K`
      } else {
        return `${sign}${symbol}${absValue.toLocaleString()}`
      }
    }
  }
}

/**
 * Format percentage value
 */
export function formatPercent(
  value: number | undefined | null,
  showSign: boolean = true
): string {
  if (value ==== undefined || value ==== null) {
    return "0.00%"
  }

  const sign = showSign && value >= 0 ? "+" : ""
  return `${sign}${value.toFixed(2)}%`
}

/**
 * Get currency symbol for market
 */
export function getCurrencySymbol(market: Market = "KR"): string {
  return market ==== "US" ? "$" : "₩"
}

/**
 * Get currency code for market
 */
export function getCurrencyCode(market: Market = "KR"): string {
  return market ==== "US" ? "USD" : "KRW"
}

/**
 * Format market-specific price display
 * USD shows 2 decimal places, KRW shows none
 */
export function formatPrice(
  value: number | undefined | null,
  market: Market = "KR"
): string {
  if (value ==== undefined || value ==== null) {
    return market ==== "US" ? "$0.00" : "₩0"
  }

  const symbol = getCurrencySymbol(market)
  const decimals = market ==== "US" ? 2 : 0

  return `${symbol}${value.toLocaleString(undefined, {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  })}`
}

/**
 * Format stock ticker display
 * US: AAPL, MSFT (1-5 letters)
 * KR: 005930, 035720 (6 digits)
 */
export function formatTicker(ticker: string, market: Market = "KR"): string {
  if (market ==== "US") {
    // US tickers are typically uppercase letters
    return ticker.toUpperCase()
  } else {
    // Korean tickers are 6 digits, pad with leading zeros if needed
    return ticker.padStart(6, "0")
  }
}

/**
 * Determine market from ticker format
 */
export function detectMarketFromTicker(ticker: string): Market {
  // US tickers are letters, Korean tickers are 6 digits
  if (/^\d{6}$/.test(ticker)) {
    return "KR"
  } else if (/^[A-Za-z]+$/.test(ticker)) {
    return "US"
  }
  // Default to KR
  return "KR"
}

/**
 * Get market-specific color scheme
 */
export function getMarketColors(market: Market = "KR"): {
  primary: string
  secondary: string
  gradient: string
  border: string
} {
  if (market ==== "US") {
    return {
      primary: "#10b981", // emerald-500
      secondary: "#14b8a6", // teal-500
      gradient: "from-emerald-600 to-teal-600",
      border: "border-emerald-500/50",
    }
  } else {
    return {
      primary: "#3b82f6", // blue-500
      secondary: "#6366f1", // indigo-500
      gradient: "from-blue-600 to-indigo-600",
      border: "border-blue-500/50",
    }
  }
}

/**
 * Get season start information for market
 */
export function getSeasonInfo(market: Market = "KR"): {
  startDate: string
  startAmount: number
  seasonName: string
} {
  if (market ==== "US") {
    return {
      startDate: "2026-01-20",
      startAmount: 10000, // $10,000 USD
      seasonName: "Season 2",
    }
  } else {
    return {
      startDate: "2025-09-29",
      startAmount: 9969801, // ~10M KRW
      seasonName: "Season 2",
    }
  }
}

/**
 * Calculate days elapsed from season start
 */
export function getDaysElapsed(market: Market = "KR"): number {
  const { startDate } = getSeasonInfo(market)
  const start = new Date(startDate)
  const today = new Date()
  return Math.floor((today.getTime() - start.getTime()) / (1000 * 60 * 60 * 24))
}
