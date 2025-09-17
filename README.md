<div style="display: flex; justify-content: center; align-items: center; flex-direction: column;">
  <img src="docs/images/prism-insight-logo.jpeg" alt="PRISM-INSIGHT Logo" width="300" style="margin-bottom: 20px;">

  <div style="text-align: center;">
    <img src="https://img.shields.io/badge/License-MIT-yellow.svg" alt="License">
    <img src="https://img.shields.io/badge/python-3.10+-blue.svg" alt="Python">
    <img src="https://img.shields.io/badge/OpenAI-GPT--4.1-green.svg" alt="OpenAI">
    <img src="https://img.shields.io/badge/OpenAI-GPT--5-green.svg" alt="OpenAI">
    <img src="https://img.shields.io/badge/Anthropic-Claude--Sonnet--4-green.svg" alt="Anthropic">
  </div>
</div>

# 🔍 PRISM-INSIGHT

AI 기반 주식 분석 및 자동매매 통합 시스템
- **[공식 텔레그램 채널](https://t.me/stock_ai_agent)**: 급등주 포착/주식 분석 리포트 다운로드/매매 시뮬레이션 제공 (https://t.me/stock_ai_agent)
- **커뮤니티**: 아직 없음. 임시로 텔레그램 채널 토론방에서 대화 가능


## 📖 프로젝트 개요

PRISM-INSIGHT는 **AI 분석 에이전트를 활용한 종합 주식 분석**을 핵심으로 하는 **완전 오픈소스 무료 프로젝트**입니다. 텔레그램 채널을 통해 매일 급등주를 자동으로 포착하고, 전문가 수준의 애널리스트 리포트를 생성하여 매매 시뮬레이션을 수행하며, **선택적으로 한국투자증권 API를 통한 실제 자동매매 연동이 가능합니다.**

**✨ 모든 기능이 100% 무료로 제공됩니다!**

## 📈 '25.09.13 기준 매매 시뮬레이터 실적
- 최초 시작일 : 2025.03.15
- 총 거래 건수: 38건
- 수익 거래: 17건
- 손실 거래: 21건
- 승률: 44.74%
- **누적 수익률: 371.70%**
- **[매매 성과 요약 대시보드](https://claude.ai/public/artifacts/8958de2b-8a17-45e7-b46b-c95b1c7f4709)**

### 🎯 주요 기능

- **🤖 AI 종합 분석 (핵심)**: GPT-4.1 기반 다중 에이전트 시스템을 통한 전문가급 주식 분석
  [![분석 리포트 데모](https://img.youtube.com/vi/4WNtaaZug74/maxresdefault.jpg)](https://youtu.be/4WNtaaZug74)



- **📊 급등주 자동 포착**: 시간대별(오전/오후) 시장 트렌드 분석을 통한 관심종목 선별
  <img src="docs/images/trigger.png" alt="급등주 포착" width="500">


- **📱 텔레그램 자동 전송**: 분석 결과를 텔레그램 채널로 실시간 전송
  <img src="docs/images/summary.png" alt="요약 전송" width="500">


- **📈 매매 시뮬레이션**: GPT-5 기반 생성된 리포트를 활용한 투자 전략 시뮬레이션
  <img src="docs/images/simulation1.png" alt="시뮬레이션1" width="500">

  <img src="docs/images/simulation2.png" alt="시뮬레이션2" width="500">

  <img src="docs/images/dashboard.jpg" alt="시뮬레이션 실적" width="500">

- **💰 자동매매 연동**: 한국투자증권 REST API를 통한 실전/모의투자 자동매매 시스템
  - 실시간 주문 집행 및 체결 관리 (오전 주문은 시장가, 장마감 이후 주문은 다음 영업일 예약 주문)
  - 포트폴리오 현황 모니터링
  
  <img src="docs/images/portfolio.png" alt="실계좌 포트폴리오" width="500">

### 🧠 AI 모델 활용

- **핵심 분석**: OpenAI GPT-4.1 (종합 주식 분석 에이전트)
- **매매 시뮬레이션**: OpenAI GPT-5 (투자 전략 시뮬레이션)
- **텔레그램 대화**: Anthropic Claude (봇과의 상호작용)

### 💡 사용한 MCP Servers

- **[kospi_kosdaq](https://github.com/dragon1086/kospi-kosdaq-stock-server)**: 주식 보고서 작성 시 KRX(한국거래소) 주식 데이터 담당 MCP 서버
- **[firecrawl](https://github.com/mendableai/firecrawl-mcp-server)**: 주식 보고서 작성 시 웹크롤링 전문 MCP 서버
- **[perplexity](https://github.com/perplexityai/modelcontextprotocol/tree/main)**: 주식 보고서 작성 시 웹검색 전문 MCP 서버
- **[sqlite](https://github.com/modelcontextprotocol/servers-archived/tree/HEAD/src/sqlite)**: 매매 시뮬레이션 내역 내부 DB 저장 전문 MCP 서버

## 🏗️ 시스템 아키텍처

```
┌─────────────────┐    ┌──────────────────────┐    ┌─────────────────┐
│   급등주 포착      │ -> │ 🤖 AI 분석 에이전트     │ -> │   리포트 생성      │
│ (trigger_batch) │    │   (GPT-4.1 기반)      │    │   (PDF 변환)     │
└─────────────────┘    │  📊기술적/🏢기본적      │    └─────────────────┘
         │             │  📰뉴스/💡전략 분석     │             │
         v             └──────────────────────┘             v
┌─────────────────┐    ┌─────────────────┐       ┌─────────────────┐
│ 텔레그램 얼럿      │    │   종합 분석       │       │  매매 시뮬레이션    │
│   즉시 전송       │    │    (핵심)        │       │  (GPT-5 기반)    │
│                 │    │                 │       │   (트래킹)       │
└─────────────────┘    └─────────────────┘       └─────────────────┘
```

## 🚀 시작하기

### 사전 요구사항

- Python 3.10+
- OpenAI API 키 (GPT-4.1, GPT-5)
- Anthropic API 키 (Claude-Sonnet-4)
- 텔레그램 봇 토큰 및 채널 ID
- wkhtmltopdf (PDF 변환용)
- **자동매매 연동 시 (선택사항)**:
  - 한국투자증권 API 앱키 및 앱시크릿
  - 실전투자 또는 모의투자 계좌 정보
  - 참고 : [한국투자증권 api github](https://github.com/koreainvestment/open-trading-api)

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
cp ./trading/config/kis_devlp.yaml.example kis_devlp.yaml
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

프로젝트 실행을 위해 다음 4개 설정 파일을 반드시 구성해야 합니다:

- **`.env`**: 환경 변수 (API 키, 토큰 등)
- **`./examples/streamlit/config.py`**: 보고서 생성 웹 설정
- **`mcp_agent.config.yaml`**: MCP 에이전트 설정
- **`mcp_agent.secrets.yaml`**: MCP 에이전트 시크릿 정보
- **`./trading/config/kis_devlp.yaml`**: 한국투자증권 API 설정 (자동매매 사용 시)

## 📋 사용법

### 기본 실행

전체 파이프라인을 실행하여 급등주 분석부터 텔레그램 전송까지 자동화:

```bash
# 오전 + 오후 모두 실행
python stock_analysis_orchestrator.py --mode both

# 오전만 실행
python stock_analysis_orchestrator.py --mode morning

# 오후만 실행
python stock_analysis_orchestrator.py --mode afternoon
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

**5. 자동매매 실행 (한국투자증권 API)**
```bash
python -m trading.domestic_stock_trading
# 매수/매도 신호에 따른 자동 주문 집행
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
├── 📂 trading/                   # 💰 자동매매 시스템
│   ├── 📂 config/               # API 설정 파일
│   │   ├── kis_devlp.yaml      # 한국투자증권 API 설정
│   │   └── .token_key           # 인증 토큰
│   ├── 📂 samples/              # 매매 예제 코드
│   ├── domestic_stock_trading.py    # 국내 주식 자동매매
│   ├── kis_auth.py              # API 인증 모듈
│   └── portfolio_telegram_reporter.py # 포트폴리오 리포터
├── 📂 examples/streamlit/        # 웹 인터페이스
├── stock_analysis_orchestrator.py # 🎯 메인 오케스트레이터
├── trigger_batch.py             # 급등주 포착 배치
├── telegram_bot_agent.py        # 텔레그램 봇 (Claude 기반)
├── stock_tracking_agent.py      # 매매 시뮬레이션 (GPT-5)
├── stock_tracking_enhanced_agent.py # 향상된 매매 시뮬레이션
├── pdf_converter.py             # PDF 변환
├── requirements.txt             # 의존성 목록
├── .env.example                 # 환경 변수 예시
├── mcp_agent.config.yaml.example    # MCP 에이전트 설정 예시
└── mcp_agent.secrets.yaml.example   # MCP 에이전트 시크릿 예시
```

## 🤖 AI 에이전트 시스템 (핵심 기능)

PRISM-INSIGHT의 **핵심은 GPT-4.1 기반의 전문화된 AI 에이전트들을 통한 종합 주식 분석**입니다:

### 📊 주가 분석 에이전트
- **기술적 분석**: 주가 추세, 이동평균선, 지지/저항선 분석
- **거래량 분석**: 투자자별(기관/외국인/개인) 매매 패턴 분석

### 🏢 기업 정보 에이전트
- **기업 현황**: 재무지표, 밸류에이션, 투자의견 분석
- **기업 개요**: 사업구조, 경쟁력, 성장동력 분석

### 📰 뉴스 & 전략 에이전트
- **뉴스 분석**: 당일 주가 변동 원인 및 주요 이슈 분석
- **투자 전략**: 종합 분석을 바탕으로 한 투자자 유형별 전략 제시

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
5. **💡 투자 전략 및 의견** - 투자자 유형별 전략

## 🔧 커스터마이징

### 급등주 포착 기준 수정
`trigger_batch.py`에서 다음 조건들을 수정할 수 있습니다:
- 거래량 증가율 임계값
- 주가 상승률 기준
- 시가총액 필터링 조건

### AI 프롬프트 수정
`cores/agents/` 디렉토리의 각 에이전트 파일에서 분석 지침을 커스터마이징할 수 있습니다.

### 매매 전략 커스터마이징
- **`stock_tracking_agent.py`, `stock_tracking_enhanced_agent.py`**: 매매 시뮬레이션 로직 수정
- **`trading/domestic_stock_trading.py`**: 실제 자동매매 전략 및 주문 로직 커스터마이징

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

---

**⭐ 이 프로젝트가 도움이 되었다면 Star를 눌러주세요!**
