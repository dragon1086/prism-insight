"use client"

import { Moon, Sun, TrendingUp, Github, Send, Languages, Sparkles } from "lucide-react"
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
      {/* Platinum Sponsor Bar - Premium separated placement */}
      <div className="hidden sm:block border-b border-border/20 bg-gradient-to-r from-slate-50/50 via-blue-50/30 to-indigo-50/50 dark:from-slate-900/50 dark:via-blue-950/30 dark:to-indigo-950/50">
        <div className="container mx-auto px-4 max-w-[1600px]">
          <div className="flex items-center justify-center py-1.5 gap-3">
            <div className="flex items-center gap-2">
              <Sparkles className="w-3 h-3 text-amber-500" />
              <span className="text-[10px] font-semibold uppercase tracking-widest text-amber-600/90 dark:text-amber-400/90">
                Platinum Sponsor
              </span>
            </div>
            <div className="w-px h-3 bg-border/50" />
            <a
              href="https://wrks.ai/en"
              target="_blank"
              rel="noopener noreferrer"
              className="group flex items-center gap-2 px-3 py-1 rounded-full bg-white/60 dark:bg-white/5 border border-blue-200/50 dark:border-blue-500/20 hover:border-blue-400/50 dark:hover:border-blue-400/40 hover:bg-white/80 dark:hover:bg-white/10 transition-all duration-200 shadow-sm hover:shadow-md"
            >
              <img
                src="/wrks_ai_logo.png"
                alt="WrksAI"
                className="h-5 w-auto transition-transform duration-200 group-hover:scale-105"
              />
              <span className="text-xs font-medium text-slate-700 dark:text-slate-200 group-hover:text-blue-600 dark:group-hover:text-blue-400 transition-colors">
                WrksAI
              </span>
              <svg className="w-3 h-3 text-slate-400 group-hover:text-blue-500 transition-colors" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14" />
              </svg>
            </a>
          </div>
        </div>
      </div>

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
              <p className="text-xs text-muted-foreground">
                {t("header.updated")}: {formatLastUpdated()}
              </p>
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
          <div className="sm:hidden flex flex-col items-center gap-2 pb-3">
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
            {/* Mobile Sponsor Badge */}
            <a
              href="https://wrks.ai/en"
              target="_blank"
              rel="noopener noreferrer"
              className="flex items-center gap-2 px-3 py-1.5 rounded-full bg-gradient-to-r from-slate-100/80 to-blue-50/80 dark:from-slate-800/80 dark:to-blue-950/80 border border-blue-200/40 dark:border-blue-700/30"
            >
              <Sparkles className="w-3 h-3 text-amber-500" />
              <span className="text-[9px] font-semibold uppercase tracking-wider text-amber-600/90 dark:text-amber-400/90">
                Sponsor
              </span>
              <img
                src="/wrks_ai_logo.png"
                alt="WrksAI"
                className="h-4 w-auto"
              />
            </a>
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
