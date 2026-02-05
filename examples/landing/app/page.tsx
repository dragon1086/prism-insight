'use client'

import { useState, useEffect, useRef } from 'react'
import Image from 'next/image'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import {
  Star,
  Copy,
  Check,
  Play,
  Github,
  ExternalLink,
  Bot,
  TrendingUp,
  Bell,
  Zap,
  Globe,
  Shield,
  ChevronRight,
  Terminal,
  Heart
} from 'lucide-react'

// GitHub star count fetcher (client-side)
function useGitHubStars() {
  const [stars, setStars] = useState<number | null>(null)

  useEffect(() => {
    fetch('https://api.github.com/repos/dragon1086/prism-insight')
      .then(res => res.json())
      .then(data => setStars(data.stargazers_count))
      .catch(() => setStars(null))
  }, [])

  return stars
}

// Typewriter effect
function TypewriterText({ text, delay = 50 }: { text: string; delay?: number }) {
  const [displayed, setDisplayed] = useState('')
  const [showCursor, setShowCursor] = useState(true)

  useEffect(() => {
    let i = 0
    const timer = setInterval(() => {
      if (i < text.length) {
        setDisplayed(text.slice(0, i + 1))
        i++
      } else {
        clearInterval(timer)
      }
    }, delay)

    const cursorTimer = setInterval(() => {
      setShowCursor(prev => !prev)
    }, 530)

    return () => {
      clearInterval(timer)
      clearInterval(cursorTimer)
    }
  }, [text, delay])

  return (
    <span>
      {displayed}
      <span className={`${showCursor ? 'opacity-100' : 'opacity-0'} transition-opacity`}>▊</span>
    </span>
  )
}

// Matrix rain effect
function MatrixRain() {
  const canvasRef = useRef<HTMLCanvasElement>(null)

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return

    const ctx = canvas.getContext('2d')
    if (!ctx) return

    const resize = () => {
      canvas.width = canvas.offsetWidth
      canvas.height = canvas.offsetHeight
    }
    resize()
    window.addEventListener('resize', resize)

    const chars = '01アイウエオカキクケコ株価分析PRISM'
    const fontSize = 14
    const columns = Math.floor(canvas.width / fontSize)
    const drops: number[] = Array(columns).fill(1)

    const draw = () => {
      ctx.fillStyle = 'rgba(0, 0, 0, 0.05)'
      ctx.fillRect(0, 0, canvas.width, canvas.height)

      ctx.fillStyle = '#00ff8855'
      ctx.font = `${fontSize}px monospace`

      for (let i = 0; i < drops.length; i++) {
        const char = chars[Math.floor(Math.random() * chars.length)]
        ctx.fillText(char, i * fontSize, drops[i] * fontSize)

        if (drops[i] * fontSize > canvas.height && Math.random() > 0.975) {
          drops[i] = 0
        }
        drops[i]++
      }
    }

    const interval = setInterval(draw, 50)

    return () => {
      clearInterval(interval)
      window.removeEventListener('resize', resize)
    }
  }, [])

  return (
    <canvas
      ref={canvasRef}
      className="absolute inset-0 w-full h-full opacity-30 pointer-events-none"
    />
  )
}

// Copy button with feedback
function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false)

  const handleCopy = async () => {
    await navigator.clipboard.writeText(text)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  return (
    <button
      onClick={handleCopy}
      className="absolute top-3 right-3 p-2 rounded-md bg-white/5 hover:bg-white/10 transition-colors"
      aria-label="Copy code"
    >
      {copied ? (
        <Check className="w-4 h-4 text-emerald-400" />
      ) : (
        <Copy className="w-4 h-4 text-zinc-400" />
      )}
    </button>
  )
}

