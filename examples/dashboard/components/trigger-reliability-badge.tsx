"use client"

import { Shield } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { useLanguage } from "@/components/language-provider"
import type { TriggerReliabilityData } from "@/types/dashboard"

const GRADE_STYLES: Record<string, { bg: string; text: string; border: string }> = {
  A: { bg: "bg-emerald-500/10", text: "text-emerald-500", border: "border-emerald-500/30" },
  B: { bg: "bg-blue-500/10", text: "text-blue-500", border: "border-blue-500/30" },
  C: { bg: "bg-amber-500/10", text: "text-amber-500", border: "border-amber-500/30" },
  D: { bg: "bg-zinc-500/10", text: "text-zinc-400", border: "border-zinc-500/30" },
}

interface TriggerReliabilityBadgeProps {
  data?: TriggerReliabilityData
  onNavigateToInsights?: () => void
}

export function TriggerReliabilityBadge({ data, onNavigateToInsights }: TriggerReliabilityBadgeProps) {
  const { t } = useLanguage()

  if (!data?.best_trigger || data.trigger_reliability.length === 0) {
    return null
  }

  const bestItem = data.trigger_reliability.find(
    (item) => item.trigger_type === data.best_trigger
  )
  if (!bestItem) return null

  const grade = bestItem.grade
  const style = GRADE_STYLES[grade] || GRADE_STYLES.D
  const gradeLabel = t(`insights.triggerReliability.grade${grade}` as any)
  const winRate = bestItem.analysis_accuracy.win_rate_30d

  return (
    <button
      onClick={onNavigateToInsights}
      className={`
        inline-flex items-center gap-2 px-3 py-1.5 rounded-lg border
        ${style.bg} ${style.border}
        hover:opacity-80 transition-opacity cursor-pointer
        text-sm
      `}
    >
      <Shield className={`w-3.5 h-3.5 ${style.text}`} />
      <span className="text-muted-foreground">
        {t("insights.triggerReliability.miniBadge")}:
      </span>
      <span className={`font-medium ${style.text}`}>
        {data.best_trigger}
      </span>
      <Badge variant="outline" className={`${style.text} ${style.border} text-xs px-1.5 py-0`}>
        {grade}
      </Badge>
      {winRate != null && (
        <span className="text-muted-foreground text-xs">
          ({(winRate * 100).toFixed(0)}%)
        </span>
      )}
      <span className="text-muted-foreground/60 text-xs hidden sm:inline">
        — {gradeLabel}
      </span>
    </button>
  )
}
