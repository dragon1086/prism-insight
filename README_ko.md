<div align="center">
  <img src="docs/images/prism-insight-logo.jpeg" alt="PRISM-INSIGHT Logo" width="300">
  <br><br>
  <img src="https://img.shields.io/badge/License-AGPL%20v3-blue.svg" alt="License">
  <img src="https://img.shields.io/badge/python-3.10+-blue.svg" alt="Python">
  <img src="https://img.shields.io/badge/OpenAI-GPT--5-green.svg" alt="OpenAI">
  <img src="https://img.shields.io/badge/Anthropic-Claude--Sonnet--4.5-green.svg" alt="Anthropic">
</div>

# 🔍 PRISM-INSIGHT

[![GitHub Sponsors](https://img.shields.io/github/sponsors/dragon1086?style=for-the-badge&logo=github-sponsors&color=ff69b4&label=Sponsors)](https://github.com/sponsors/dragon1086)
[![Stars](https://img.shields.io/github/stars/dragon1086/prism-insight?style=for-the-badge)](https://github.com/dragon1086/prism-insight/stargazers)

<div align="center">

---

### 🏆 Platinum Sponsor

<a href="https://wrks.ai/ko">
  <img src="docs/images/wrks_ai_logo.png" alt="AI3 WrksAI" width="50">
</a>

**[AI3](https://www.ai3.kr/) | [WrksAI](https://wrks.ai/ko)**

직장인을 위한 AI 비서 **웍스AI**를 만드는 **AI3**가<br>
투자자를 위한 AI 비서 **PRISM-INSIGHT**를 후원합니다.

👉 [웍스AI 알아보기](https://wrks.ai/ko)

---

</div>

---

### 📊 AI 기반 한국 주식시장 분석 및 매매 시스템

* **[공식 텔레그램 채널](https://t.me/prism_insight_global_en)**
  급등주 포착 · 주식 분석 리포트 다운로드 · 매매 시뮬레이션 · 자동매매 리포트 제공

    * **➡️ 글로벌(영어) 채널:**
      [https://t.me/prism_insight_global_en](https://t.me/prism_insight_global_en)
    * **➡️ 한국 채널:**
      [https://t.me/stock_ai_agent](https://t.me/stock_ai_agent)

* **[공식 대시보드](https://analysis.stocksimulation.kr/)**: PRISM-INSIGHT 실전매매 & 시뮬레이션 실시간 성과 대시보드 (부가적으로 AI 보유 분석, 거래내역, 관심종목 제공)
* **커뮤니티**: [깃허브 디스커션](https://github.com/dragon1086/prism-insight/discussions) 또는 텔레그램 토론방 (@prism_insight_discuss)
---




## 📖 프로젝트 개요

PRISM-INSIGHT는 **한국 주식시장(코스피/코스닥) 전문** **AI 분석 에이전트를 활용한 종합 주식 분석**을 핵심으로 하는 **완전 오픈소스 무료 프로젝트**입니다. 텔레그램 채널을 통해 매일 한국 급등주를 자동으로 포착하고, 전문가 수준의 애널리스트 리포트를 생성하여 매매 시뮬레이션 및 자동매매를 수행합니다.

**✨ 모든 기능이 100% 무료로 제공됩니다!**

## 🇺🇸 미국 주식 모듈 (NEW)

PRISM-INSIGHT가 이제 **미국 주식시장(NYSE, NASDAQ)** 분석을 지원합니다. 한국 주식 버전과 동일한 AI 기반 워크플로우를 사용합니다.

### 주요 기능
- **동일한 AI 에이전트 아키텍처**: 13개 전문 에이전트를 통한 종합 미국 주식 분석
- **MCP 서버 통합**: yahoo-finance-mcp (OHLCV, 재무제표), sec-edgar-mcp (SEC 공시, 내부자 거래)
- **KIS 해외주식 API**: 한국투자증권 해외주식 API를 통한 자동매매
- **다국어 지원**: 영어, 한국어, 일본어, 중국어 리포트

### 디렉토리 구조
```
prism-us/
├── us_stock_analysis_orchestrator.py  # 메인 오케스트레이터
├── us_trigger_batch.py                # 급등주 포착
├── us_stock_tracking_agent.py         # 매매 시뮬레이션
├── us_telegram_summary_agent.py       # 텔레그램 요약
├── cores/
│   ├── us_data_client.py              # 통합 데이터 클라이언트 (yfinance + finnhub)
│   ├── us_surge_detector.py           # 급등주 탐지 모듈
│   ├── us_analysis.py                 # 핵심 분석 모듈
│   └── agents/                        # 미국 전용 에이전트
└── trading/config/                    # KIS 해외주식 설정
```

### 빠른 시작
```bash
# 미국 주식 분석 파이프라인 실행 (한국어 기본 - 국내주식과 동일)
python prism-us/us_stock_analysis_orchestrator.py --mode morning

# 영어 리포트 및 메시지로 실행
python prism-us/us_stock_analysis_orchestrator.py --mode morning --language en

# 텔레그램 없이 테스트
python prism-us/us_stock_analysis_orchestrator.py --mode morning --no-telegram
```

## 📈 '26.01.25 기준 매매 시뮬레이터 및 실제 계좌 실적
### ⭐ 시즌1 ('25.09.28 종료. 실계좌 매매 없음)
**시뮬레이터 실적**
- 최초 시작일 : 2025.03.15
- 총 거래 건수: 51건
- 수익 거래: 23건
- 손실 거래: 28건
- 승률: 45.1%
- **누적 수익률: 408.60%**
- **[매매 성과 요약 대시보드](https://claude.ai/public/artifacts/d546cc2e-9d2c-4787-8415-86930494e198)**

### ⭐⭐ 시즌2 (진행 중)
**시뮬레이터 실적**
- 최초 시작일 : 2025.09.29
- 총 거래 건수: 50건
- 수익 거래: 21건
- 손실 거래: 29건
- 승률: 42.00%
- **매도 종목 누적 수익률: 127.34%**
- **포트폴리오 실현 수익률: 12.73%** (10개 슬롯으로 분산 관리, 127.34% ÷ 10)
- 시장 수익률(시즌2 시작 대비): KOSPI +45.43%, KOSDAQ +17.39%
- **[매매 성과 요약 대시보드](https://analysis.stocksimulation.kr/)**

**실제계좌 실적**
- 최초 시작일 : 2025.09.29
- 시작 금액: ₩9,969,801
- 현재 총자산 (평가금액 + 예수금): ₩10,816,740
- **수익률: +8.50%**

## 🤖 AI 에이전트 시스템 아키텍쳐 (핵심 기능)

PRISM-INSIGHT는 **13개의 전문화된 AI 에이전트들이 협업하는 다중 에이전트 시스템**입니다. 각 에이전트는 특정 분석 영역에 특화되어 있으며, 서로 유기적으로 협력하여 전문가 수준의 종합 분석 및 매매를 이행합니다.

### 📊 분석 팀 (6개 에이전트) - GPT-5 기반

#### 1. 기술적 분석가 (Technical Analyst)
<img src="docs/images/aiagent/technical_analyst.jpeg" alt="Technical Analyst" width="300"/>

- **역할**: 주가 및 거래량 기술적 분석 전문가
- **분석 항목**:
  - 주가 추세, 이동평균선, 지지/저항선
  - 차트 패턴 및 기술적 지표 (RSI, MACD, 볼린저밴드)
  - 기술적 관점 제시

#### 2. 거래동향 분석가 (Trading Flow Analyst)
<img src="docs/images/aiagent/tranding_flow_analyst.jpeg" alt="Trading Flow Analyst" width="300"/>

- **역할**: 투자자별 거래 동향 분석 전문가
- **분석 항목**:
  - 기관/외국인/개인 투자자의 매매 패턴
  - 거래량 분석을 통한 투자 주체별 동향 파악

#### 3. 재무 분석가 (Financial Analyst)
<img src="docs/images/aiagent/financial_analyst.jpeg" alt="Financial Analyst" width="300"/>

- **역할**: 기업 재무 및 밸류에이션 분석 전문가
- **분석 항목**:
  - 재무제표 분석 (매출, 영업이익, 순이익)
  - PER, PBR, ROE 등 밸류에이션 평가
  - 목표주가 및 증권사 컨센서스

#### 4. 산업 분석가 (Industry Analyst)
<img src="docs/images/aiagent/industry_analyst.jpeg" alt="Industry Analyst" width="300"/>

- **역할**: 기업 사업구조 및 경쟁력 분석 전문가
- **분석 항목**:
  - 사업 포트폴리오 및 시장 점유율
  - 경쟁사 대비 강점/약점
  - 연구개발 투자 및 성장동력

#### 5. 정보 분석가 (Information Analyst)
<img src="docs/images/aiagent/information_analyst.jpeg" alt="Information Analyst" width="300"/>

- **역할**: 뉴스 및 이슈 트렌드 분석 전문가
- **분석 항목**:
  - 당일 주가 변동 원인 규명
  - 최신 뉴스 및 공시 분석
  - 업종 동향 및 정치/경제 이슈

#### 6. 시장 분석가 (Market Analyst)
<img src="docs/images/aiagent/market_analyst.jpeg" alt="Market Analyst" width="300"/>

- **역할**: 전체 시장 및 거시경제 분석 전문가
- **분석 항목**:
  - KOSPI/KOSDAQ 인덱스 분석
  - 거시경제 지표 (금리, 환율, 물가)
  - 글로벌 경제와 한국 시장의 상관관계

---

### 💡 전략 팀 (1개 에이전트) - GPT-5 기반

#### 7. 투자 전략가 (Investment Strategist)
<img src="docs/images/aiagent/investment_strategist.jpeg" alt="Investment Strategist" width="300"/>

- **역할**: 모든 분석 결과를 통합하여 최종 투자 전략 수립
- **제공 사항**:
  - 단기/중기/장기 투자자별 맞춤 전략
  - 리스크 레벨 및 매매 타이밍 제안
  - 포트폴리오 관점의 종합 의견

---

### 💬 커뮤니케이션 팀 (3개 에이전트) - GPT-5 기반

#### 8-1. 요약 전문가 (Summary Specialist)
<img src="docs/images/aiagent/summary_specialist.jpeg" alt="Summary Specialist" width="300"/>

- **역할**: 상세 보고서를 투자자를 위한 핵심 요약으로 변환
- **특징**:
  - 400자 내외의 간결한 텔레그램 메시지 생성
  - 핵심 정보와 투자 포인트 추출
  - 텔레그램 최적화 포맷팅

#### 8-2. 품질 검수자 (Quality Inspector)
<img src="docs/images/aiagent/quality_inspector.jpeg" alt="Quality Inspector" width="300"/>

- **역할**: 생성된 메시지의 품질 평가 및 개선 제안
- **특징**:
  - 정확성, 명확성, 포맷 준수 여부 검증
  - 할루시네이션 탐지 및 오류 지적
  - 요약 전문가와 협업하여 EXCELLENT 등급까지 반복 개선

#### 8-3. 번역 전문가 (Translation Specialist)
<img src="docs/images/aiagent/translator_specialist.png" alt="Translation Specialist" width="300"/>

- **역할**: 분석 리포트와 메시지를 다국어로 번역
- **특징**:
  - 다국어 브로드캐스팅 지원 (영어, 일본어, 중국어 등)
  - 전문 용어와 시장 맥락 보존
  - 언어별 텔레그램 채널에 병렬 전송 가능

---

### 📈 매매 시뮬레이션 팀 (3개 에이전트) - GPT-5 기반

#### 9-1. 매수 전문가 (Buy Specialist)
<img src="docs/images/aiagent/buy_specialist.jpeg" alt="Buy Specialist" width="300"/>

- **역할**: AI 리포트 기반 매수 의사결정 및 진입 관리
- **특징**:
  - 밸류에이션과 모멘텀 기반 매수 점수 평가 (1~10점)
  - 최대 10개 슬롯 포트폴리오 관리
  - 산업군 분산투자 및 리스크 관리
  - 동적 목표가/손절가 설정
  - 상세 매매 시나리오 작성

#### 9-2. 매도 전문가 (Sell Specialist)
<img src="docs/images/aiagent/sell_specialist.jpeg" alt="Sell Specialist" width="300"/>

- **역할**: 매매시나리오 기반 보유 종목 모니터링 및 매도 타이밍 결정
- **특징**:
  - 손절/익절 시나리오 실시간 모니터링
  - 기술적 추세 및 시장 환경 분석
  - 포트폴리오 최적화 조정 제안
  - 100% 매도 특성을 고려한 신중한 결정

#### 9-3. 매매일지 에이전트 (Trading Journal) - Optional

- **역할**: 완료된 매매를 복기 분석하고 장기기억으로 축적
- **특징**:
  - 매수/매도 맥락 비교 분석 및 교훈 추출
  - 계층적 기억 압축 (상세 → 요약 → 직관)
  - 과거 경험 기반 매수 점수 조정
  - 기본값 비활성화 (`.env`에 `ENABLE_TRADING_JOURNAL=true`로 활성화)
  - 📖 자세한 내용: [docs/TRADING_JOURNAL.md](docs/TRADING_JOURNAL.md)

---

### 💬 사용자 상담 팀 (2개 에이전트) - Claude Sonnet 4.5 기반

#### 10-1. 포트폴리오 상담가 (Portfolio Consultant)
<img src="docs/images/aiagent/portfolio_consultant.jpeg" alt="Portfolio Consultant" width="300"/>

- **역할**: 사용자 보유 종목 평가 및 맞춤형 투자 조언
- **특징**:
  - 사용자의 평균 매수가와 보유 기간 기반 분석
  - 최신 시장 데이터와 뉴스를 활용한 종합 평가
  - 사용자 요청 스타일(친근/전문가/직설적 등) 적응형 응답
  - 수익/손실 포지션별 맞춤 조언

#### 10-2. 대화 관리자 (Dialogue Manager)
<img src="docs/images/aiagent/dialogue_manager.jpeg" alt="Dialogue Manager" width="300"/>

- **역할**: 대화 맥락 유지 및 후속 질문 처리
- **특징**:
  - 이전 대화 컨텍스트 기억 및 참조
  - 추가 질문에 대한 일관된 답변
  - 필요시 최신 데이터 추가 조회
  - 자연스러운 대화 흐름 유지

---

## 🔄 에이전트 협업 워크플로우

  <img src="docs/images/aiagent/agent_workflow2.png" alt="시뮬레이션2" width="700">


## 🎯 주요 기능

- **🤖 AI 종합 분석 (핵심)**: GPT-5 기반 다중 에이전트 시스템을 통한 전문가급 주식 분석
  [![분석 리포트 데모](https://img.youtube.com/vi/4WNtaaZug74/maxresdefault.jpg)](https://youtu.be/4WNtaaZug74)



- **📊 급등주 자동 포착**: 시간대별(오전/오후) 시장 트렌드 분석을 통한 관심종목 선별
  <img src="docs/images/trigger.png" alt="급등주 포착" width="500">


- **📱 텔레그램 자동 전송**: 분석 결과를 텔레그램 채널로 실시간 전송
  <img src="docs/images/summary.png" alt="요약 전송" width="500">


- **📈 매매 시뮬레이션**: GPT-5 기반 생성된 리포트를 활용한 투자 전략 시뮬레이션
  <img src="docs/images/simulation1.png" alt="시뮬레이션1" width="500">

  <img src="docs/images/simulation2.png" alt="시뮬레이션2" width="500">

  <img src="docs/images/season1_dashboard.png" alt="시뮬레이션 실적" width="500">

- **💱 자동매매**: 한국투자증권 API를 통해 매매시뮬레이션 결과대로 자동매매

- **🎨 실시간 대시보드**: AI가 매매한 종목 포트폴리오, 시장대비 실적, AI의 매매 근거, 매매히스토리, 관심종목, 시스템 유지 비용에 대한 모든 정보를 투명하게 공개

  <img src="docs/images/dashboard1.png" alt="Dashboard 1" width="500">
  <img src="docs/images/dashboard2.png" alt="Dashboard 2" width="500">
  <img src="docs/images/dashboard3.png" alt="Dashboard 3" width="500">
  <img src="docs/images/dashboard4.png" alt="Dashboard 4" width="500">
  <img src="docs/images/dashboard5.png" alt="Dashboard 5" width="500">
  <img src="docs/images/dashboard6.png" alt="Dashboard 6" width="500">
  <img src="docs/images/dashboard7.png" alt="Dashboard 7" width="500">

## 🧠 AI 모델 활용

- **핵심 분석 및 매매**: OpenAI GPT-5 (종합 주식 분석 및 매매 시뮬레이션)
- **텔레그램 대화**: Anthropic Claude Sonnet 4.5 (봇과의 상호작용)
- **번역**: OpenAI GPT-5 (텔레그램 채널 다중 언어 송출)

## 💡 사용한 MCP Servers

### 한국 시장 (KR)
- **[kospi_kosdaq](https://github.com/dragon1086/kospi-kosdaq-stock-server)**: 주식 보고서 작성 시 KRX(한국거래소) 주식 데이터 담당 MCP 서버
- **[firecrawl](https://github.com/mendableai/firecrawl-mcp-server)**: 주식 보고서 작성 시 웹크롤링 전문 MCP 서버
- **[perplexity](https://github.com/perplexityai/modelcontextprotocol/tree/main)**: 주식 보고서 작성 시 웹검색 전문 MCP 서버
- **[sqlite](https://github.com/modelcontextprotocol/servers-archived/tree/HEAD/src/sqlite)**: 매매 시뮬레이션 내역 내부 DB 저장 전문 MCP 서버
- **[time](https://github.com/modelcontextprotocol/servers/tree/main/src/time)**: 현재 시간 불러오는 MCP 서버

### 미국 시장 (NEW)
- **[yahoo-finance-mcp](https://pypi.org/project/yahoo-finance-mcp/)**: OHLCV, 기업 정보, 재무제표, 기관 보유 현황 (PyPI, uvx 원격 실행)
- **[sec-edgar-mcp](https://pypi.org/project/sec-edgar-mcp/)**: SEC 공시, XBRL 재무제표, 내부자 거래 데이터 (PyPI, uvx 원격 실행)


## 🚀 시작하기

### 사전 요구사항

- Python 3.10+
- OpenAI API 키 (GPT-5, GPT-5)
- Anthropic API 키 (Claude-Sonnet-4.5)
- 텔레그램 봇 토큰 및 채널 ID
- Playwright (PDF 변환용)
- 한국투자증권 API 관련 앱키 및 시크릿키

### 설치

1. **저장소 클론**
```bash
git clone https://github.com/dragon1086/prism-insight.git
cd prism-insight
```

2. **의존성 설치**
```bash
pip install -r requirements.txt
```

3. **설정 파일 준비**
다음 예시 파일들을 복사하여 실제 설정 파일을 생성하세요:
```bash
cp .env.example .env
cp ./examples/streamlit/config.py.example ./examples/streamlit/config.py
cp mcp_agent.config.yaml.example mcp_agent.config.yaml
cp mcp_agent.secrets.yaml.example mcp_agent.secrets.yaml
```

4. **설정 파일 편집**
복사한 설정 파일들을 편집하여 필요한 API 키와 설정값들을 입력하세요.

**중요:** KRX 데이터 마켓플레이스 인증을 위해 카카오 계정 정보를 설정하세요:
```bash
# .env
KAKAO_ID=your_kakao_email@example.com
KAKAO_PW=your_kakao_password
```

```yaml
# mcp_agent.config.yaml
kospi_kosdaq:
  command: "python3"
  args: ["-m", "kospi_kosdaq_stock_server"]
  env:
    KAKAO_ID: "your_kakao_id"
    KAKAO_PW: "your_kakao_password"
```

> **💡 2단계 인증 사용자:** 카카오 2단계 인증이 설정되어 있으면 매 분석시마다 앱에서 확인이 필요합니다. 번거로우시다면 카카오앱 > 전체 설정 > 카카오계정 > 계정 보안 > 2단계 인증 '사용 안함'으로 변경하세요.

5. **Playwright 설치** (PDF 변환용)

시스템이 첫 실행 시 **자동으로 Playwright 브라우저를 설치**합니다. 수동 설치 방법:

```bash
# Playwright 패키지 설치 (requirements.txt에 포함됨)
pip install playwright

# Chromium 브라우저 다운로드
python3 -m playwright install chromium
```

**플랫폼별 설치 방법:**

```bash
# macOS
pip3 install playwright
python3 -m playwright install chromium

# Ubuntu/Debian
pip install playwright
python3 -m playwright install --with-deps chromium

# Rocky Linux 8 / CentOS / RHEL
pip3 install playwright
playwright install chromium

# --with-deps가 동작하지 않으면 수동으로 의존성 설치:
dnf install -y epel-release
dnf install -y nss nspr atk at-spi2-atk cups-libs libdrm \
    libxkbcommon libXcomposite libXdamage libXfixes \
    libXrandr mesa-libgbm alsa-lib pango cairo

# 또는 설치 스크립트 사용
cd utils
chmod +x setup_playwright.sh
./setup_playwright.sh
```

**📖 자세한 설치 가이드:** [utils/PLAYWRIGHT_SETUP_ko.md](utils/PLAYWRIGHT_SETUP_ko.md)

6. **perplexity-ask MCP 서버 설치**
```bash
cd perplexity-ask
npm install
```

7. **한글 폰트 설치** (Linux 환경)

Linux에서 차트 한글 표시를 위해 한글 폰트가 필요합니다.

```bash
# Rocky Linux 8 / CentOS / RHEL
sudo dnf install google-nanum-fonts

# Ubuntu 22.04+ / Debian
./cores/ubuntu_font_installer.py 실행

# 폰트 캐시 갱신
sudo fc-cache -fv
python3 -c "import matplotlib.font_manager as fm; fm.fontManager.rebuild()"

참고: macOS와 Windows는 기본 한글 폰트가 지원되어 별도 설치 불필요
```

8. **자동 실행 설정 (Crontab)**

시스템에서 자동으로 실행되도록 crontab을 설정합니다:

```bash
# 간편 설정 (권장)
chmod +x utils/setup_crontab_simple.sh
utils/setup_crontab_simple.sh

# 또는 고급 설정
chmod +x utils/setup_crontab.sh
utils/setup_crontab.sh
```

자세한 내용은 [CRONTAB_SETUP.md](utils/CRONTAB_SETUP.md)를 참조하세요.

### 필수 설정 파일

프로젝트 실행을 위해 다음 설정 파일을 구성해야 합니다:

#### 🔧 핵심 설정 (필수)
- **`mcp_agent.config.yaml`**: MCP 에이전트 설정
- **`mcp_agent.secrets.yaml`**: MCP 에이전트 시크릿 정보 (API 키 등)

#### 📱 텔레그램 설정 (선택)
- **`.env`**: 텔레그램 채널 ID, 봇 토큰 등 환경 변수
  - 텔레그램을 사용하지 않으려면 `--no-telegram` 옵션으로 실행
  - 텔레그램 없이도 모든 분석 기능 정상 동작

#### 🌐 웹 인터페이스 설정 (선택)
- **`./examples/streamlit/config.py`**: 보고서 생성 웹 설정

💡 **Tip**: `--no-telegram` 옵션을 사용하면 `.env` 파일 없이도 실행 가능합니다!

## 📋 사용법

### 기본 실행

전체 파이프라인을 실행하여 급등주 분석부터 텔레그램 전송까지 자동화:

```bash
# 오전 + 오후 모두 실행 (텔레그램 활성화)
python stock_analysis_orchestrator.py --mode both

# 오전만 실행
python stock_analysis_orchestrator.py --mode morning

# 오후만 실행
python stock_analysis_orchestrator.py --mode afternoon

# 텔레그램 없이 로컬 테스트 (텔레그램 설정 불필요)
python stock_analysis_orchestrator.py --mode morning --no-telegram

# 영문 리포트 생성 (기본값: 한국어)
python stock_analysis_orchestrator.py --mode morning --language en

# 다국어 채널 브로드캐스팅 (.env 설정 필요)
python stock_analysis_orchestrator.py --mode morning --broadcast-languages en,ja,zh
```

#### 💡 텔레그램 옵션 (`--no-telegram`)

텔레그램 설정 없이도 시스템을 실행할 수 있습니다:

**사용 시나리오:**
- 🧪 **로컬 개발/테스트**: 텔레그램 설정 없이 핵심 기능만 빠르게 테스트
- 🚀 **성능 최적화**: 메시지 생성 및 전송 과정을 스킵하여 실행
- 🔧 **디버깅**: 분석 및 보고서 생성 기능만 집중 검증

**실행 효과:**
- ✅ 급등주 포착 → 보고서 생성 → PDF 변환 → 트래킹 시스템 (모두 정상 동작)
- ❌ 텔레그램 알럿, 메시지 생성, 메시지 전송 (스킵)
- 💰 AI 요약 생성 비용 절감

**필수 환경변수 (텔레그램 사용 시):**
```bash
# .env 파일
TELEGRAM_CHANNEL_ID="-1001234567890"  # 메인 채널 (기본값: 한국어)
TELEGRAM_BOT_TOKEN="1234567890:ABCdefGHIjklMNOpqrsTUVwxyz"

# 다국어 브로드캐스팅 (선택사항)
# --broadcast-languages 인자와 함께 사용 (예: --broadcast-languages en,ja,zh)
# TELEGRAM_CHANNEL_ID_EN="-1001234567891"  # 영어 채널
# TELEGRAM_CHANNEL_ID_JA="-1001234567892"  # 일본어 채널
# TELEGRAM_CHANNEL_ID_ZH="-1001234567893"  # 중국어 채널
```

### 개별 모듈 실행

**1. 급등주 포착만 실행**
```bash
python trigger_batch.py morning INFO --output trigger_results.json
```

**2. 특정 종목 AI 분석 보고서 생성 (핵심 기능)**
```bash
python cores/main.py
# 또는 직접 analyze_stock 함수 사용
```

**3. PDF 변환**
```bash
python pdf_converter.py input.md output.pdf
```

**4. 텔레그램 메시지 생성 및 전송**
```bash
python telegram_summary_agent.py
python telegram_bot_agent.py
```

## 📁 프로젝트 구조

```
prism-insight/
├── 📂 cores/                     # 🤖 핵심 AI 분석 엔진 (한국 시장)
│   ├── 📂 agents/               # AI 에이전트 모듈
│   │   ├── company_info_agents.py        # 기업 정보 분석 에이전트
│   │   ├── news_strategy_agents.py       # 뉴스 및 투자 전략 에이전트
│   │   ├── stock_price_agents.py         # 주가 및 거래량 분석 에이전트
│   │   ├── telegram_quality_inspector.py # 품질 검수자 에이전트
│   │   ├── telegram_summary_agent.py     # 요약 전문가 에이전트
│   │   └── telegram_translator_agent.py  # 번역 전문가 에이전트
│   ├── analysis.py              # 종합 주식 분석 (핵심)
│   ├── main.py                  # 메인 분석 실행
│   ├── report_generation.py     # 보고서 생성
│   ├── stock_chart.py           # 차트 생성
│   └── utils.py                 # 유틸리티 함수
├── 📂 prism-us/                  # 🇺🇸 미국 주식 모듈 (NEW)
│   ├── us_stock_analysis_orchestrator.py  # 미국 메인 오케스트레이터
│   ├── us_trigger_batch.py                # 미국 급등주 포착
│   ├── us_stock_tracking_agent.py         # 미국 매매 시뮬레이션
│   ├── us_telegram_summary_agent.py       # 미국 텔레그램 요약
│   ├── check_market_day.py                # 미국 시장 휴일 체커
│   ├── 📂 cores/                          # 미국 핵심 분석
│   │   ├── us_data_client.py              # 통합 데이터 클라이언트 (yfinance + finnhub)
│   │   ├── us_surge_detector.py           # 급등주 탐지 모듈
│   │   ├── us_analysis.py                 # 핵심 분석 모듈
│   │   └── 📂 agents/                     # 미국 전용 에이전트
│   └── 📂 tracking/                       # 미국 데이터베이스 스키마
├── 📂 examples/streamlit/        # 웹 인터페이스
├── 📂 trading/                   # 💱 자동매매 시스템 (한국투자증권 API)
│   ├── kis_auth.py              # KIS API 인증 및 토큰 관리
│   ├── domestic_stock_trading.py # 국내주식 매매 핵심 모듈
│   ├── portfolio_telegram_reporter.py # 포트폴리오 텔레그램 리포터
│   ├── 📂 config/               # 설정 파일 디렉토리
│   │   ├── kis_devlp.yaml       # KIS API 설정 (앱키, 계좌번호 등)
│   │   └── kis_devlp.yaml.example # 설정 파일 예시
│   └── 📂 samples/              # API 샘플 코드
├── 📂 utils/                     # 유틸리티 스크립트
├── 📂 tests/                     # 테스트 코드
├── stock_analysis_orchestrator.py # 🎯 메인 오케스트레이터 (한국 시장)
├── telegram_config.py           # 텔레그램 설정 관리 클래스
├── trigger_batch.py             # 급등주 포착 배치
├── telegram_bot_agent.py        # 텔레그램 봇 (Claude 기반)
├── stock_tracking_agent.py      # 매매 시뮬레이션 (GPT-5)
├── stock_tracking_enhanced_agent.py # 향상된 매매 시뮬레이션
├── compress_trading_memory.py   # 매매 메모리 압축 및 정리
├── performance_tracker_batch.py # 일일 성과 추적
├── pdf_converter.py             # PDF 변환
├── requirements.txt             # 의존성 목록
├── .env.example                 # 환경 변수 예시
├── mcp_agent.config.yaml.example    # MCP 에이전트 설정 예시
├── mcp_agent.secrets.yaml.example   # MCP 에이전트 시크릿 예시
```

## 📈 분석 보고서 구성

AI 에이전트가 생성하는 종합 애널리스트 리포트는 다음 섹션들로 구성됩니다:

1. **📊 핵심 투자 포인트** - 요약 및 주요 포인트
2. **📈 기술적 분석**
   - 주가 및 거래량 분석
   - 투자자 거래 동향 분석
3. **🏢 기본적 분석**
   - 기업 현황 분석
   - 기업 개요 분석
4. **📰 뉴스 트렌드 분석** - 최근 주요 뉴스 및 이슈
5. **🌐 시장 분석** - KOSPI/KOSDAQ 지수 및 거시환경 분석
6. **💡 투자 전략 및 의견** - 투자자 유형별 전략

## 🔧 커스터마이징

### 급등주 포착 기준 수정
`trigger_batch.py`에서 다음 조건들을 수정할 수 있습니다:
- 거래량 증가율 임계값
- 주가 상승률 기준
- 시가총액 필터링 조건

> **📖 하이브리드 선별 알고리즘:** 트리거가 매수/매도 에이전트와 궁합이 좋은 종목을 선정하는 방식으로 개선되었습니다. 자세한 내용은 [docs/TRIGGER_BATCH_ALGORITHMS.md](docs/TRIGGER_BATCH_ALGORITHMS.md)를 참조하세요.

### AI 프롬프트 수정
`cores/agents/` 디렉토리의 각 에이전트 파일에서 분석 지침을 커스터마이징할 수 있습니다.

### 차트 스타일 변경
`cores/stock_chart.py`에서 차트 색상, 스타일, 지표를 수정할 수 있습니다.

## 🤝 기여하기

1. 프로젝트를 포크합니다
2. 기능 브랜치를 생성합니다 (`git checkout -b feature/멋진기능`)
3. 변경사항을 커밋합니다 (`git commit -m '멋진 기능 추가'`)
4. 브랜치에 푸시합니다 (`git push origin feature/멋진기능`)
5. Pull Request를 생성합니다

## 📄 라이센스

**PRISM-INSIGHT는 이중 라이선스(Dual License) 방식을 채택하고 있습니다:**

### 개인 및 오픈소스 사용
[![License: AGPL v3](https://img.shields.io/badge/License-AGPL%20v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)

이 프로젝트는 다음 사용에 대해 **GNU Affero General Public License v3.0 (AGPL-3.0)** 라이선스가 적용됩니다:
- 개인적 사용
- 비상업적 프로젝트
- 오픈소스 개발
- 학술 연구

AGPL-3.0 라이선스 요구사항:
- 수정한 소스코드를 공개해야 함
- 네트워크를 통한 서비스 사용도 "배포"로 간주하여 소스코드 공개 필요
- 파생 저작물도 동일한 라이선스 적용

📖 **[AGPL-3.0 전문 (한글 참고용)](LICENSE-ko.md)** | **[영문 원본](LICENSE)**

### 상업적 SaaS 사용
SaaS 기업 및 상업적 사용의 경우 별도 **상업 라이선스**가 필요합니다.

**상업 라이선스 혜택:**
- 소스코드 공개 의무 없음
- 수정 내용을 비공개로 유지 가능
- 독점 소프트웨어에 통합 가능
- 전용 기술 지원
- 우선 기능 개발

**가격:**
- 스타트업 (<50명): $500/월
- 중소기업 (50-500명): $2,000/월
- 대기업 (>500명): 맞춤 가격
- **독점 SaaS 라이선스:** 상호 유연한 비용 협의 (1년 약정)

📧 **상업 라이선스 문의:** dragon1086@naver.com  
📄 **[상업 라이선스 상세 (한글)](LICENSE-COMMERCIAL-ko.md)** | **[영문](LICENSE-COMMERCIAL.md)**


### 왜 듀얼 라이선스인가?

**커뮤니티를 위해:**
- 개인 학습 및 개발에 무료
- 오픈소스 생태계 기여
- AGPL-3.0으로 완전한 투명성

**지속 가능성을 위해:**
- 상업 라이선스 수익으로 지속적인 개발 자금 확보
- 서버 인프라 유지
- 새로운 기능 및 AI 모델 추가
- 고품질 지원 제공

**비즈니스를 위해:**
- 상업적 사용에 대한 법적 명확성
- 소스코드 공개 요구사항 없음
- 전문적인 지원 및 SLA
- 커스터마이징 기회

---

**라이선스 관련 질문이 있으신가요?**
- 📧 이메일: dragon1086@naver.com
- 💬 텔레그램: [@stock_ai_agent](https://t.me/stock_ai_agent)
- 🐛 GitHub Issues: [여기에 보고](https://github.com/dragon1086/prism-insight/issues)

## ⚠️ 면책 조항

본 시스템에서 제공하는 분석 정보는 투자 참고용이며, 투자 권유를 목적으로 하지 않습니다. 모든 투자 결정과 그에 따른 손익은 투자자 본인의 책임입니다.

## 📞 문의

프로젝트 관련 문의사항이나 버그 리포트는 [GitHub Issues](https://github.com/dragon1086/prism-insight/issues)를 통해 제출해 주세요.

## 💝 프로젝트 지속 가능성을 위한 후원

### 💙 개인 후원자 분들께

작은 응원도 큰 힘이 됩니다! 커피 한 잔 값으로 프로젝트를 응원해주세요.

<div align="center">
  <a href="https://github.com/sponsors/dragon1086">
    <img src="https://img.shields.io/badge/Sponsor_on_GitHub-❤️-ff69b4?style=for-the-badge&logo=github-sponsors" alt="Sponsor on GitHub">
  </a>
</div>

### 💰 투명한 운영

매월 약 ₩290,000의 API 비용과 서버 비용이 발생합니다 ('25.10월 기준) :
- OpenAI API (GPT-5, GPT-5): ~₩170,000/월
- Anthropic API (Claude Sonnet 4.5): ~₩30,000/월
- Firecrawl API (MCP Server): ~₩30,000/월
- Perplexity API (MCP Server): ~₩15,000/월
- 서버 및 인프라: ~₩45,000/월

현재 450명+이 무료로 사용하고 있습니다.

### ✨ 현재 후원 현황

정말 감사합니다! 여러분의 응원으로 프리즘 인사이트가 계속 운영됩니다.

#### 💙 개인 후원자 분들
<!-- sponsors -->
**핵심 서포터**
- [@tehryung-ray](https://github.com/tehryung-ray) 💙

**서포터**
- [@ddkerty](https://github.com/ddkerty) 💙
- [@jk5745](https://github.com/jk5745) 💙
<!-- sponsors -->

---

**중요:** 모든 기능은 후원 여부와 관계없이 무료로 제공됩니다.  
후원은 서비스 지속을 위한 응원일 뿐입니다.

---

## ⭐ 프로젝트 성장

'25.8월 중순 출시 이후 **단 10주 만에 250+ Stars**를 달성했습니다!

[![Star History Chart](https://api.star-history.com/svg?repos=dragon1086/prism-insight&type=Date)](https://star-history.com/#dragon1086/prism-insight&Date)

---

**⭐ 이 프로젝트가 도움이 되었다면 Star를 눌러주세요!**
