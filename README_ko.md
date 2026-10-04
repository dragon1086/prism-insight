<div align="center">
  <img src="docs/images/prism-insight-logo.jpeg" alt="PRISM-INSIGHT Logo" width="240">
  &nbsp;&nbsp;
  <a href="assets/characters/priso/README.md">
    <img src="assets/characters/priso/v1.0/priso_master_transparent.png" alt="PRISM 마스코트 프리소" width="240">
  </a>
  <br>
  <sub><strong>프리소(Priso)</strong> · PRISM 공식 마스코트</sub>
  <br><br>
  <img src="https://img.shields.io/badge/License-AGPL%20v3-blue.svg" alt="License">
  <img src="https://img.shields.io/badge/python-3.10+-blue.svg" alt="Python">
  <img src="https://img.shields.io/badge/OpenAI-GPT--6_%7C_GPT--5.6-green.svg" alt="OpenAI GPT-6 및 GPT-5.6">
  <img src="https://img.shields.io/badge/Anthropic-Claude_Sonnet_5.5_(optional)-green.svg" alt="Anthropic Claude Sonnet 5.5 (선택)">
  <img src="https://img.shields.io/badge/ChatGPT_Plus-Codex_OAuth-ff6b35.svg" alt="ChatGPT Plus">
</div>

