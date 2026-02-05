"use client"

import { Moon, Sun, TrendingUp, Github, Send, Languages } from "lucide-react"
import { useTheme } from "next-themes"
import { useLanguage } from "@/components/language-provider"
import { Button } from "@/components/ui/button"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import type { Market } from "@/types/dashboard"

interface DashboardHeaderProps {
  activeTab: "dashboard" | "ai-decisions" | "trading" | "watchlist" | "insights" | "jeoningu-lab"
  onTabChange: (tab: "dashboard" | "ai-decisions" | "trading" | "watchlist" | "insights" | "jeoningu-lab") => void
  lastUpdated?: string
  market?: Market
  onMarketChange?: (market: Market) => void
}

export function DashboardHeader({ activeTab, onTabChange, lastUpdated, market = "KR", onMarketChange }: DashboardHeaderProps) {
  const { theme, setTheme } = useTheme()
  const { language, setLanguage, t } = useLanguage()

  const formatLastUpdated = () => {
    if (!lastUpdated) return t("header.realtimeUpdate")

    try {
      const date = new Date(lastUpdated)
      if (isNaN(date.getTime())) return t("header.realtimeUpdate")
      return date.toLocaleString(language === "ko" ? "ko-KR" : "en-US", {
        month: 'long',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit'
      })
    } catch {
      return t("header.realtimeUpdate")
    }
  }

  return (
    <header className="sticky top-0 z-50 w-full border-b border-border/40 bg-background/95 backdrop-blur supports-[backdrop-filter]:bg-background/60">
      <div className="container mx-auto px-4 max-w-[1600px]">
        {/* Top Row: Logo + Market Tabs + Utils */}
        <div className="flex h-16 items-center justify-between">
          {/* Logo */}
          <div className="flex items-center gap-3">
            <div className="flex items-center justify-center w-10 h-10 rounded-lg bg-gradient-to-br from-primary via-purple-600 to-blue-600">
              <TrendingUp className="w-6 h-6 text-white" />
            </div>
            <div>
              <div className="flex items-center gap-2">
                <h1 className="text-xl font-bold bg-gradient-to-r from-primary via-purple-600 to-blue-600 bg-clip-text text-transparent">
                  Prism Insight
                </h1>
                <TooltipProvider>
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <span className="px-2 py-0.5 text-xs font-medium rounded-full bg-green-500/10 text-green-500 cursor-help">
                        {t("header.openSource")}
                      </span>
                    </TooltipTrigger>
                    <TooltipContent>
                      <p className="text-xs">{t("header.tooltip.openSource")}</p>
                    </TooltipContent>
                  </Tooltip>
                </TooltipProvider>
              </div>
              <div className="flex items-center gap-2">
                <p className="text-xs text-muted-foreground">
                  {t("header.updated")}: {formatLastUpdated()}
                </p>
                {/* Platinum Sponsor Badge */}
                <span className="hidden sm:flex items-center text-[10px] text-muted-foreground/50">•</span>
                <a
                  href="https://wrks.ai/en"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="hidden sm:flex items-center gap-1.5 group"
                >
                  <span className="text-[10px] text-muted-foreground/50 group-hover:text-muted-foreground transition-colors uppercase tracking-wide">
                    Powered by
                  </span>
                  <img
                    src="/wrks_ai_logo.png"
                    alt="WrksAI"
                    className="h-3 w-auto opacity-60 group-hover:opacity-100 transition-opacity"
                  />
                </a>
              </div>
            </div>
          </div>

          {/* Market Selector - Big Prominent Tabs */}
          {onMarketChange && (
            <div className="hidden sm:flex items-center">
              <div className="flex bg-muted/50 rounded-xl p-1.5 gap-1">
                <button
                  onClick={() => onMarketChange("KR")}
                  className={`
                    flex items-center gap-2 px-5 py-2.5 rounded-lg font-semibold text-sm transition-all duration-200
                    ${market === "KR"
                      ? "bg-gradient-to-r from-blue-600 to-indigo-600 text-white shadow-lg shadow-blue-500/25"
                      : "text-muted-foreground hover:text-foreground hover:bg-muted"
                    }
                  `}
                >
                  <span className="text-lg">🇰🇷</span>
                  <span>{language === "ko" ? "한국주식" : "Korea"}</span>
                  {market === "KR" && (
                    <span className="ml-1 px-1.5 py-0.5 text-[10px] bg-white/20 rounded-full">
                      Season 2
                    </span>
                  )}
                </button>
                <button
                  onClick={() => onMarketChange("US")}
                  className={`
                    flex items-center gap-2 px-5 py-2.5 rounded-lg font-semibold text-sm transition-all duration-200
                    ${market === "US"
                      ? "bg-gradient-to-r from-emerald-600 to-teal-600 text-white shadow-lg shadow-emerald-500/25"
                      : "text-muted-foreground hover:text-foreground hover:bg-muted"
                    }
                  `}
                >
                  <span className="text-lg">🇺🇸</span>
                  <span>{language === "ko" ? "미국주식" : "US Stocks"}</span>
                  {market === "US" && (
                    <span className="ml-1 px-1.5 py-0.5 text-[10px] bg-white/20 rounded-full">
                      Season 2
                    </span>
                  )}
                </button>
              </div>
            </div>
          )}

          {/* Utility Buttons */}
          <div className="flex items-center gap-1">
            <TooltipProvider>
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button
                    variant="ghost"
                    size="icon"
                    asChild
                    className="rounded-full"
                  >
                    <a
                      href="https://github.com/dragon1086/prism-insight"
                      target="_blank"
                      rel="noopener noreferrer"
                      aria-label="GitHub Repository"
                    >
                      <Github className="h-5 w-5" />
                    </a>
                  </Button>
                </TooltipTrigger>
                <TooltipContent>
                  <p className="text-xs">{t("header.tooltip.github")}</p>
                </TooltipContent>
              </Tooltip>
            </TooltipProvider>

            <TooltipProvider>
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button
                    variant="ghost"
                    size="icon"
                    asChild
                    className="rounded-full"
                  >
                    <a
                      href="https://t.me/stock_ai_agent"
                      target="_blank"
                      rel="noopener noreferrer"
                      aria-label="Telegram Channel"
                    >
                      <Send className="h-5 w-5" />
                    </a>
                  </Button>
                </TooltipTrigger>
                <TooltipContent>
                  <p className="text-xs">{t("header.tooltip.telegram")}</p>
                </TooltipContent>
              </Tooltip>
            </TooltipProvider>

            {/* Language Toggle - Prominent Button */}
            <button
              onClick={() => setLanguage(language === "ko" ? "en" : "ko")}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-muted/50 hover:bg-muted transition-colors font-medium text-sm"
            >
              <Languages className="h-4 w-4" />
              <span className={language === "ko" ? "text-muted-foreground" : "text-foreground font-semibold"}>EN</span>
              <span className="text-muted-foreground/50">/</span>
              <span className={language === "ko" ? "text-foreground font-semibold" : "text-muted-foreground"}>한</span>
            </button>

            <Button
              variant="ghost"
              size="icon"
              onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
              className="rounded-full"
            >
              <Sun className="h-5 w-5 rotate-0 scale-100 transition-all dark:-rotate-90 dark:scale-0" />
              <Moon className="absolute h-5 w-5 rotate-90 scale-0 transition-all dark:rotate-0 dark:scale-100" />
              <span className="sr-only">{t("header.tooltip.theme")}</span>
            </Button>
          </div>
        </div>

        {/* Mobile Market Selector */}
        {onMarketChange && (
          <div className="sm:hidden flex justify-center pb-3">
            <div className="flex bg-muted/50 rounded-xl p-1 gap-1 w-full max-w-sm">
              <button
                onClick={() => onMarketChange("KR")}
                className={`
                  flex-1 flex items-center justify-center gap-2 px-4 py-2 rounded-lg font-semibold text-sm transition-all
                  ${market === "KR"
                    ? "bg-gradient-to-r from-blue-600 to-indigo-600 text-white shadow-md"
                    : "text-muted-foreground"
                  }
                `}
              >
                <span>🇰🇷</span>
                <span>{language === "ko" ? "한국주식" : "Korea"}</span>
              </button>
              <button
                onClick={() => onMarketChange("US")}
                className={`
                  flex-1 flex items-center justify-center gap-2 px-4 py-2 rounded-lg font-semibold text-sm transition-all
                  ${market === "US"
                    ? "bg-gradient-to-r from-emerald-600 to-teal-600 text-white shadow-md"
                    : "text-muted-foreground"
                  }
                `}
              >
                <span>🇺🇸</span>
                <span>{language === "ko" ? "미국주식" : "US"}</span>
              </button>
            </div>
          </div>
        )}

        {/* Navigation Tabs */}
        <nav className="hidden md:flex items-center gap-1 pb-3 border-t border-border/30 pt-2">
          <Button
            variant={activeTab === "dashboard" ? "secondary" : "ghost"}
            onClick={() => onTabChange("dashboard")}
            className="font-medium"
          >
            {t("header.dashboard")}
          </Button>
          <Button
            variant={activeTab === "ai-decisions" ? "secondary" : "ghost"}
            onClick={() => onTabChange("ai-decisions")}
            className="font-medium"
          >
            {t("header.aiDecisions")}
          </Button>
          <Button
            variant={activeTab === "trading" ? "secondary" : "ghost"}
            onClick={() => onTabChange("trading")}
            className="font-medium"
          >
            {t("header.trading")}
          </Button>
          <Button
            variant={activeTab === "watchlist" ? "secondary" : "ghost"}
            onClick={() => onTabChange("watchlist")}
            className="font-medium"
          >
            {t("header.watchlist")}
          </Button>
          <Button
            variant={activeTab === "insights" ? "secondary" : "ghost"}
            onClick={() => onTabChange("insights")}
            className="font-medium"
          >
            💡 {t("header.insights")}
          </Button>
          {/* Jeoningu Lab - Only show for Korean market */}
          {market === "KR" && (
            <Button
              variant={activeTab === "jeoningu-lab" ? "secondary" : "ghost"}
              onClick={() => onTabChange("jeoningu-lab")}
              className={`font-medium ${
                activeTab === "jeoningu-lab"
                  ? "bg-gradient-to-r from-purple-600 to-indigo-600 text-white hover:from-purple-700 hover:to-indigo-700"
                  : "hover:bg-purple-50 dark:hover:bg-purple-950"
              }`}
            >
              🧪 {language === "ko" ? "실험실" : "Lab"}
            </Button>
          )}
        </nav>

        {/* Mobile Navigation */}
        <nav className="md:hidden flex items-center gap-1 pb-3 overflow-x-auto">
          <Button
            variant={activeTab === "dashboard" ? "secondary" : "ghost"}
            onClick={() => onTabChange("dashboard")}
            size="sm"
            className="font-medium whitespace-nowrap"
          >
            {t("header.dashboard")}
          </Button>
          <Button
            variant={activeTab === "ai-decisions" ? "secondary" : "ghost"}
            onClick={() => onTabChange("ai-decisions")}
            size="sm"
            className="font-medium whitespace-nowrap"
          >
            {t("header.aiDecisions")}
          </Button>
          <Button
            variant={activeTab === "trading" ? "secondary" : "ghost"}
            onClick={() => onTabChange("trading")}
            size="sm"
            className="font-medium whitespace-nowrap"
          >
            {t("header.trading")}
          </Button>
          <Button
            variant={activeTab === "watchlist" ? "secondary" : "ghost"}
            onClick={() => onTabChange("watchlist")}
            size="sm"
            className="font-medium whitespace-nowrap"
          >
            {t("header.watchlist")}
          </Button>
          <Button
            variant={activeTab === "insights" ? "secondary" : "ghost"}
            onClick={() => onTabChange("insights")}
            size="sm"
            className="font-medium whitespace-nowrap"
          >
            💡 {t("header.insights")}
          </Button>
          {/* Jeoningu Lab - Only show for Korean market (Mobile) */}
          {market === "KR" && (
            <Button
              variant={activeTab === "jeoningu-lab" ? "secondary" : "ghost"}
              onClick={() => onTabChange("jeoningu-lab")}
              size="sm"
              className={`font-medium whitespace-nowrap ${
                activeTab === "jeoningu-lab"
                  ? "bg-gradient-to-r from-purple-600 to-indigo-600 text-white hover:from-purple-700 hover:to-indigo-700"
                  : "hover:bg-purple-50 dark:hover:bg-purple-950"
              }`}
            >
              🧪 {language === "ko" ? "실험실" : "Lab"}
            </Button>
          )}
        </nav>
      </div>
    </header>
  )
}
