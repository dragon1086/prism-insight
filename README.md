<div style="display: flex; justify-content: center; align-items: center; flex-direction: column;">
  <img src="docs/images/prism-insight-logo.jpeg" alt="PRISM-INSIGHT Logo" width="300" style="margin-bottom: 20px;">

  <div style="text-align: center;">
    <img src="https://img.shields.io/badge/License-MIT-yellow.svg" alt="License">
    <img src="https://img.shields.io/badge/python-3.10+-blue.svg" alt="Python">
    <img src="https://img.shields.io/badge/OpenAI-GPT--4.1-green.svg" alt="OpenAI">
    <img src="https://img.shields.io/badge/OpenAI-GPT--5-green.svg" alt="OpenAI">
    <img src="https://img.shields.io/badge/Anthropic-Claude--Sonnet--4.5-green.svg" alt="Anthropic">
  </div>
</div>

# 🔍 PRISM-INSIGHT

[![GitHub Sponsors](https://img.shields.io/github/sponsors/dragon1086?style=for-the-badge&logo=github-sponsors&color=ff69b4&label=Sponsors)](https://github.com/sponsors/dragon1086)
[![Stars](https://img.shields.io/github/stars/dragon1086/prism-insight?style=for-the-badge)](https://github.com/dragon1086/prism-insight/stargazers)

AI 기반 주식 분석 및 매매 시스템
- **[공식 텔레그램 채널](https://t.me/stock_ai_agent)**: 급등주 포착/주식 분석 리포트 다운로드/매매 시뮬레이션/자동매매 리포트 제공 (https://t.me/stock_ai_agent)
- **[공식 대시보드](https://analysis.stocksimulation.kr/)**: PRISM-INSIGHT 실전매매 & 시뮬레이션 실시간 성과 대시보드 (부가적으로 AI 보유 분석, 거래내역, 관심종목 제공)
- **커뮤니티**: 아직 없음. 임시로 텔레그램 채널 토론방에서 대화 가능


## 📖 프로젝트 개요

PRISM-INSIGHT는 **AI 분석 에이전트를 활용한 종합 주식 분석**을 핵심으로 하는 **완전 오픈소스 무료 프로젝트**입니다. 텔레그램 채널을 통해 매일 급등주를 자동으로 포착하고, 전문가 수준의 애널리스트 리포트를 생성하여 매매 시뮬레이션 및 자동매매를 수행합니다.

**✨ 모든 기능이 100% 무료로 제공됩니다!**

## 📈 '25.10.29 기준 매매 시뮬레이터 및 실제 계좌 실적
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
- 총 거래 건수: 3건
- 수익 거래: 2건
- 손실 거래: 1건
- 승률: 66.67%
- **누적 수익률: 19.47%**
- **[매매 성과 요약 대시보드](https://analysis.stocksimulation.kr/)**

**실제계좌 실적**
- 최초 시작일 : 2025.09.29
- 아직 실적 없음

## 🤖 AI 에이전트 시스템 아키텍쳐 (핵심 기능)

PRISM-INSIGHT는 **12개의 전문화된 AI 에이전트들이 협업하는 다중 에이전트 시스템**입니다. 각 에이전트는 특정 분석 영역에 특화되어 있으며, 서로 유기적으로 협력하여 전문가 수준의 종합 분석 및 매매를 이행합니다.

### 📊 분석 팀 (6개 에이전트) - GPT-4.1 기반

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

### 💡 전략 팀 (1개 에이전트) - GPT-4.1 기반

#### 7. 투자 전략가 (Investment Strategist)
<img src="docs/images/aiagent/investment_strategist.jpeg" alt="Investment Strategist" width="300"/>

- **역할**: 모든 분석 결과를 통합하여 최종 투자 전략 수립
- **제공 사항**:
  - 단기/중기/장기 투자자별 맞춤 전략
  - 리스크 레벨 및 매매 타이밍 제안
  - 포트폴리오 관점의 종합 의견

---

### 💬 커뮤니케이션 팀 (2개 에이전트) - GPT-4.1

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

---

### 📈 매매 시뮬레이션 팀 (2개 에이전트) - GPT-5 기반

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

  <img src="docs/images/aiagent/agent_workflow.png" alt="시뮬레이션2" width="500">


## 🎯 주요 기능

- **🤖 AI 종합 분석 (핵심)**: GPT-4.1 기반 다중 에이전트 시스템을 통한 전문가급 주식 분석
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

- **🎨 시각화**: 주가, 거래량, 시가총액 등 다양한 차트 생성

## 🧠 AI 모델 활용

- **핵심 분석**: OpenAI GPT-4.1 (종합 주식 분석 에이전트)
- **매매 시뮬레이션**: OpenAI GPT-5 (투자 전략 시뮬레이션)
- **텔레그램 대화**: Anthropic Claude Sonnet 4.5 (봇과의 상호작용)

## 💡 사용한 MCP Servers

- **[kospi_kosdaq](https://github.com/dragon1086/kospi-kosdaq-stock-server)**: 주식 보고서 작성 시 KRX(한국거래소) 주식 데이터 담당 MCP 서버
- **[firecrawl](https://github.com/mendableai/firecrawl-mcp-server)**: 주식 보고서 작성 시 웹크롤링 전문 MCP 서버
- **[perplexity](https://github.com/perplexityai/modelcontextprotocol/tree/main)**: 주식 보고서 작성 시 웹검색 전문 MCP 서버
- **[sqlite](https://github.com/modelcontextprotocol/servers-archived/tree/HEAD/src/sqlite)**: 매매 시뮬레이션 내역 내부 DB 저장 전문 MCP 서버
- **[time](https://github.com/modelcontextprotocol/servers/tree/main/src/time)**: 현재 시간 불러오는 MCP 서버


## 🚀 시작하기

### 사전 요구사항

- Python 3.10+
- OpenAI API 키 (GPT-4.1, GPT-5)
- Anthropic API 키 (Claude-Sonnet-4.5)
- 텔레그램 봇 토큰 및 채널 ID
- wkhtmltopdf (PDF 변환용)
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

5. **wkhtmltopdf 설치** (PDF 변환용)
```bash
# macOS
brew install wkhtmltopdf

# Ubuntu/Debian
sudo apt-get install wkhtmltopdf

# CentOS/RHEL
sudo yum install wkhtmltopdf
```

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
TELEGRAM_CHANNEL_ID="-1001234567890"
TELEGRAM_BOT_TOKEN="1234567890:ABCdefGHIjklMNOpqrsTUVwxyz"
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
├── 📂 cores/                     # 🤖 핵심 AI 분석 엔진
│   ├── 📂 agents/               # AI 에이전트 모듈
│   │   ├── company_info_agents.py    # 기업 정보 분석 에이전트
│   │   ├── news_strategy_agents.py   # 뉴스 및 투자 전략 에이전트
│   │   └── stock_price_agents.py     # 주가 및 거래량 분석 에이전트
│   ├── analysis.py              # 종합 주식 분석 (핵심)
│   ├── main.py                  # 메인 분석 실행
│   ├── report_generation.py     # 보고서 생성
│   ├── stock_chart.py           # 차트 생성
│   └── utils.py                 # 유틸리티 함수
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
├── stock_analysis_orchestrator.py # 🎯 메인 오케스트레이터
├── telegram_config.py           # 텔레그램 설정 관리 클래스
├── trigger_batch.py             # 급등주 포착 배치
├── telegram_bot_agent.py        # 텔레그램 봇 (Claude 기반)
├── stock_tracking_agent.py      # 매매 시뮬레이션 (GPT-5)
├── stock_tracking_enhanced_agent.py # 향상된 매매 시뮬레이션
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

이 프로젝트는 MIT 라이센스 하에 배포됩니다. 자세한 내용은 `LICENSE` 파일을 참조하세요.

## ⚠️ 면책 조항

본 시스템에서 제공하는 분석 정보는 투자 참고용이며, 투자 권유를 목적으로 하지 않습니다. 모든 투자 결정과 그에 따른 손익은 투자자 본인의 책임입니다.

## 📞 문의

프로젝트 관련 문의사항이나 버그 리포트는 [GitHub Issues](https://github.com/dragon1086/prism-insight/issues)를 통해 제출해 주세요.

## 💝 프로젝트 지속 가능성을 위한 후원

### 🥇 Gold Sponsor를 찾습니다 (단 1곳, 모집 중)

**월 $500로 PRISM-INSIGHT의 영구적 운영을 도와주실 파트너를 모십니다.**

✨ **Gold Sponsor 전용 혜택:**
- 🏆 **독점 노출**: GitHub README 최상단 + [대시보드](https://analysis.stocksimulation.kr/) 메인에 로고 단독 배치
- 📊 **사용자 도달**: 450+ 텔레그램 구독자 + 매일 증가하는 GitHub 방문자에게 지속적 브랜드 노출
- 💎 **명예**: 완전 오픈소스 AI 주식 분석 시스템의 유일한 공식 후원 파트너
- 🛡️ **안정성 보장**: 육아로 시간이 부족한 개발자가 시스템 유지보수에 전념할 수 있는 환경 제공
- 🤝 **직접 소통**: 프로젝트 로드맵 및 우선순위에 대한 의견 제시 기회

---
**월 $500 상세**

1. AI 운영비 충당: GPT-5 등 최고급 AI 모델 API 비용 전액
2. 안정화 보상금: 핵심 시스템의 긴급 유지보수 및 안정화 작업에 대한 보상
---

📧 **Gold Sponsor 파트너십 문의**:
- **이메일**: dragon1086@naver.com
- **GitHub Issues**: [Partnership Inquiry](https://github.com/dragon1086/prism-insight/issues/new?labels=sponsorship&template=partnership.md)
- **텔레그램**: @stock_ai_ko
- 또는 [GitHub Sponsors](https://github.com/sponsors/dragon1086)에서 $500 티어 선택

> 💡 **기업 맞춤 제안이 필요하신가요?** 이메일로 먼저 상담해 드립니다.

---

### 💙 개인 후원자 분들께

작은 응원도 큰 힘이 됩니다! 커피 한 잔 값으로 프로젝트를 응원해주세요.

<div align="center">
  <a href="https://github.com/sponsors/dragon1086">
    <img src="https://img.shields.io/badge/Sponsor_on_GitHub-❤️-ff69b4?style=for-the-badge&logo=github-sponsors" alt="Sponsor on GitHub">
  </a>
</div>

### 💰 투명한 운영

매월 약 ₩260,000의 API 비용과 서버 비용이 발생합니다 ('25.10월 기준) :
- OpenAI API (GPT-4.1, GPT-5): ~₩140,000/월
- Anthropic API (Claude Sonnet 4.5): ~₩30,000/월
- Firecrawl API (MCP Server): ~₩30,000/월
- Perplexity API (MCP Server): ~₩15,000/월
- 서버 및 인프라: ~₩45,000/월

현재 450명이 무료로 사용하고 있습니다.

### ✨ 현재 후원 현황

정말 감사합니다! 여러분의 응원으로 프리즘 인사이트가 계속 운영됩니다.

#### 🥇 Gold Sponsor
<!-- gold-sponsor -->
**자리가 비어있습니다!** 첫 번째 파트너가 되어주세요.
<!-- gold-sponsor -->

#### 💙 개인 후원자 분들
<!-- sponsors -->
아직 후원자가 없습니다. 첫 번째 후원자가 되어주세요! 💙
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
