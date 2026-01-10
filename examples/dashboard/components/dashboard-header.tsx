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

interface DashboardHeaderProps {
  activeTab: "dashboard" | "ai-decisions" | "trading" | "watchlist" | "insights" | "jeoningu-lab"
  onTabChange: (tab: "dashboard" | "ai-decisions" | "trading" | "watchlist" | "insights" | "jeoningu-lab") => void
  lastUpdated?: string
}

export function DashboardHeader({ activeTab, onTabChange, lastUpdated }: DashboardHeaderProps) {
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
        <div className="flex h-16 items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="flex items-center justify-center w-10 h-10 rounded-lg bg-gradient-to-br from-primary via-purple-600 to-blue-600">
              <TrendingUp className="w-6 h-6 text-white" />
            </div>
            <div>
              <div className="flex items-center gap-2">
                <h1 className="text-xl font-bold bg-gradient-to-r from-primary via-purple-600 to-blue-600 bg-clip-text text-transparent">
                  Prism Insight
                </h1>
                <span className="px-2 py-0.5 text-xs font-semibold rounded-full bg-gradient-to-r from-primary to-purple-600 text-white">
                  {t("header.season")}
                </span>
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
              <div className="flex items-center gap-3 mt-0.5">
                <p className="text-xs text-muted-foreground">
                  {t("header.startDate")}
                </p>
                <span className="text-muted-foreground/30">•</span>
                <p className="text-xs text-muted-foreground">
                  {t("header.updated")}: {formatLastUpdated()}
                </p>
              </div>
            </div>
          </div>

          <nav className="hidden md:flex items-center gap-1">
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
          </nav>

          <div className="flex items-center gap-2">
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

            <TooltipProvider>
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button
                    variant="ghost"
                    size="icon"
                    onClick={() => setLanguage(language === "ko" ? "en" : "ko")}
                    className="rounded-full"
                  >
                    <Languages className="h-5 w-5" />
                    <span className="sr-only">Toggle Language</span>
                  </Button>
                </TooltipTrigger>
                <TooltipContent>
                  <p className="text-xs">{language === "ko" ? "English" : "한국어"}</p>
                </TooltipContent>
              </Tooltip>
            </TooltipProvider>

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
        </nav>
      </div>
    </header>
  )
}
