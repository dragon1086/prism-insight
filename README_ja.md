<div align="center">
  <img src="docs/images/prism-insight-logo.jpeg" alt="PRISM-INSIGHT ロゴ" width="300">
  <br><br>
  <img src="https://img.shields.io/badge/License-AGPL%20v3-blue.svg" alt="License">
  <img src="https://img.shields.io/badge/python-3.10+-blue.svg" alt="Python">
  <img src="https://img.shields.io/badge/OpenAI-GPT--5-green.svg" alt="OpenAI">
  <img src="https://img.shields.io/badge/Anthropic-Claude--Sonnet--4.5-green.svg" alt="Anthropic">
</div>

# PRISM-INSIGHT

[![GitHub Sponsors](https://img.shields.io/github/sponsors/dragon1086?style=for-the-badge&logo=github-sponsors&color=ff69b4&label=Sponsors)](https://github.com/sponsors/dragon1086)
[![Stars](https://img.shields.io/github/stars/dragon1086/prism-insight?style=for-the-badge)](https://github.com/dragon1086/prism-insight/stargazers)

> **AI駆動の株式市場分析・自動売買システム**
>
> 13以上の専門AIエージェントが連携し、急騰銘柄の検出、アナリストレベルのレポート作成、自動売買を実行します。

<p align="center">
  <a href="README.md">English</a> |
  <a href="README_ko.md">한국어</a> |
  <a href="README_ja.md">日本語</a> |
  <a href="README_zh.md">中文</a> |
  <a href="README_es.md">Español</a>
</p>

---

### 🏆 プラチナスポンサー

<div align="center">
<a href="https://wrks.ai/en">
  <img src="docs/images/wrks_ai_logo.png" alt="AI3 WrksAI" width="50">
</a>

**[AI3](https://www.ai3.kr/) | [WrksAI](https://wrks.ai/en)**

**WrksAI**（プロフェッショナル向けAIアシスタント）の開発元であるAI3が、<br>
投資家のためのAIアシスタント **PRISM-INSIGHT** を誇りを持ってスポンサーしています。
</div>

---

## ⚡ 今すぐ試す（インストール不要）

### 1. ライブダッシュボード
AIトレーディングのパフォーマンスをリアルタイムで確認できます：
👉 **[analysis.stocksimulation.kr](https://analysis.stocksimulation.kr/)**

### 2. Telegramチャンネル
毎日の急騰銘柄アラートとAI分析レポートを受け取れます：
- 🇺🇸 **[英語チャンネル](https://t.me/prism_insight_global_en)**
- 🇰🇷 **[韓国語チャンネル](https://t.me/stock_ai_agent)**
- 🇯🇵 **[日本語チャンネル](https://t.me/prism_insight_ja)**
- 🇨🇳 **[中国語チャンネル](https://t.me/prism_insight_zh)**
- 🇪🇸 **[スペイン語チャンネル](https://t.me/prism_insight_es)**

### 3. サンプルレポート
AIが生成したApple Inc.の分析レポートをご覧ください：

[![サンプルレポート - Apple Inc. 分析](https://img.youtube.com/vi/LVOAdVCh1QE/maxresdefault.jpg)](https://youtu.be/LVOAdVCh1QE)

---

## ⚡ 60秒で試す（米国株）

PRISM-INSIGHTを最も手軽に試す方法です。必要なのは **OpenAI APIキー** のみです。

```bash
# Clone and run the quickstart script
git clone https://github.com/dragon1086/prism-insight.git
cd prism-insight
./quickstart.sh YOUR_OPENAI_API_KEY
```

これでApple（AAPL）のAI分析レポートが生成されます。他の銘柄も試せます：
```bash
python3 demo.py MSFT              # Microsoft
python3 demo.py NVDA              # NVIDIA
python3 demo.py TSLA --language ko  # Tesla（韓国語レポート）
```

> 💡 **OpenAI APIキーの取得**は [OpenAI Platform](https://platform.openai.com/api-keys) から行えます
>
> 📰 **オプション**: ニュース分析のために [Perplexity APIキー](https://www.perplexity.ai/) を `mcp_agent.config.yaml` に追加できます

AIが生成したPDFレポートは `prism-us/pdf_reports/` に保存されます。

<details>
<summary>🐳 Docker を使用する場合（Python環境構築不要）</summary>

```bash
# 1. Set your OpenAI API key
export OPENAI_API_KEY=sk-your-key-here

# 2. Start container
docker-compose -f docker-compose.quickstart.yml up -d

# 3. Run analysis
docker exec -it prism-quickstart python3 demo.py NVDA
```

レポートは `./quickstart-output/` に保存されます。

</details>

---

## 🚀 フルインストール

### 前提条件
- Python 3.10+ または Docker
- OpenAI APIキー（[こちらから取得](https://platform.openai.com/api-keys)）

### オプションA: Pythonインストール

```bash
# 1. Clone & Install
git clone https://github.com/dragon1086/prism-insight.git
cd prism-insight
pip install -r requirements.txt

# 2. Install Playwright for PDF generation
python3 -m playwright install chromium

# 3. Install perplexity-ask MCP server
cd perplexity-ask && npm install && npm run build && cd ..

# 4. Setup config
cp mcp_agent.config.yaml.example mcp_agent.config.yaml
cp mcp_agent.secrets.yaml.example mcp_agent.secrets.yaml
# Edit mcp_agent.secrets.yaml with your OpenAI API key
# Edit mcp_agent.config.yaml with KRX credentials (Kakao account)

# 5. Run analysis (no Telegram required!)
python stock_analysis_orchestrator.py --mode morning --no-telegram
```

### オプションB: Docker（本番環境推奨）

```bash
# 1. Clone & Configure
git clone https://github.com/dragon1086/prism-insight.git
cd prism-insight
cp mcp_agent.config.yaml.example mcp_agent.config.yaml
cp mcp_agent.secrets.yaml.example mcp_agent.secrets.yaml
# Edit config files with your API keys

# 2. Build & Run
docker-compose up -d

# 3. Run analysis manually (optional)
docker exec prism-insight-container python3 stock_analysis_orchestrator.py --mode morning --no-telegram
```

📖 **詳細セットアップガイド**: [docs/SETUP.md](docs/SETUP.md)

---

## 📖 PRISM-INSIGHTとは？

PRISM-INSIGHTは、**韓国（KOSPI/KOSDAQ）** および **米国（NYSE/NASDAQ）** 市場に対応した、**完全オープンソース・無料** のAI駆動型株式分析システムです。

### コア機能
- **急騰銘柄検出** - 異常な出来高・価格変動を示す銘柄の自動検出
- **AI分析レポート** - 13の専門AIエージェントが生成するプロアナリストレベルのレポート
- **トレーディングシミュレーション** - AI駆動の売買判断とポートフォリオ管理
- **自動売買** - 韓国投資証券APIを通じた実取引の執行
- **Telegram連携** - リアルタイムアラートと多言語ブロードキャスト

### AIモデル
- **分析・売買**: OpenAI GPT-5
- **Telegramボット**: Anthropic Claude Sonnet 4.5
- **翻訳**: OpenAI GPT-5（EN、JA、ZH対応）

---

## 🤖 AIエージェントシステム

13以上の専門エージェントがチームで連携します：

| チーム | エージェント数 | 目的 |
|--------|---------------|------|
| **分析** | 6エージェント | テクニカル分析、財務分析、業界分析、ニュース分析、市場分析 |
| **戦略** | 1エージェント | 投資戦略の統合 |
| **コミュニケーション** | 3エージェント | 要約、品質評価、翻訳 |
| **トレーディング** | 3エージェント | 売買判断、ジャーナル |
| **コンサルテーション** | 2エージェント | Telegramを通じたユーザーインタラクション |

<details>
<summary>📊 エージェントワークフロー図を表示</summary>
<br>
<img src="docs/images/aiagent/agent_workflow2.png" alt="エージェントワークフロー" width="700">
</details>

📖 **エージェント詳細ドキュメント**: [docs/CLAUDE_AGENTS.md](docs/CLAUDE_AGENTS.md)

---

## ✨ 主要機能

| 機能 | 説明 |
|------|------|
| **🤖 AI分析** | GPT-5マルチエージェントシステムによるエキスパートレベルの株式分析 |
| **📊 急騰検出** | 朝・午後の市場トレンド分析による自動ウォッチリスト生成 |
| **📱 Telegram** | チャンネルへのリアルタイム分析配信 |
| **📈 トレーディングシミュレーション** | AI駆動の投資戦略シミュレーション |
| **💱 自動売買** | 韓国投資証券APIを通じた売買執行 |
| **🎨 ダッシュボード** | ポートフォリオ、取引履歴、パフォーマンスの透明な追跡 |
| **🧠 自己改善** | トレーディングジャーナルのフィードバックループ — 過去のトリガー勝率が将来の買い判断に自動的に反映されます（[詳細](docs/TRADING_JOURNAL.md#performance-tracker-피드백-루프-self-improving-trading)） |
| **🇺🇸 米国市場** | NYSE/NASDAQ分析の完全サポート |

<details>
<summary>🖼️ スクリーンショットを表示</summary>
<br>
<img src="docs/images/trigger-en.png" alt="急騰検出" width="500">
<img src="docs/images/summary-en.png" alt="サマリー" width="500">
<img src="docs/images/dashboard1-en.png" alt="ダッシュボード" width="500">
</details>

---

## 📈 トレーディングパフォーマンス

### シーズン2（進行中）
| 指標 | 値 |
|------|-----|
| 開始日 | 2025.09.29 |
| 総取引数 | 50 |
| 勝率 | 42.00% |
| **累積リターン** | **127.34%** |
| リアル口座リターン | +8.50% |

👉 **[ライブダッシュボード](https://analysis.stocksimulation.kr/)**

---

## 🇺🇸 米国株モジュール

米国市場にも同じAI駆動ワークフローを適用：

```bash
# Run US analysis
python prism-us/us_stock_analysis_orchestrator.py --mode morning --no-telegram

# With English reports
python prism-us/us_stock_analysis_orchestrator.py --mode morning --language en
```

**データソース**: yahoo-finance-mcp、sec-edgar-mcp（SEC提出書類、インサイダー取引）

---

## 📚 ドキュメント

| ドキュメント | 説明 |
|-------------|------|
| [docs/SETUP.md](docs/SETUP.md) | 完全なインストールガイド |
| [docs/CLAUDE_AGENTS.md](docs/CLAUDE_AGENTS.md) | AIエージェントシステムの詳細 |
| [docs/TRIGGER_BATCH_ALGORITHMS.md](docs/TRIGGER_BATCH_ALGORITHMS.md) | 急騰検出アルゴリズム |
| [docs/TRADING_JOURNAL.md](docs/TRADING_JOURNAL.md) | トレーディングメモリシステム |

---

## 🎨 フロントエンドサンプル

### ランディングページ
Next.jsとTailwind CSSで構築されたモダンでレスポンシブなランディングページです。

👉 **[ライブデモ](https://prism-insight-landing.vercel.app/)**

```bash
cd examples/landing
npm install
npm run dev
# Visit http://localhost:3000
```

**特徴**: マトリックスレインアニメーション、タイプライターエフェクト、GitHubスターカウンター、レスポンシブデザイン

### ダッシュボード
リアルタイムのポートフォリオ追跡とパフォーマンスダッシュボードです。

```bash
cd examples/dashboard
npm install
npm run dev
# Visit http://localhost:3000
```

**特徴**: ポートフォリオ概要、取引履歴、パフォーマンス指標、市場セレクター（韓国/米国）

📖 **ダッシュボードセットアップガイド**: [examples/dashboard/DASHBOARD_README.md](examples/dashboard/DASHBOARD_README.md)

---

## 💡 MCPサーバー

### 韓国市場
- **[kospi_kosdaq](https://github.com/dragon1086/kospi-kosdaq-stock-server)** - KRX株式データ
- **[firecrawl](https://github.com/mendableai/firecrawl-mcp-server)** - Webクローリング
- **[perplexity](https://github.com/perplexityai/modelcontextprotocol)** - Web検索
- **[sqlite](https://github.com/modelcontextprotocol/servers-archived)** - トレーディングシミュレーションDB

### 米国市場
- **[yahoo-finance-mcp](https://pypi.org/project/yahoo-finance-mcp/)** - OHLCV、財務データ
- **[sec-edgar-mcp](https://pypi.org/project/sec-edgar-mcp/)** - SEC提出書類、インサイダー取引

---

## 🤝 コントリビューション

1. プロジェクトをフォーク
2. フィーチャーブランチを作成（`git checkout -b feature/amazing-feature`）
3. 変更をコミット（`git commit -m 'Add amazing feature'`）
4. ブランチにプッシュ（`git push origin feature/amazing-feature`）
5. プルリクエストを作成

---

## 📄 ライセンス

**デュアルライセンス：**

### 個人・オープンソース利用
[![License: AGPL v3](https://img.shields.io/badge/License-AGPL%20v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)

個人利用、非商用プロジェクト、オープンソース開発の場合、AGPL-3.0の下で無料でご利用いただけます。

### 商用SaaS利用
SaaS企業の場合、別途商用ライセンスが必要です。

📧 **お問い合わせ**: dragon1086@naver.com
📄 **詳細**: [LICENSE-COMMERCIAL.md](LICENSE-COMMERCIAL.md)

---

## ⚠️ 免責事項

分析情報は参考用であり、投資助言ではありません。すべての投資判断およびそれに伴う損益は、投資家ご自身の責任となります。

---

## 💝 スポンサーシップ

### プロジェクトを支援する

月間運用コスト（約$310/月）：
- OpenAI API: 約$235/月
- Anthropic API: 約$11/月
- Firecrawl + Perplexity: 約$35/月
- サーバーインフラ: 約$30/月

現在450人以上のユーザーに無料で提供しています。

<div align="center">
  <a href="https://github.com/sponsors/dragon1086">
    <img src="https://img.shields.io/badge/GitHubでスポンサーする-❤️-ff69b4?style=for-the-badge&logo=github-sponsors" alt="GitHubでスポンサーする">
  </a>
</div>

### 個人スポンサー
<!-- sponsors -->
- [@jk5745](https://github.com/jk5745) 💙
<!-- sponsors -->

---

## ⭐ プロジェクトの成長

ローンチから **10週間で250以上のスター** を達成しました！

[![Star History Chart](https://api.star-history.com/svg?repos=dragon1086/prism-insight&type=Date)](https://star-history.com/#dragon1086/prism-insight&Date)

---

**⭐ このプロジェクトがお役に立ちましたら、ぜひスターをお願いします！**

📞 **お問い合わせ**: [GitHub Issues](https://github.com/dragon1086/prism-insight/issues) | [Telegram](https://t.me/stock_ai_agent) | [Discussions](https://github.com/dragon1086/prism-insight/discussions)