// Agent data with actual images
const AGENTS = [
  { name: 'Technical Analyst', role: 'Price & Volume Analysis', image: '/agents/technical_analyst.jpeg', delay: 0 },
  { name: 'Trading Flow Analyst', role: 'Investor Patterns', image: '/agents/tranding_flow_analyst.jpeg', delay: 0.05 },
  { name: 'Financial Analyst', role: 'Valuation & Metrics', image: '/agents/financial_analyst.jpeg', delay: 0.1 },
  { name: 'Industry Analyst', role: 'Business & Competition', image: '/agents/industry_analyst.jpeg', delay: 0.15 },
  { name: 'Information Analyst', role: 'News & Catalysts', image: '/agents/information_analyst.jpeg', delay: 0.2 },
  { name: 'Market Analyst', role: 'Macro Environment', image: '/agents/market_analyst.jpeg', delay: 0.25 },
  { name: 'Investment Strategist', role: 'Strategy Synthesis', image: '/agents/investment_strategist.jpeg', delay: 0.3 },
  { name: 'Summary Optimizer', role: 'Telegram Messages', image: '/agents/summary_specialist.jpeg', delay: 0.35 },
  { name: 'Quality Evaluator', role: 'Output Validation', image: '/agents/quality_inspector.jpeg', delay: 0.4 },
  { name: 'Buy Specialist', role: 'Entry Decisions', image: '/agents/buy_specialist.jpeg', delay: 0.45 },
  { name: 'Sell Specialist', role: 'Exit Timing', image: '/agents/sell_specialist.jpeg', delay: 0.5 },
  { name: 'Portfolio Consultant', role: 'User Advice', image: '/agents/portfolio_consultant.jpeg', delay: 0.55 },
  { name: 'Dialogue Manager', role: 'Conversation Context', image: '/agents/dialogue_manager.jpeg', delay: 0.6 },
]