[![CI](https://github.com/dragon1086/prism-insight/actions/workflows/ci.yml/badge.svg)](https://github.com/dragon1086/prism-insight/actions/workflows/ci.yml)
[![Codacy Badge](https://app.codacy.com/project/badge/Grade/2f8fd766b0634c068ff9da57ccda00c6)](https://app.codacy.com/gh/dragon1086/prism-insight/dashboard?utm_source=gh&utm_medium=referral&utm_content=&utm_campaign=Badge_grade)

# PRISM-INSIGHT

[![GitHub Sponsors](https://img.shields.io/github/sponsors/dragon1086?style=for-the-badge&logo=github-sponsors&color=ff69b4&label=Sponsors)](https://github.com/sponsors/dragon1086)
[![Stars](https://img.shields.io/github/stars/dragon1086/prism-insight?style=for-the-badge)](https://github.com/dragon1086/prism-insight/stargazers)

> **AI 기반 주식시장 분석 및 매매 시스템**
>
> 13개 이상의 전문 AI 에이전트가 협업하여 급등주를 포착하고, 애널리스트 수준의 리포트를 만들고, 매매까지 자동으로 실행합니다.

<p align="center">
  <a href="README.md">English</a> |
  <a href="README_ko.md">한국어</a> |
  <a href="README_ja.md">日本語</a> |
  <a href="README_zh.md">中文</a> |
  <a href="README_es.md">Español</a>
</p>

### 플래티넘 스폰서

<div align="center">
<a href="https://wrks.ai/ko">
  <img src="docs/images/wrks_ai_logo.png" alt="AI3 WrksAI" width="50">
</a>

**[AI3](https://www.ai3.kr/) | [WrksAI](https://wrks.ai/ko)**

직장인을 위한 AI 비서 **웍스AI**를 만드는 **AI3**가<br>
투자자를 위한 AI 비서 **PRISM-INSIGHT**를 후원합니다.
</div>

---

## 신기능: Stance — 그래서 요즘, 어떤 시스템 트레이딩 전략이 제일 잘나가는데?

<p align="center">
  <img src="docs/images/stance-ecosystem-ko.png" alt="한국과 미국 시스템 트레이딩 전략의 수익, 최대 하락, 평균 투자비중, 기록률을 비교하는 Stance 리더보드" width="100%">
</p>

**과거 실적? 안 받습니다.** Stance는 등록한 순간부터 새 기록을 시작합니다. 과거 수익률 업로드도, 소급 입력도 없습니다. 이후의 판단과 성과가 하나로 이어져 쌓이므로, 잘된 구간만 골라낸 홍보가 아니라 **전략의 실제 실력과 위험**을 볼 수 있습니다. 한국과 미국 순위를 나누고, 수익 옆에 최대 하락·평균 투자비중·기록률을 함께 보여줍니다.

- **요즘 잘나가는 전략 찾기** — 모든 전략을 같은 기준으로 비교
- **수익률 너머까지 보기** — 하락폭·실제 투자비중·빠진 기록까지 확인
- **기록을 믿을 근거** — 서버가 판단 시각과 당시 가격을 확인하고 이후 성과를 자동 계산
- **내 전략도 참가** — 코딩 에이전트가 전략 찾기부터 등록·연동·테스트까지

**[실시간 순위 보기](https://analysis.stocksimulation.kr/?tab=stance)** · **[내 전략 참가하기](https://analysis.stocksimulation.kr/?tab=stance)** · **[빠른 시작](stance/QUICKSTART_ko.md)**

<details>
<summary><strong>내 전략은 어떻게 참가하나요?</strong></summary>

<p align="center">
  <img src="docs/images/stance-integration-ko.png" alt="전략 프로젝트를 열고 코딩 에이전트에 지시문을 붙여넣은 뒤, 찾은 전략과 소개를 확인하고 승인하면 등록과 연동을 자동으로 마치는 과정" width="100%">
</p>

전략 프로젝트를 **Codex CLI·Cursor·Claude Code 같은 코딩 에이전트**로 연 뒤, Stance 대시보드에서 복사한 지시문을 채팅에 붙여넣으면 됩니다. 에이전트가 독립 전략과 한국·미국 포트폴리오를 찾아내고, 공개할 이름·소개·링크 중 필요한 것만 묻습니다. 등록 계획을 먼저 보여주며, 사용자가 승인한 뒤에만 키 보관·코드 수정·테스트까지 진행합니다.

등록 직후부터 <strong>‘기록 쌓는 중’</strong>에 나오고 첫 판단부터 성과가 공개됩니다. 주식 공식 순위는 **63거래일 동안 기록하고, 자산의 1% 이상을 넣었던 거래를 20번 마친 뒤** 시작됩니다. 연결한 날부터 새 기록이 쌓이며 과거 성과는 끼워 넣을 수 없습니다. 실계좌·잔고·증권사 키는 필요 없습니다.
</details>

---

## 신기능: ChatGPT Plus/Pro 구독으로 바로 사용

**API 키 없어도 됩니다.** PRISM-INSIGHT는 이제 ChatGPT Plus($20/월) 또는 Pro($200/월) 구독을 통해 **Codex OAuth 프록시** 방식으로 분석을 직접 실행할 수 있습니다.

```bash
# 최초 1회 로그인 (브라우저가 자동으로 열려 ChatGPT 인증 진행)
python -m cores.chatgpt_proxy.oauth_login

# 재인증이 필요할 때 (계정 변경, 토큰 만료 등)
python -m cores.chatgpt_proxy.oauth_login --force

# ChatGPT 구독으로 실행
PRISM_OPENAI_AUTH_MODE=chatgpt_oauth python stock_analysis_orchestrator.py --mode morning
```

> 토큰은 백그라운드에서 자동 갱신되므로, ChatGPT 계정을 바꾸거나 비밀번호를 변경한 경우에만 다시 로그인하면 됩니다.

API 요금 0원. 동일한 강력한 분석. 기존 구독으로 충분합니다.

---

## 모바일 앱

<div align="center">

**AI 주식 분석을 언제 어디서나**

<a href="https://play.google.com/store/apps/details?id=com.prisminsight.prism_mobile">
  <img src="https://img.shields.io/badge/Google_Play-다운로드-green?style=for-the-badge&logo=google-play" alt="Google Play">
</a>
<a href="https://apps.apple.com/us/app/prism-insight-stock-analysis/id6759331074">
  <img src="https://img.shields.io/badge/App_Store-다운로드-blue?style=for-the-badge&logo=apple" alt="App Store">
</a>

</div>

- **스마트 필터링** — 원하는 텔레그램 알림만 선별해서 받기
- **PDF 리포트** — 모바일 최적화 AI 분석 리포트

---

## PRISM-INSIGHT 홍보영상

[![PRISM-INSIGHT 소개영상](https://img.youtube.com/vi/zAywb1G0wRA/maxresdefault.jpg)](https://www.youtube.com/watch?v=zAywb1G0wRA)

---

## 바로 체험하기 (설치 없이)

### 1. 라이브 대시보드
AI 매매 성과를 실시간으로 확인하세요:
**[analysis.stocksimulation.kr](https://analysis.stocksimulation.kr/)**

### 2. 텔레그램 채널
매일 급등주 알림과 AI 분석 리포트를 받아보세요:
- **[영어 채널](https://t.me/prism_insight_global_en)**
- **[한국어 채널](https://t.me/stock_ai_agent)**
- **[일본어 채널](https://t.me/prism_insight_ja)**
- **[중국어 채널](https://t.me/prism_insight_zh)**
- **[스페인어 채널](https://t.me/prism_insight_es)**

### 3. 샘플 리포트
AI가 생성한 Apple Inc. 분석 리포트를 확인하세요:

[![샘플 리포트 - Apple Inc. 분석](https://img.youtube.com/vi/LVOAdVCh1QE/maxresdefault.jpg)](https://youtu.be/LVOAdVCh1QE)

---

## 60초 안에 체험하기 (미국 주식)

PRISM-INSIGHT를 가장 빠르게 체험하는 방법입니다. **OpenAI API 키**만 있으면 됩니다.

```bash
# 클론 후 퀵스타트 스크립트 실행
git clone https://github.com/dragon1086/prism-insight.git
cd prism-insight
./quickstart.sh YOUR_OPENAI_API_KEY
```

Apple(AAPL)의 AI 분석 리포트가 생성됩니다. 다른 종목도 분석해보세요:
```bash
python3 demo.py MSFT              # Microsoft
python3 demo.py NVDA              # NVIDIA
python3 demo.py TSLA --language ko  # Tesla (한국어 리포트)
```

> **OpenAI API 키 발급**: [OpenAI Platform](https://platform.openai.com/api-keys)
>
> **선택사항**: 뉴스 분석을 위해 [Perplexity API 키](https://www.perplexity.ai/)를 `mcp_agent.config.yaml`에 추가하세요
>
> **선택사항**: `ADANOS_API_KEY`를 추가하면 미국 주식 뉴스 분석에 구조화된 소셜 심리 정보가 더해집니다

AI가 생성한 PDF 리포트는 `prism-us/pdf_reports/`에 저장됩니다.

<details>
<summary>또는 Docker로 실행 (Python 설치 불필요)</summary>

```bash
# 1. OpenAI API 키 설정
export OPENAI_API_KEY=sk-your-key-here

# 2. 로컬 quickstart 이미지 빌드 및 시작
docker compose -f docker-compose.quickstart.yml up --build -d

# 3. 분석 실행
docker exec -it prism-quickstart python3 demo.py NVDA
```

첫 실행 시 이미지를 로컬에서 빌드하므로 몇 분 정도 걸릴 수 있습니다.

리포트는 `./quickstart-output/`에 저장됩니다.

</details>

---

## 전체 설치

### 사전 요구사항
- Python 3.10+ 또는 Docker
- OpenAI API 키 ([여기서 발급](https://platform.openai.com/api-keys)) 또는 ChatGPT Plus/Pro 구독

### 옵션 A: Python 설치

```bash
# 1. 클론 & 설치
git clone https://github.com/dragon1086/prism-insight.git
cd prism-insight
pip install -r requirements.txt

# 2. Playwright 설치 (PDF 생성용)
python3 -m playwright install chromium

# 3. MCP 서버(Firecrawl, Perplexity 등)는 mcp_agent.config.yaml에 적힌 대로
#    npx/uv가 필요할 때 실행 — 따로 설치할 필요 없음

# 4. 설정
cp mcp_agent.config.yaml.example mcp_agent.config.yaml
cp mcp_agent.secrets.yaml.example mcp_agent.secrets.yaml
cp trading/config/kis_devlp.yaml.example trading/config/kis_devlp.yaml
# mcp_agent.secrets.yaml에 OpenAI API 키 입력
# trading/config/kis_devlp.yaml에 한국투자증권(KIS) API 키 입력 (한국 시장 데이터)

# 5. 분석 실행 (텔레그램 설정 불필요!)
python stock_analysis_orchestrator.py --mode morning --no-telegram
```

### 옵션 B: Docker (프로덕션 권장)

```bash
# 위 4단계의 설정 파일을 준비한 뒤:
docker compose up -d
docker exec prism-insight-container python3 stock_analysis_orchestrator.py --mode morning --no-telegram
```

**전체 설치 가이드**: [docs/SETUP_ko.md](docs/SETUP_ko.md)

---

## PRISM-INSIGHT란?

PRISM-INSIGHT는 **한국 (코스피/코스닥)** 및 **미국 (NYSE/NASDAQ)** 시장을 위한 **완전 오픈소스, 무료** AI 주식 분석 시스템입니다.

### 핵심 기능
- **급등주 포착** — 비정상적인 거래량/가격 움직임을 보이는 종목 자동 탐지
- **AI 분석 리포트** — 전문 AI 에이전트가 생성하는 애널리스트급 리포트
- **매매 시뮬레이션** — 포트폴리오 관리와 함께 AI 기반 매수/매도 결정
- **자동매매** — 한국투자증권 API를 통한 실제 매매 실행
- **텔레그램 통합** — 실시간 알림 및 다국어 브로드캐스팅
- **거시경제 인텔리전스** — 시장 국면 판단, 섹터 로테이션 분석, 리스크 이벤트 모니터링

### AI 모델
코드에 정해진 기본 모델입니다. 모두 `.env`에서 바꿀 수 있습니다([.env.example](.env.example) 참고).

| 역할 | 기본 모델 |
|------|----------|
| 리포트 섹션·투자전략·요약·거시경제 분석 | OpenAI **GPT-6 Luna** (`REPORT_MODEL`) |
| 매수·매도 판단 | OpenAI **GPT-5.6 Sol**, GPT-6.1 Sol 또는 GPT-6 Astra로 변경 가능 (`PRISM_BUY_CODEX_MODEL`, `PRISM_SELL_CODEX_MODEL`) |
| 텔레그램 질의응답 | OpenAI **GPT-6.1 Sol** (`TELEGRAM_ANALYSIS_MODEL`) |
| 번역(영어·일본어·중국어·스페인어)·매매일지 | OpenAI **GPT-6 Luna** |
| 선택 기능: 온디맨드 인사이트 에이전트 | Anthropic **Claude Sonnet 5.5** (`INSIGHT_MODEL`) |

모든 기능은 OpenAI API 키 또는 ChatGPT Plus/Pro 구독(Codex OAuth)으로 실행됩니다.

---

## AI 에이전트 시스템

고정된 숫자보다 실행 경로에 따라 에이전트를 구분합니다:

| 팀 | 에이전트 | 역할 |
|---|---------|------|
| **거시경제** | KR / US | 규칙 기반 시장 국면 위에 주도 업종·리스크·이벤트 조사를 더함 |
| **종목 분석** | 시장별 6개 기본 섹션 | 기술적·수급·기업·산업·뉴스·시장 분석 |
| **전략·요약** | 실행 중 동적 생성 | 기본 섹션을 투자전략과 핵심 요약으로 통합 |
| **매매** | KR / US 매수·매도 | AI 시나리오와 점수·포트폴리오·재진입 관문을 결합 |
| **저널·메모리** | 회고·압축·원칙 | 청산 결과를 다음 판단의 근거로 제공 |
| **커뮤니케이션·상담** | 평가·최적화·번역·후속 질문 | 텔레그램 요약과 사용자 대화 |

<details>
<summary>에이전트 워크플로우 다이어그램 보기</summary>
<br>
<img src="docs/images/aiagent/agent_workflow2.png" alt="에이전트 워크플로우" width="700">
</details>

**상세 문서**: [파이프라인 아키텍처](docs/PIPELINE_ARCHITECTURE_ko.md) | [AI 에이전트 시스템](docs/CLAUDE_AGENTS_ko.md)

---

## 주요 기능

| 기능 | 설명 |
|-----|------|
| **AI 분석** | OpenAI GPT-6 / GPT-5.6 모델 기반 다중 에이전트 시스템을 통한 전문가급 주식 분석 |
| **급등주 포착** | 오전/오후 시장 트렌드 분석을 통한 자동 관심종목 선별 |
| **텔레그램** | 채널로 실시간 분석 배포 |
| **매매 시뮬레이션** | AI 기반 투자 전략 시뮬레이션 |
| **자동매매** | 한국투자증권 API를 통한 실행 |
| **대시보드** | 투명한 포트폴리오, 거래내역, 성과 추적 |
| **자기개선 매매** | 매매일지 피드백 루프 — 과거 트리거 승률이 이후 매수 판단에 자동 반영 ([상세](docs/TRADING_JOURNAL.md#performance-tracker-피드백-루프-self-improving-trading)) |
| **미국 시장** | NYSE/NASDAQ 분석 완벽 지원 |
| **거시경제 인텔리전스** | 시장 국면 판단 및 섹터 로테이션으로 더 스마트한 종목 선정 |
| **모바일 앱** | iOS & Android 앱, 스마트 필터링 및 PDF 리포트 |

<details>
<summary>대시보드 스크린샷 보기</summary>
<br>
<img src="docs/images/dashboard_portfolio.png" alt="포트폴리오 개요" width="700">
<br><br>
<img src="docs/images/dashboard_trades.png" alt="매매 시뮬레이터" width="700">
<br><br>
<img src="docs/images/dashboard_performance.png" alt="AI 매매 시나리오" width="700">
</details>

---

## 매매 실적 — 시즌 2

![PRISM-INSIGHT 시즌 2: 10슬롯 계좌 실현 수익률과 코스피·코스닥, S&P 500·나스닥 비교](docs/images/season2-performance-ko.png)

같은 청산 거래를 두 가지 방식으로 보여드립니다.

- **거래별 수익률 합계** — 청산한 거래 하나하나의 수익률을 단순히 더한 값입니다. 복리가 아니고 투자 비중도 반영하지 않습니다.
- **10슬롯 계좌 수익률** — 계좌를 10개의 같은 슬롯으로 나눈 모의 계좌의 실현 수익률입니다(1슬롯보다 적게 산 거래는 그 비중만큼 반영). 청산한 거래만 포함하며 복리가 아닙니다.

| | 한국 (시즌 2) | 미국 |
|---|---|---|
| 기간 | 2025-09-30 ~ 2026-10-02 | 2026-01-28 ~ 2026-10-02 |
| 청산 거래 | 211건 | 127건 |
| 승률 | 40.3% (85승) | 33.1% (42승) |
| 거래당 평균 수익률 | +1.68% | +0.65% |
| 거래별 수익률 합계 | +355.3% | +82.8% |
| **10슬롯 계좌 수익률** | **+35.2%** | **+8.3%** |
| 계좌 곡선의 최대 하락폭 | −9.3%p | −13.7%p |
| 같은 기간 지수 | 코스피 +103.5% (3,431 → 6,982)<br>코스닥 +5.3% (847 → 892) | S&P 500 +10.8% (6,969 → 7,723)<br>나스닥 +14.8% (23,685 → 27,191) |
| 최고 수익 청산 거래 | 삼성전기 +86.8%<br>SK하이닉스 +73.8%<br>SK스퀘어 +57.6% | 마이크론 +105.7%, 마이크론 +52.8%<br>IBM +27.0% |

**이 기간 계좌 수익률은 코스피에 뒤졌습니다. 이 점을 숨기지 않고 말씀드립니다.** 코스피는 두 배 가까이 올랐지만 코스닥은 약 5% 오르는 데 그쳤습니다. 대형 반도체주가 이끈 상승장이었습니다. PRISM이 2026년 10월 직접 청산 기록을 되짚어 본 결과, 가장 큰 원인은 이것이었습니다. 진입 후 60거래일 안에 30% 이상 오른 한국 종목에서 실현 수익의 중앙값은 +2%였지만, 최고 상승폭의 중앙값은 +60%였습니다. 주도주를 너무 일찍 팔고 있었던 것입니다. 다음 절의 변경은 바로 이 문제를 겨냥하며, 효과를 확인하실 수 있도록 두 지표를 계속 공개하겠습니다.

> 출처: 라이브 대시보드 데이터([한국](https://analysis.stocksimulation.kr/dashboard_data.json), [미국](https://analysis.stocksimulation.kr/us_dashboard_data.json)), 2026-10-02(한국)·2026-10-03 KST(미국) 생성. 지수 등락률은 대시보드 곡선의 첫 지점(한국 2025-09-29, 미국 2026-01-29) 기준입니다. 보유 중인 종목은 제외했습니다. 모의 매매 결과이며 투자 권유가 아닙니다.

**[라이브 대시보드](https://analysis.stocksimulation.kr/)**

---

## PRISM은 지금 이렇게 매매합니다 (2026년 10월)

![PRISM 매매 흐름: 스크리닝, AI 분석, 매수 판단, 작은 첫 매수, 시나리오 증액, 주도주 보유, 재진입, 주간 점검](docs/images/how-prism-trades-ko.png)

**투자 방향.** PRISM은 오닐식 추세추종을 따릅니다. 대부분의 거래는 작게 하고 빨리 정리하며, 계좌는 크게 가는 소수 종목으로 계단식으로 키우는 것을 목표로 합니다. 거래를 많이 할수록 그런 종목을 찾을 확률은 높아지지만, 손절이 반복되면 계좌가 녹습니다. 그래서 핵심은 **좋은 종목을 고르고 사는 눈의 정확도**입니다.

| 단계 | 내용 |
|------|------|
| **1. 스크리닝** | 오전·오후 트리거가 가격과 거래량이 크게 움직이는 종목을 고릅니다. 트리거마다 PRISM의 최근 180일 기록(후보가 +20%에 도달한 비율, 실현 손익 평균)으로 품질 가중치(0.7~1.3)를 매기며, 약한 트리거는 최종 선발 자리를 더 이상 보장받지 못합니다. |
| **2. AI 분석** | 전문 에이전트들이 기술적 분석·수급·재무·산업·뉴스·시장 리포트를 쓰고, 이어서 투자전략을 정리합니다. |
| **3. 매수 판단** | 매수 에이전트가 정해진 채점표에 따라 1~10점을 매깁니다. 펀더멘털(수익성·재무 건전성·성장성·사업 명확성), 모멘텀 신호, 추세 점검을 봅니다. 진입하려면 현재 시장 국면의 최소 점수, 손익비 기준을 넘고, 손절폭이 국면별 한도(−5%~−7%)보다 넓지 않아야 합니다. |
| **4. 작은 첫 매수** | 계좌를 10개의 같은 슬롯으로 나눕니다. 새 종목은 변동성에 따라 1슬롯의 30~80%로 시작하며, 가장 강한 트리거에서 나온 고득점 셋업은 한 단계 크게 시작합니다. |
| **5. 시나리오 증액** | 매수할 때 AI가 증액 시나리오 2~4개(예: 돌파, 눌림 후 회복)를 쓰고 매일 갱신합니다. 코드는 조건이 맞을 때만, 평균 매수가보다 위일 때만, 직전 매수분을 넘지 않게, 처음 정한 위험 한도 안에서, 최대 1슬롯까지 더 삽니다. 확인된 강세(그날 첫 증액 뒤 최초 진입가 대비 +8% 이상, 거래량 평소의 1.5배 이상)라면 같은 세션에 한 번 더 증액할 수 있습니다. |
| **6. 주도주 보유** | 매수 후 4~15거래일 안에 종가가 최초 매수가보다 20% 이상 오르고, 50일 평균선에서 지나치게 멀어지지 않은 종목을 주도주로 봅니다. 최대 40거래일 동안 50일선 아래로 마감하거나 최초 매수가 아래로 내려갈 때만 팝니다. 1~3거래일 만에 20% 급등한 종목은 기존 수익 보호선을 그대로 씁니다. |
| **7. 재진입** | 손절했거나 가격 위치 때문에 매수를 보류한 종목을 최대 60거래일 동안 지켜봅니다. 장 마감 직전(한국 14:00, 미국 13:50) 기준 가격을 되찾으면 AI 재점검이 승인해야 매수합니다. 감시 기간마다 최대 3번, 시장당 하루 최대 2건입니다. |
| **8. 점검 루프** | 주간 주도주 리포트가 큰 수익 종목 포착, 놓친 대박, 손절 비용, 트리거별 성적을 추적합니다. 2주 점검(2026년 10월 18일)에서 10월 변경 하나하나를 같은 기준으로 평가합니다. |

이 변경 대부분은 2026년 10월 2일~4일에 실제 운영에 들어갔고, 10월 6일이 모든 변경이 적용되는 첫 거래일입니다. 그래서 위의 시즌 2 수치는 대부분 이 변경 이전의 결과입니다. 설계 문서: [투자 방향](docs/TRADING_CHANGE_REVIEW_HARNESS.md) · [트리거 우선순위](docs/TRIGGER_QUALITY_PRIORITY_ko.md) · [작은 첫 매수](docs/micro-split/B3_LIVE_ko.md) · [시나리오 증액](docs/micro-split/ADD_SCENARIOS_DESIGN_ko.md) · [주도주 보유](docs/RUNNER_HOLD_RULE_ko.md) · [재진입](docs/REENTRY_V3_LIVE_ko.md) · [주간 리포트](docs/WEEKLY_RUNNER_REPORT_ko.md) · [2주 점검](docs/TWO_WEEK_REVIEW_ko.md)

---

## 매매 시스템은 어떻게 실패에서 배웠나

한국 시장의 매매 기록에는 서로 반대되는 두 문제가 나타났습니다. 처음에는
진입을 지나치게 피했고, 이후에는 시장과 주문 상태를 충분히 통제하지 못한
채 위험을 감수했습니다. v1.16.7부터 v2.18까지의 개선은 단순한 프롬프트
교정을 넘어 시장 국면·청산 상태·재진입을 결정론적으로 통제하는 방향으로
진화했습니다.

![관망 편향에서 상태 기반 리스크 통제로 발전한 PRISM-INSIGHT 매매 시스템](docs/images/trading-evolution-ko.png)

> 수치는 시스템 변화를 진단하기 위한 값입니다. 누적 수익은 거래별 수익률의
> 합계이며, 미진입 후보의 성과는 사후 관찰값입니다. 시간가중 포트폴리오
> 수익률이나 실제로 실현 가능한 백테스트 수익률을 뜻하지 않습니다.

### 2026년 10월: 검증하고, 채택하고, 버린 것

PRISM은 규칙을 바꾸기 전에 자신의 과거 후보와 거래로 그 규칙을 다시 돌려 보고, 결론을 교훈 장부에 남깁니다. 같은 질문을 두 번 검증하지 않기 위해서입니다.

**버린 것** (현행 규칙보다 낫지 않았습니다):
- **포켓피봇·거래량 확인 트리거** (2018~2026): 두 시장 모두 거래량 조건이 아무 효과가 없었습니다.
- **하락한 종목이 50일선과 200일선을 동시에 회복할 때 매수**: 우위가 없었고, 한국에서는 오히려 나빴습니다.
- **오닐 8주 규칙을 그대로 적용해 50일선까지 보유**: 결과가 나빠졌습니다(거래별 수익률 합계 기준 한국 −43%p, 미국 약 −40%p). 1~3거래일 만에 20% 급등한 종목은 멀리 있는 50일선을 기다리다 이익을 거의 다 반납했습니다.
- **손절 점검을 60분 단위로, 변동성(ATR) 기준 손절, 올린 손절선을 종가에만 집행**: 모두 현행 손절보다 나빴습니다.

**채택한 것**:
- **주도주 보유**는 4~15거래일 안에 +20%에 도달하고 50일선에서 지나치게 멀어지지 않은 종목에만 적용합니다(한국에서 바뀐 7건 기준 +57%p. 표본이 작고 같은 데이터로 고른 결과이므로 주간 리포트로 계속 추적합니다).
- 고정 +2%·+4% 사다리 대신 **작은 첫 매수와 AI가 쓰는 증액 시나리오**를 쓰고, 확인된 강세에는 더 빠르게 증액합니다.
- **재진입은 감시 기간마다 최대 3번**까지 시도합니다. 예전의 1회 제한은 수익이 났던 재진입을 잘라냈습니다(한국 7건 중 4건, 미국 43건 중 13건).
- **PRISM 자체 기록에 따른 트리거 우선순위**를 쓰고, 거래량 급증 트리거는 주가가 오르는 경우에만 잡도록 고쳤습니다.

전체 교훈 장부: [docs/RESEARCH_LESSONS_ko.md](docs/RESEARCH_LESSONS_ko.md)

---

## 미국 주식 모듈

미국 시장을 위한 동일한 AI 기반 워크플로우입니다:

```bash
# 미국 주식 분석 실행
python prism-us/us_stock_analysis_orchestrator.py --mode morning --no-telegram

# 영어 리포트로 실행
python prism-us/us_stock_analysis_orchestrator.py --mode morning --language en
```

**데이터 소스**: yahoo-finance-mcp, sec-edgar-mcp (SEC 공시, 내부자 거래)

---

## 문서

| 문서 | 설명 |
|-----|------|
| [docs/SETUP_ko.md](docs/SETUP_ko.md) | 완전한 설치 가이드 |
| [docs/CLAUDE_AGENTS_ko.md](docs/CLAUDE_AGENTS_ko.md) | AI 에이전트 시스템 상세 |
| [docs/PIPELINE_ARCHITECTURE_ko.md](docs/PIPELINE_ARCHITECTURE_ko.md) | 스크리닝 → 분석 → 매매 → 피드백 설계 |
| [docs/TRIGGER_BATCH_ALGORITHMS.md](docs/TRIGGER_BATCH_ALGORITHMS.md) | 급등주 포착 알고리즘 |
| [docs/TRADING_JOURNAL.md](docs/TRADING_JOURNAL.md) | 매매 메모리 시스템 |
| [docs/TRADING_CHANGE_REVIEW_HARNESS.md](docs/TRADING_CHANGE_REVIEW_HARNESS.md) | 투자 방향과 매매 변경 검토 절차 |
| [docs/RESEARCH_LESSONS_ko.md](docs/RESEARCH_LESSONS_ko.md) | 연구 교훈 장부: 검증하고, 채택하고, 버린 것 |
| [docs/TRIGGER_QUALITY_PRIORITY_ko.md](docs/TRIGGER_QUALITY_PRIORITY_ko.md) | PRISM 자체 기록에 따른 트리거 우선순위 |
| [docs/micro-split/B3_LIVE_ko.md](docs/micro-split/B3_LIVE_ko.md) | 작은 첫 매수와 실제 비중 늘리기 |
| [docs/micro-split/ADD_SCENARIOS_DESIGN_ko.md](docs/micro-split/ADD_SCENARIOS_DESIGN_ko.md) | AI 증액 시나리오와 빠른 증액 |
| [docs/RUNNER_HOLD_RULE_ko.md](docs/RUNNER_HOLD_RULE_ko.md) | 주도주 보유 규칙 |
| [docs/REENTRY_V3_LIVE_ko.md](docs/REENTRY_V3_LIVE_ko.md) | 재진입 규칙 |
| [docs/WEEKLY_RUNNER_REPORT_ko.md](docs/WEEKLY_RUNNER_REPORT_ko.md) | 주간 주도주 리포트 |
| [docs/TWO_WEEK_REVIEW_ko.md](docs/TWO_WEEK_REVIEW_ko.md) | 10월 변경의 2주 점검 |

---

## 프론트엔드 예제

### 대시보드
실시간 포트폴리오 추적 및 성과 대시보드입니다.

**[라이브 데모](https://analysis.stocksimulation.kr/)**

```bash
cd examples/dashboard
npm install
npm run dev
# http://localhost:3000 접속
```

**기능**: 포트폴리오 개요, 매매 내역, 성과 지표, 마켓 선택기 (한국/미국), KOSPI/KOSDAQ 대비 수익률 비교

**대시보드 설정 가이드**: [examples/dashboard/DASHBOARD_README.md](examples/dashboard/DASHBOARD_README.md)

---

## MCP 서버

### 한국 시장
- **kospi_kosdaq** — 한국투자증권(KIS) API 기반 내장 한국 시장 데이터 서버 (`cores/market_data`)
- **[firecrawl](https://github.com/mendableai/firecrawl-mcp-server)** — 웹 크롤링
- **[perplexity](https://github.com/perplexityai/modelcontextprotocol)** — 웹 검색
- **[sqlite](https://github.com/modelcontextprotocol/servers-archived)** — 매매 시뮬레이션 DB

### 미국 시장
- **[yahoo-finance-mcp](https://pypi.org/project/yahoo-finance-mcp/)** — OHLCV, 재무제표
- **[sec-edgar-mcp](https://pypi.org/project/sec-edgar-mcp/)** — SEC 공시, 내부자 거래

---

## 기여하기

1. 프로젝트를 포크합니다
2. 기능 브랜치를 생성합니다 (`git checkout -b feature/amazing-feature`)
3. 변경사항을 커밋합니다 (`git commit -m 'Add amazing feature'`)
4. 브랜치에 푸시합니다 (`git push origin feature/amazing-feature`)
5. Pull Request를 생성합니다

### 기여자와 후원자

**코드 기여자** — PRISM-INSIGHT를 함께 만들어 주신 모든 분께 감사드립니다.

[@dragon1086](https://github.com/dragon1086) · [@rocky-mun](https://github.com/rocky-mun) · [@tkgo11](https://github.com/tkgo11) · [@alexander-schneider](https://github.com/alexander-schneider) · [@bonggu-kang](https://github.com/bonggu-kang) · [@willagio](https://github.com/willagio) · [@lifrary](https://github.com/lifrary) · [@cjinzy](https://github.com/cjinzy) · [@don9x2E](https://github.com/don9x2E) · [@jk5745](https://github.com/jk5745) · [@sungwoowi](https://github.com/sungwoowi)

**Gold Supporter** — [@tkgo11](https://github.com/tkgo11)

프로젝트를 후원해 주셔서 감사합니다.

---

## 라이선스

**이중 라이선스:**

### 개인 및 오픈소스 사용
[![License: AGPL v3](https://img.shields.io/badge/License-AGPL%20v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)

개인 사용, 비상업적 프로젝트, 오픈소스 개발에 AGPL-3.0으로 무료 사용 가능합니다.

### 상업적 SaaS 사용
SaaS 기업은 별도의 상업 라이선스가 필요합니다.

**연락처**: dragon1086@naver.com
**상세 조건**: [COMMERCIAL-LICENSE-ko.md](COMMERCIAL-LICENSE-ko.md)

제3자 오픈소스 구성요소에는 각 구성요소의 라이선스가 적용됩니다. 고지,
소스 코드 안내 및 라이선스 원문은
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)에서 확인할 수 있습니다.

---

## 면책 조항

분석 정보는 참고용이며 투자 권유가 아닙니다. 모든 투자 결정과 그에 따른 손익은 투자자 본인의 책임입니다.

---

## 후원

### 프로젝트 지원

월간 운영 비용 (2026년 1월 기준, 약 $313/월):
- OpenAI API: 약 $234/월
- Anthropic API: 약 $11/월
- Firecrawl + Perplexity: 약 $36/월
- 서버 인프라: 약 $32/월

현재 450명 이상이 무료로 사용하고 있습니다.

<div align="center">
  <a href="https://github.com/sponsors/dragon1086">
    <img src="https://img.shields.io/badge/Sponsor_on_GitHub-❤️-ff69b4?style=for-the-badge&logo=github-sponsors" alt="GitHub에서 후원하기">
  </a>
</div>

---

## 프로젝트 성장

[![Star History Chart](https://api.star-history.com/svg?repos=dragon1086/prism-insight&type=Date)](https://star-history.com/#dragon1086/prism-insight&Date)

---

**이 프로젝트가 도움이 되었다면 Star를 눌러주세요!**

**문의**: [GitHub Issues](https://github.com/dragon1086/prism-insight/issues) | [텔레그램](https://t.me/stock_ai_agent) | [디스커션](https://github.com/dragon1086/prism-insight/discussions)
