"use client"

import { Github, Send, Globe } from "lucide-react"
import { Card } from "@/components/ui/card"
import { useLanguage } from "@/components/language-provider"

export function ProjectFooter() {
  const { t } = useLanguage()
  return (
    <footer className="mt-12 border-t border-border/40">
      <div className="container mx-auto px-4 py-8 max-w-[1600px]">
        <Card className="bg-gradient-to-br from-background/50 to-muted/30 border-border/50 backdrop-blur-sm">
          <div className="p-6">
            <div className="grid grid-cols-1 md:grid-cols-3 gap-8">
              {/* 프로젝트 소개 */}
              <div className="space-y-3">
                <h3 className="text-lg font-bold bg-gradient-to-r from-primary to-primary/60 bg-clip-text text-transparent">
                  🔍 PRISM-INSIGHT
                </h3>
                <p className="text-sm text-muted-foreground leading-relaxed">
                  {t("footer.description")}
                  <br />
                  <span className="text-xs">
                    {t("footer.openSource")}
                  </span>
                </p>
                <div className="pt-2">
                  <div className="flex items-center gap-2 text-xs text-muted-foreground">
                    <div className="flex items-center gap-1">
                      <div className="w-2 h-2 rounded-full bg-green-500 animate-pulse" />
                      <span>GPT-4.1</span>
                    </div>
                    <span>•</span>
                    <div className="flex items-center gap-1">
                      <div className="w-2 h-2 rounded-full bg-blue-500 animate-pulse" />
                      <span>GPT-5.1</span>
                    </div>
                    <span>•</span>
                    <div className="flex items-center gap-1">
                      <div className="w-2 h-2 rounded-full bg-purple-500 animate-pulse" />
                      <span>Claude 4.5</span>
                    </div>
                  </div>
                </div>
              </div>

              {/* 주요 기능 */}
              <div className="space-y-3">
                <h4 className="text-sm font-semibold text-foreground/80">{t("footer.features")}</h4>
                <ul className="space-y-2 text-sm text-muted-foreground">
                  <li className="flex items-start gap-2">
                    <span className="text-primary mt-0.5">✓</span>
                    <span>{t("footer.feature1")}</span>
                  </li>
                  <li className="flex items-start gap-2">
                    <span className="text-primary mt-0.5">✓</span>
                    <span>{t("footer.feature2")}</span>
                  </li>
                  <li className="flex items-start gap-2">
                    <span className="text-primary mt-0.5">✓</span>
                    <span>{t("footer.feature3")}</span>
                  </li>
                  <li className="flex items-start gap-2">
                    <span className="text-primary mt-0.5">✓</span>
                    <span>{t("footer.feature4")}</span>
                  </li>
                </ul>
              </div>

              {/* 링크 */}
              <div className="space-y-3">
                <h4 className="text-sm font-semibold text-foreground/80">{t("footer.links")}</h4>
                <div className="flex flex-col gap-3">
                  {/* GitHub */}
                  <a
                    href="https://github.com/dragon1086/prism-insight"
                    target="_blank"
                    rel="noopener noreferrer"
                    className="group flex items-center gap-3 p-3 rounded-lg bg-background/60 hover:bg-background/80 border border-border/50 hover:border-border transition-all duration-200 hover:shadow-md"
                  >
                    <div className="flex items-center justify-center w-10 h-10 rounded-lg bg-gradient-to-br from-primary/20 to-primary/5 group-hover:from-primary/30 group-hover:to-primary/10 transition-all">
                      <Github className="h-5 w-5 text-primary" />
                    </div>
                    <div className="flex-1">
                      <div className="text-sm font-medium text-foreground group-hover:text-primary transition-colors">
                        GitHub Repository
                      </div>
                      <div className="text-xs text-muted-foreground">
                        {t("footer.sourceCode")}
                      </div>
                    </div>
                  </a>

                  {/* Telegram */}
                  <a
                    href="https://t.me/stock_ai_agent"
                    target="_blank"
                    rel="noopener noreferrer"
                    className="group flex items-center gap-3 p-3 rounded-lg bg-background/60 hover:bg-background/80 border border-border/50 hover:border-border transition-all duration-200 hover:shadow-md"
                  >
                    <div className="flex items-center justify-center w-10 h-10 rounded-lg bg-gradient-to-br from-blue-500/20 to-blue-500/5 group-hover:from-blue-500/30 group-hover:to-blue-500/10 transition-all">
                      <Send className="h-5 w-5 text-blue-500" />
                    </div>
                    <div className="flex-1">
                      <div className="text-sm font-medium text-foreground group-hover:text-blue-500 transition-colors">
                        Telegram Channel
                      </div>
                      <div className="text-xs text-muted-foreground">
                        {t("footer.telegramDesc")}
                      </div>
                    </div>
                  </a>

                  {/* Landing Page */}
                  <a
                    href="https://prism-insight-landing.vercel.app"
                    target="_blank"
                    rel="noopener noreferrer"
                    className="group flex items-center gap-3 p-3 rounded-lg bg-background/60 hover:bg-background/80 border border-border/50 hover:border-border transition-all duration-200 hover:shadow-md"
                  >
                    <div className="flex items-center justify-center w-10 h-10 rounded-lg bg-gradient-to-br from-emerald-500/20 to-emerald-500/5 group-hover:from-emerald-500/30 group-hover:to-emerald-500/10 transition-all">
                      <Globe className="h-5 w-5 text-emerald-500" />
                    </div>
                    <div className="flex-1">
                      <div className="text-sm font-medium text-foreground group-hover:text-emerald-500 transition-colors">
                        About PRISM-INSIGHT
                      </div>
                      <div className="text-xs text-muted-foreground">
                        {t("footer.landingDesc")}
                      </div>
                    </div>
                  </a>
                </div>

                {/* Star 통계 */}
                <div className="pt-2">
                  <a
                    href="https://github.com/dragon1086/prism-insight"
                    target="_blank"
                    rel="noopener noreferrer"
                    className="inline-flex items-center gap-2 text-xs text-muted-foreground hover:text-primary transition-colors"
                  >
                    <span>⭐</span>
                    <span>{t("footer.stars")}</span>
                  </a>
                </div>
              </div>
            </div>

            {/* 하단 구분선 및 저작권 */}
            <div className="mt-6 pt-6 border-t border-border/30">
              <div className="flex flex-col md:flex-row justify-between items-center gap-4 text-xs text-muted-foreground">
                <div className="flex flex-col md:flex-row items-center gap-2">
                  <div className="flex items-center gap-2">
                    <span>© 2025 PRISM-INSIGHT</span>
                    <span className="hidden md:inline">•</span>
                    <span className="text-xs">All rights reserved</span>
                  </div>
                  {/* Platinum Sponsor - Inline */}
                  <div className="flex items-center gap-2">
                    <span className="hidden md:inline text-muted-foreground/40">•</span>
                    <a
                      href="https://wrks.ai/en"
                      target="_blank"
                      rel="noopener noreferrer"
                      className="group flex items-center gap-2 px-2.5 py-1 rounded-md bg-gradient-to-r from-amber-500/5 to-yellow-500/5 hover:from-amber-500/10 hover:to-yellow-500/10 border border-amber-500/20 transition-all duration-200"
                    >
                      <span className="text-[10px] text-amber-600/80 dark:text-amber-400/80 font-medium uppercase tracking-wider">
                        Platinum Sponsor
                      </span>
                      <div className="flex items-center gap-1.5">
                        <img
                          src="/wrks_ai_logo.png"
                          alt="WrksAI"
                          className="h-3.5 w-auto opacity-80 group-hover:opacity-100 transition-opacity"
                        />
                        <span className="text-xs text-muted-foreground/90 group-hover:text-foreground transition-colors font-medium">
                          WrksAI
                        </span>
                      </div>
                    </a>
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  <span className="px-2 py-1 rounded-md bg-primary/10 text-primary text-xs font-medium">
                    Open Source
                  </span>
                  <span className="px-2 py-1 rounded-md bg-green-500/10 text-green-500 text-xs font-medium">
                    MIT License
                  </span>
                </div>
              </div>
            </div>

            {/* 면책 조항 */}
            <div className="mt-4 pt-4 border-t border-border/20">
              <p className="text-xs text-muted-foreground/60 text-center leading-relaxed">
                {t("footer.disclaimer")}
              </p>
            </div>
          </div>
        </Card>
      </div>
    </footer>
  )
}