export default function LandingPage() {
  const stars = useGitHubStars()
  const [videoPlaying, setVideoPlaying] = useState(false)

  const quickstartCode = `# 1. Clone repository
git clone https://github.com/dragon1086/prism-insight.git
cd prism-insight

# 2. Quick setup (requires OpenAI API key)
./quickstart.sh YOUR_OPENAI_API_KEY

# 3. Generate a PDF report (demo.py = report generation only)
python demo.py AAPL`

  return (
    <div className="min-h-screen bg-zinc-950 text-zinc-100 overflow-x-hidden">
      {/* Noise texture overlay */}
      <div
        className="fixed inset-0 pointer-events-none opacity-[0.015] z-50"
        style={{
          backgroundImage: `url("data:image/svg+xml,%3Csvg viewBox='0 0 256 256' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='noise'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23noise)'/%3E%3C/svg%3E")`,
        }}
      />

      {/* Hero Section */}
      <section className="relative min-h-screen flex items-center justify-center px-4 py-20 overflow-hidden">
        <MatrixRain />

        {/* Gradient orbs */}
        <div className="absolute top-1/4 -left-32 w-96 h-96 bg-emerald-500/20 rounded-full blur-[128px]" />
        <div className="absolute bottom-1/4 -right-32 w-96 h-96 bg-cyan-500/20 rounded-full blur-[128px]" />

        <div className="relative z-10 max-w-5xl mx-auto text-center">
          {/* Badge */}
          <div className="inline-flex items-center gap-2 mb-8 px-4 py-2 rounded-full border border-emerald-500/30 bg-emerald-500/10 backdrop-blur-sm animate-fade-in">
            <span className="relative flex h-2 w-2">
              <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75"></span>
              <span className="relative inline-flex rounded-full h-2 w-2 bg-emerald-500"></span>
            </span>
            <span className="text-sm font-mono text-emerald-300">Open Source • AGPL-3.0</span>
          </div>

          {/* Logo & Title */}
          <div className="mb-6">
            <h1 className="text-5xl md:text-7xl font-bold tracking-tight mb-4">
              <span className="bg-gradient-to-r from-emerald-400 via-cyan-400 to-emerald-400 bg-clip-text text-transparent bg-[length:200%_auto] animate-gradient">
                PRISM
              </span>
              <span className="text-zinc-400">-</span>
              <span className="text-zinc-100">INSIGHT</span>
            </h1>
          </div>

          {/* Tagline with typewriter */}
          <div className="h-16 md:h-12 mb-8">
            <p className="text-xl md:text-2xl font-mono text-zinc-400">
              <TypewriterText
                text="AI-powered Korean & US stock analysis with automated trading"
                delay={40}
              />
            </p>
          </div>

          {/* Description */}
          <p className="text-lg text-zinc-500 max-w-2xl mx-auto mb-12 leading-relaxed">
            13 specialized AI agents analyze markets in real-time, generate trading signals,
            and execute trades automatically via Telegram alerts.
          </p>

          {/* CTA Buttons */}
          <div className="flex flex-col sm:flex-row gap-4 justify-center items-center mb-16">
            <Button
              size="lg"
              className="bg-emerald-500 hover:bg-emerald-400 text-zinc-950 font-semibold px-8 h-12 text-base gap-2 group"
              asChild
            >
              <a href="https://github.com/dragon1086/prism-insight" target="_blank" rel="noopener noreferrer">
                <Github className="w-5 h-5" />
                <span>Star on GitHub</span>
                {stars !== null && (
                  <Badge variant="secondary" className="ml-2 bg-zinc-950/50 text-emerald-300 border-0">
                    {stars.toLocaleString()}
                  </Badge>
                )}
                <ChevronRight className="w-4 h-4 group-hover:translate-x-1 transition-transform" />
              </a>
            </Button>

            <Button
              size="lg"
              variant="outline"
              className="border-zinc-700 hover:border-zinc-500 hover:bg-zinc-900 px-8 h-12 text-base gap-2"
              asChild
            >
              <a href="https://analysis.stocksimulation.kr" target="_blank" rel="noopener noreferrer">
                <ExternalLink className="w-5 h-5" />
                Live Dashboard
              </a>
            </Button>
          </div>

          {/* Terminal preview */}
          <div className="relative max-w-2xl mx-auto">
            <div className="absolute -inset-1 bg-gradient-to-r from-emerald-500/20 via-cyan-500/20 to-emerald-500/20 rounded-xl blur-lg opacity-50" />
            <Card className="relative bg-zinc-900/90 border-zinc-800 backdrop-blur-xl overflow-hidden">
              <div className="flex items-center gap-2 px-4 py-3 border-b border-zinc-800 bg-zinc-900/50">
                <div className="flex gap-1.5">
                  <div className="w-3 h-3 rounded-full bg-red-500/80" />
                  <div className="w-3 h-3 rounded-full bg-yellow-500/80" />
                  <div className="w-3 h-3 rounded-full bg-green-500/80" />
                </div>
                <span className="text-xs font-mono text-zinc-500 ml-2">prism-insight — python</span>
              </div>
              <CardContent className="p-0">
                <pre className="p-4 text-sm font-mono text-left overflow-x-auto">
                  <code className="text-emerald-400">$ python demo.py NVDA</code>
                  {'\n\n'}
                  <code className="text-zinc-500">Looking up company name for NVDA...</code>
                  {'\n'}
                  <code className="text-zinc-400">Found: NVIDIA Corporation</code>
                  {'\n\n'}
                  <code className="text-cyan-400">[1/3] Generating AI analysis report...</code>
                  {'\n'}
                  <code className="text-zinc-500">      This may take 3-5 minutes. AI agents are analyzing:</code>
                  {'\n'}
                  <code className="text-zinc-500">      - Price & volume trends</code>
                  {'\n'}
                  <code className="text-zinc-500">      - Financial fundamentals</code>
                  {'\n'}
                  <code className="text-zinc-500">      - Investment strategy</code>
                  {'\n\n'}
                  <code className="text-emerald-400">[2/3] Analysis complete! (185.2 seconds)</code>
                  {'\n'}
                  <code className="text-emerald-400">[3/3] Saving report files...</code>
                  {'\n\n'}
                  <code className="text-yellow-400">  PDF: prism-us/pdf_reports/NVDA_20260201.pdf</code>
                </pre>
              </CardContent>
            </Card>
          </div>
        </div>

        {/* Scroll indicator */}
        <div className="absolute bottom-8 left-1/2 -translate-x-1/2 animate-bounce">
          <div className="w-6 h-10 rounded-full border-2 border-zinc-600 flex items-start justify-center p-2">
            <div className="w-1 h-2 bg-zinc-500 rounded-full animate-pulse" />
          </div>
        </div>
      </section>

      {/* Platinum Sponsor Section */}
      <section className="relative py-16 px-4 border-t border-zinc-800/50">
        <div className="max-w-2xl mx-auto text-center">
          <Badge className="mb-4 bg-amber-500/10 text-amber-400 border-amber-500/30">
            🏆 Platinum Sponsor
          </Badge>
          <a
            href="https://wrks.ai/en"
            target="_blank"
            rel="noopener noreferrer"
            className="block group"
          >
            <div className="relative inline-block mb-6">
              <div className="absolute -inset-2 bg-gradient-to-r from-amber-500/20 via-yellow-500/20 to-amber-500/20 rounded-2xl blur-lg opacity-0 group-hover:opacity-100 transition-opacity duration-500" />
              <div className="relative bg-white/5 p-6 rounded-2xl border border-zinc-800 group-hover:border-amber-500/50 transition-colors duration-300">
                <Image
                  src="/wrks_ai_logo.png"
                  alt="WrksAI Logo"
                  width={200}
                  height={60}
                  className="h-12 w-auto"
                />
              </div>
            </div>
          </a>
          <p className="text-zinc-400 leading-relaxed">
            <a
              href="https://www.ai3.kr/"
              target="_blank"
              rel="noopener noreferrer"
              className="text-emerald-400 hover:text-emerald-300 transition-colors font-medium"
            >
              AI3
            </a>
            , creator of{' '}
            <a
              href="https://wrks.ai/en"
              target="_blank"
              rel="noopener noreferrer"
              className="text-emerald-400 hover:text-emerald-300 transition-colors font-medium"
            >
              WrksAI
            </a>
            {' '}- the AI assistant for professionals, proudly sponsors{' '}
            <span className="text-zinc-200 font-semibold">PRISM-INSIGHT</span>
            {' '}- the AI assistant for investors.
          </p>
        </div>
      </section>

      {/* Features Section */}
      <section className="relative py-32 px-4">
        <div className="max-w-6xl mx-auto">
          <div className="text-center mb-16">
            <Badge className="mb-4 bg-zinc-800 text-zinc-300 border-zinc-700">Meet the Team</Badge>
            <h2 className="text-4xl md:text-5xl font-bold mb-6">
              <span className="text-zinc-100">13 AI Agents.</span>
              <br />
              <span className="text-zinc-500">One Mission.</span>
            </h2>
            <p className="text-zinc-500 max-w-xl mx-auto">
              Each agent specializes in a specific aspect of stock analysis,
              working together to provide comprehensive market insights.
            </p>
          </div>

          {/* Agent Grid */}
          <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 gap-4 mb-16">
            {AGENTS.map((agent, i) => (
              <Card
                key={agent.name}
                className="bg-zinc-900/50 border-zinc-800 hover:border-emerald-500/50 transition-all duration-300 group overflow-hidden"
                style={{ animationDelay: `${agent.delay}s` }}
              >
                <CardContent className="p-0">
                  <div className="relative aspect-square overflow-hidden">
                    <Image
                      src={agent.image}
                      alt={agent.name}
                      fill
                      className="object-cover group-hover:scale-110 transition-transform duration-500"
                    />
                    <div className="absolute inset-0 bg-gradient-to-t from-zinc-950 via-zinc-950/20 to-transparent" />
                    <div className="absolute bottom-0 left-0 right-0 p-3">
                      <p className="text-xs font-mono text-emerald-400 mb-0.5">#{i + 1}</p>
                      <h3 className="font-semibold text-zinc-100 text-sm leading-tight">{agent.name}</h3>
                      <p className="text-xs text-zinc-500 mt-0.5">{agent.role}</p>
                    </div>
                  </div>
                </CardContent>
              </Card>
            ))}
          </div>

          {/* Feature highlights */}
          <div className="grid md:grid-cols-3 gap-8">
            <div className="text-center p-8 rounded-2xl bg-zinc-900/30 border border-zinc-800/50">
              <div className="inline-flex items-center justify-center w-16 h-16 rounded-2xl bg-gradient-to-br from-emerald-500/20 to-emerald-500/5 mb-6">
                <Globe className="w-8 h-8 text-emerald-400" />
              </div>
              <h3 className="text-xl font-semibold mb-3">Dual Market Support</h3>
              <p className="text-zinc-500">
                KOSPI/KOSDAQ and NYSE/NASDAQ. Analyze any market with the same powerful engine.
              </p>
            </div>

            <div className="text-center p-8 rounded-2xl bg-zinc-900/30 border border-zinc-800/50">
              <div className="inline-flex items-center justify-center w-16 h-16 rounded-2xl bg-gradient-to-br from-cyan-500/20 to-cyan-500/5 mb-6">
                <Bell className="w-8 h-8 text-cyan-400" />
              </div>
              <h3 className="text-xl font-semibold mb-3">Telegram Alerts</h3>
              <p className="text-zinc-500">
                Real-time trading signals delivered directly to your phone. Never miss an opportunity.
              </p>
            </div>

            <div className="text-center p-8 rounded-2xl bg-zinc-900/30 border border-zinc-800/50">
              <div className="inline-flex items-center justify-center w-16 h-16 rounded-2xl bg-gradient-to-br from-purple-500/20 to-purple-500/5 mb-6">
                <Zap className="w-8 h-8 text-purple-400" />
              </div>
              <h3 className="text-xl font-semibold mb-3">Auto Trading</h3>
              <p className="text-zinc-500">
                Execute trades automatically via KIS API. Set it and forget it.
              </p>
            </div>
          </div>
        </div>
      </section>

      {/* Demo Video Section */}
      <section className="relative py-32 px-4 bg-zinc-900/50">
        <div className="absolute inset-0 bg-gradient-to-b from-zinc-950 via-transparent to-zinc-950" />

        <div className="relative max-w-4xl mx-auto">
          <div className="text-center mb-12">
            <Badge className="mb-4 bg-zinc-800 text-zinc-300 border-zinc-700">Demo</Badge>
            <h2 className="text-4xl font-bold mb-4">See It In Action</h2>
            <p className="text-zinc-500">Watch how PRISM-INSIGHT analyzes stocks in real-time</p>
          </div>

          <div className="relative aspect-video rounded-2xl overflow-hidden bg-zinc-900 border border-zinc-800">
            {!videoPlaying ? (
              <div className="absolute inset-0 flex items-center justify-center bg-zinc-900">
                <Image
                  src={`https://img.youtube.com/vi/LVOAdVCh1QE/maxresdefault.jpg`}
                  alt="PRISM-INSIGHT Demo Video"
                  fill
                  className="object-cover opacity-50"
                />
                <button
                  onClick={() => setVideoPlaying(true)}
                  className="relative z-10 flex items-center justify-center w-20 h-20 rounded-full bg-emerald-500 hover:bg-emerald-400 transition-colors group"
                >
                  <Play className="w-8 h-8 text-zinc-950 ml-1 group-hover:scale-110 transition-transform" />
                </button>
              </div>
            ) : (
              <iframe
                src="https://www.youtube.com/embed/LVOAdVCh1QE?autoplay=1"
                title="PRISM-INSIGHT Demo"
                allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture"
                allowFullScreen
                className="absolute inset-0 w-full h-full"
              />
            )}
          </div>
        </div>
      </section>

      {/* QuickStart Section */}
      <section className="relative py-32 px-4">
        <div className="max-w-3xl mx-auto">
          <div className="text-center mb-12">
            <Badge className="mb-4 bg-zinc-800 text-zinc-300 border-zinc-700">Quick Start</Badge>
            <h2 className="text-4xl font-bold mb-4">Up and Running in 3 Steps</h2>
            <p className="text-zinc-500">Get your first analysis in under 5 minutes</p>
          </div>

          <div className="relative">
            <div className="absolute -inset-1 bg-gradient-to-r from-emerald-500/20 via-cyan-500/20 to-emerald-500/20 rounded-xl blur opacity-30" />
            <Card className="relative bg-zinc-900 border-zinc-800 overflow-hidden">
              <div className="flex items-center justify-between px-4 py-3 border-b border-zinc-800 bg-zinc-950/50">
                <div className="flex items-center gap-2">
                  <Terminal className="w-4 h-4 text-zinc-500" />
                  <span className="text-sm font-mono text-zinc-500">terminal</span>
                </div>
                <CopyButton text={quickstartCode} />
              </div>
              <CardContent className="p-0">
                <pre className="p-6 text-sm font-mono overflow-x-auto">
                  <code className="text-zinc-400">
                    <span className="text-zinc-500"># 1. Clone repository</span>
                    {'\n'}
                    <span className="text-emerald-400">git clone</span> https://github.com/dragon1086/prism-insight.git
                    {'\n'}
                    <span className="text-cyan-400">cd</span> prism-insight
                    {'\n\n'}
                    <span className="text-zinc-500"># 2. Quick setup (requires OpenAI API key)</span>
                    {'\n'}
                    <span className="text-cyan-400">./quickstart.sh</span> <span className="text-yellow-400">YOUR_OPENAI_API_KEY</span>
                    {'\n\n'}
                    <span className="text-zinc-500"># 3. Generate a PDF report (demo.py = report only)</span>
                    {'\n'}
                    <span className="text-emerald-400">python</span> demo.py <span className="text-yellow-400">AAPL</span>
                  </code>
                </pre>
              </CardContent>
            </Card>
          </div>

          <p className="text-center text-sm text-zinc-600 mt-6">
            Or try the{' '}
            <a
              href="https://github.com/dragon1086/prism-insight#option-b-docker-recommended-for-production"
              target="_blank"
              rel="noopener noreferrer"
              className="text-emerald-400 hover:text-emerald-300 underline underline-offset-4"
            >
              Docker installation
            </a>
            {' '}for containerized deployment
          </p>
        </div>
      </section>

      {/* Screenshots Gallery */}
      <section className="relative py-32 px-4 bg-zinc-900/30">
        <div className="max-w-6xl mx-auto">
          <div className="text-center mb-12">
            <Badge className="mb-4 bg-zinc-800 text-zinc-300 border-zinc-700">Screenshots</Badge>
            <h2 className="text-4xl font-bold mb-4">Beautiful, Informative Reports</h2>
            <p className="text-zinc-500">AI-generated analysis with actionable insights</p>
          </div>

          <div className="grid md:grid-cols-2 gap-6">
            {/* Dashboard screenshot */}
            <div className="rounded-xl bg-zinc-900 border border-zinc-800 overflow-hidden group hover:border-emerald-500/50 transition-colors">
              <div className="relative bg-zinc-950 p-2">
                <Image
                  src="/screenshots/dashboard_screenshot.png"
                  alt="PRISM-INSIGHT Dashboard"
                  width={800}
                  height={500}
                  className="w-full h-auto rounded-lg group-hover:scale-[1.02] transition-transform duration-500"
                />
              </div>
              <div className="p-4 border-t border-zinc-800">
                <div className="flex items-center gap-2">
                  <TrendingUp className="w-4 h-4 text-emerald-400" />
                  <span className="text-sm font-medium text-zinc-200">Dashboard View</span>
                </div>
                <p className="text-xs text-zinc-500 mt-1">Real-time portfolio tracking & performance metrics</p>
              </div>
            </div>

            {/* Report screenshot */}
            <div className="rounded-xl bg-zinc-900 border border-zinc-800 overflow-hidden group hover:border-cyan-500/50 transition-colors">
              <div className="relative bg-zinc-950 p-2">
                <Image
                  src="/screenshots/analysis_report_screenshot.png"
                  alt="AI Analysis Report"
                  width={800}
                  height={500}
                  className="w-full h-auto rounded-lg group-hover:scale-[1.02] transition-transform duration-500"
                />
              </div>
              <div className="p-4 border-t border-zinc-800">
                <div className="flex items-center gap-2">
                  <Terminal className="w-4 h-4 text-cyan-400" />
                  <span className="text-sm font-medium text-zinc-200">Analysis Report</span>
                </div>
                <p className="text-xs text-zinc-500 mt-1">Comprehensive PDF reports with AI insights</p>
              </div>
            </div>

            {/* Telegram screenshot */}
            <div className="rounded-xl bg-zinc-900 border border-zinc-800 overflow-hidden group hover:border-blue-500/50 transition-colors">
              <div className="relative bg-zinc-950 p-2">
                <Image
                  src="/screenshots/telegram_alert_screenshot.png"
                  alt="Telegram Trading Alerts"
                  width={800}
                  height={500}
                  className="w-full h-auto rounded-lg group-hover:scale-[1.02] transition-transform duration-500"
                />
              </div>
              <div className="p-4 border-t border-zinc-800">
                <div className="flex items-center gap-2">
                  <Bell className="w-4 h-4 text-blue-400" />
                  <span className="text-sm font-medium text-zinc-200">Telegram Alerts</span>
                </div>
                <p className="text-xs text-zinc-500 mt-1">Instant buy/sell signals delivered to your phone</p>
              </div>
            </div>

            {/* Trading screenshot */}
            <div className="rounded-xl bg-zinc-900 border border-zinc-800 overflow-hidden group hover:border-purple-500/50 transition-colors">
              <div className="relative bg-zinc-950 p-2">
                <Image
                  src="/screenshots/auto_trading_screenshot.jpeg"
                  alt="Auto Trading History"
                  width={800}
                  height={500}
                  className="w-full h-auto rounded-lg group-hover:scale-[1.02] transition-transform duration-500"
                />
              </div>
              <div className="p-4 border-t border-zinc-800">
                <div className="flex items-center gap-2">
                  <Zap className="w-4 h-4 text-purple-400" />
                  <span className="text-sm font-medium text-zinc-200">Auto Trading</span>
                </div>
                <p className="text-xs text-zinc-500 mt-1">Automated execution via KIS API</p>
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* Support Section */}
      <section className="relative py-32 px-4">
        <div className="max-w-2xl mx-auto text-center">
          <div className="inline-flex items-center justify-center w-20 h-20 rounded-3xl bg-gradient-to-br from-pink-500/20 to-red-500/20 mb-8">
            <Heart className="w-10 h-10 text-pink-400" />
          </div>

          <h2 className="text-4xl font-bold mb-6">Support the Project</h2>
          <p className="text-zinc-500 mb-8 leading-relaxed">
            PRISM-INSIGHT is free and open source. If it helps your trading,
            consider supporting the development.
          </p>

          <div className="flex flex-col sm:flex-row gap-4 justify-center">
            <Button
              size="lg"
              className="bg-pink-500 hover:bg-pink-400 text-white font-semibold px-8 h-12"
              asChild
            >
              <a href="https://github.com/sponsors/dragon1086" target="_blank" rel="noopener noreferrer">
                <Heart className="w-5 h-5 mr-2" />
                Become a Sponsor
              </a>
            </Button>

            <Button
              size="lg"
              variant="outline"
              className="border-zinc-700 hover:border-zinc-500 hover:bg-zinc-900 px-8 h-12"
              asChild
            >
              <a href="https://github.com/dragon1086/prism-insight" target="_blank" rel="noopener noreferrer">
                <Star className="w-5 h-5 mr-2" />
                Star on GitHub
              </a>
            </Button>
          </div>
        </div>
      </section>

      {/* Footer */}
      <footer className="border-t border-zinc-800 py-12 px-4">
        <div className="max-w-6xl mx-auto">
          <div className="flex flex-col md:flex-row items-center justify-between gap-6">
            <div className="flex items-center gap-3">
              <span className="text-xl font-bold">
                <span className="text-emerald-400">PRISM</span>
                <span className="text-zinc-600">-</span>
                <span className="text-zinc-400">INSIGHT</span>
              </span>
              <Badge variant="outline" className="text-zinc-500 border-zinc-700">
                AGPL-3.0
              </Badge>
            </div>

            <div className="flex items-center gap-6 text-sm text-zinc-500">
              <a
                href="https://github.com/dragon1086/prism-insight"
                className="hover:text-zinc-300 transition-colors flex items-center gap-2"
                target="_blank"
                rel="noopener noreferrer"
              >
                <Github className="w-4 h-4" />
                GitHub
              </a>
              <a
                href="https://analysis.stocksimulation.kr"
                className="hover:text-zinc-300 transition-colors flex items-center gap-2"
                target="_blank"
                rel="noopener noreferrer"
              >
                <ExternalLink className="w-4 h-4" />
                Live Dashboard
              </a>
            </div>
          </div>

          <div className="mt-8 pt-8 border-t border-zinc-800/50 text-center text-sm text-zinc-600">
            <p>Built with AI, for traders who trade smarter.</p>
          </div>
        </div>
      </footer>

      {/* Custom styles */}
      <style jsx global>{`
        @keyframes gradient {
          0%, 100% { background-position: 0% 50%; }
          50% { background-position: 100% 50%; }
        }

        @keyframes fade-in {
          from { opacity: 0; transform: translateY(10px); }
          to { opacity: 1; transform: translateY(0); }
        }

        .animate-gradient {
          animation: gradient 3s ease infinite;
        }

        .animate-fade-in {
          animation: fade-in 0.6s ease-out forwards;
        }
      `}</style>
    </div>
  )
}
