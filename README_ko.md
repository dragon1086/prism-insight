<div align="center">
  <img src="docs/images/prism-insight-logo.jpeg" alt="PRISM-INSIGHT Logo" width="300">
  <br><br>
  <img src="https://img.shields.io/badge/License-AGPL%20v3-blue.svg" alt="License">
  <img src="https://img.shields.io/badge/python-3.10+-blue.svg" alt="Python">
  <img src="https://img.shields.io/badge/OpenAI-GPT--5-green.svg" alt="OpenAI">
  <img src="https://img.shields.io/badge/Anthropic-Claude--Sonnet--4.5-green.svg" alt="Anthropic">
</div>

# PRISM-INSIGHT

[![GitHub Sponsors](https://img.shields.io/github/sponsors/dragon1086?style=for-the-badge&logo=github-sponsors&color=ff69b4&label=Sponsors)](https://github.com/sponsors/dragon1086)
[![Stars](https://img.shields.io/github/stars/dragon1086/prism-insight?style=for-the-badge)](https://github.com/dragon1086/prism-insight/stargazers)

> **AI 기반 주식시장 분석 및 매매 시스템**
>
> 13개 이상의 전문화된 AI 에이전트가 협업하여 급등주를 포착하고, 애널리스트급 리포트를 생성하며, 자동으로 매매를 실행합니다.

📖 [English Documentation](README.md)

---

### 🏆 플래티넘 스폰서

<div align="center">
<a href="https://wrks.ai/ko">
  <img src="docs/images/wrks_ai_logo.png" alt="AI3 WrksAI" width="50">
</a>

**[AI3](https://www.ai3.kr/) | [WrksAI](https://wrks.ai/ko)**

직장인을 위한 AI 비서 **웍스AI**를 만드는 **AI3**가<br>
투자자를 위한 AI 비서 **PRISM-INSIGHT**를 후원합니다.
</div>

---

## ⚡ 바로 체험하기 (설치 없이)

### 1. 라이브 대시보드
AI 매매 성과를 실시간으로 확인하세요:
👉 **[analysis.stocksimulation.kr](https://analysis.stocksimulation.kr/)**

### 2. 텔레그램 채널
매일 급등주 알림과 AI 분석 리포트를 받아보세요:
- 🇰🇷 **[한국 채널](https://t.me/stock_ai_agent)**
- 🇺🇸 **[영어 채널](https://t.me/prism_insight_global_en)**

### 3. 샘플 리포트
AI가 생성한 Apple Inc. 분석 리포트를 확인하세요:

[![샘플 리포트 - Apple Inc. 분석](https://img.youtube.com/vi/LVOAdVCh1QE/maxresdefault.jpg)](https://youtu.be/LVOAdVCh1QE)

---

## 🚀 빠른 시작

### 사전 요구사항
- Python 3.10+ 또는 Docker
- OpenAI API 키 ([여기서 발급](https://platform.openai.com/api-keys))

### 옵션 A: Python 설치

```bash
# 1. 클론 & 설치
git clone https://github.com/dragon1086/prism-insight.git
cd prism-insight
pip install -r requirements.txt

# 2. Playwright 설치 (PDF 생성용)
python3 -m playwright install chromium

# 3. perplexity-ask MCP 서버 설치
cd perplexity-ask && npm install && npm run build && cd ..

# 4. 설정
cp mcp_agent.config.yaml.example mcp_agent.config.yaml
cp mcp_agent.secrets.yaml.example mcp_agent.secrets.yaml
# mcp_agent.secrets.yaml에 OpenAI API 키 입력
# mcp_agent.config.yaml에 KRX 인증 정보 입력 (카카오 계정)

# 5. 분석 실행 (텔레그램 설정 불필요!)
python stock_analysis_orchestrator.py --mode morning --no-telegram
```

### 옵션 B: Docker (프로덕션 권장)

```bash
# 1. 클론 & 설정
git clone https://github.com/dragon1086/prism-insight.git
cd prism-insight
cp mcp_agent.config.yaml.example mcp_agent.config.yaml
cp mcp_agent.secrets.yaml.example mcp_agent.secrets.yaml
# 설정 파일에 API 키 입력

# 2. 빌드 & 실행
docker-compose up -d

# 3. 수동 분석 실행 (선택)
docker exec prism-insight-container python3 stock_analysis_orchestrator.py --mode morning --no-telegram
```

📖 **전체 설치 가이드**: [docs/SETUP_ko.md](docs/SETUP_ko.md)

---

## 📖 PRISM-INSIGHT란?

PRISM-INSIGHT는 **한국 (코스피/코스닥)** 및 **미국 (NYSE/NASDAQ)** 시장을 위한 **완전 오픈소스, 무료** AI 주식 분석 시스템입니다.

### 핵심 기능
- **급등주 포착** - 비정상적인 거래량/가격 움직임을 보이는 종목 자동 탐지
- **AI 분석 리포트** - 13개 전문 AI 에이전트가 생성하는 전문가급 리포트
- **매매 시뮬레이션** - 포트폴리오 관리와 함께 AI 기반 매수/매도 결정
- **자동매매** - 한국투자증권 API를 통한 실제 매매 실행
- **텔레그램 통합** - 실시간 알림 및 다국어 브로드캐스팅

### AI 모델
- **분석 및 매매**: OpenAI GPT-5
- **텔레그램 봇**: Anthropic Claude Sonnet 4.5
- **번역**: OpenAI GPT-5 (영어, 일본어, 중국어 지원)

---

## 🤖 AI 에이전트 시스템

13개 이상의 전문 에이전트가 팀으로 협업합니다:

| 팀 | 에이전트 | 역할 |
|---|---------|------|
| **분석 팀** | 6개 | 기술적, 재무, 산업, 뉴스, 시장 분석 |
| **전략 팀** | 1개 | 투자 전략 수립 |
| **커뮤니케이션 팀** | 3개 | 요약, 품질 평가, 번역 |
| **매매 팀** | 3개 | 매수/매도 결정, 매매 저널 |
| **상담 팀** | 2개 | 텔레그램을 통한 사용자 상호작용 |

<details>
<summary>📊 에이전트 워크플로우 다이어그램 보기</summary>
<br>
<img src="docs/images/aiagent/agent_workflow2.png" alt="에이전트 워크플로우" width="700">
</details>

📖 **상세 에이전트 문서**: [docs/CLAUDE_AGENTS_ko.md](docs/CLAUDE_AGENTS_ko.md)

---

## ✨ 주요 기능

| 기능 | 설명 |
|-----|------|
| **🤖 AI 분석** | GPT-5 다중 에이전트 시스템을 통한 전문가급 주식 분석 |
| **📊 급등주 포착** | 오전/오후 시장 트렌드 분석을 통한 자동 관심종목 선별 |
| **📱 텔레그램** | 채널로 실시간 분석 배포 |
| **📈 매매 시뮬레이션** | AI 기반 투자 전략 시뮬레이션 |
| **💱 자동매매** | 한국투자증권 API를 통한 실행 |
| **🎨 대시보드** | 투명한 포트폴리오, 거래내역, 성과 추적 |
| **🇺🇸 미국 시장** | NYSE/NASDAQ 분석 완벽 지원 |

<details>
<summary>🖼️ 스크린샷 보기</summary>
<br>
<img src="docs/images/trigger.png" alt="급등주 포착" width="500">
<img src="docs/images/summary.png" alt="요약" width="500">
<img src="docs/images/dashboard1.png" alt="대시보드" width="500">
</details>

---

## 📈 매매 실적

### 시즌 2 (진행 중)
| 지표 | 값 |
|-----|---|
| 시작일 | 2025.09.29 |
| 총 거래 | 50건 |
| 승률 | 42.00% |
| **누적 수익률** | **127.34%** |
| 실계좌 수익률 | +8.50% |

👉 **[라이브 대시보드](https://analysis.stocksimulation.kr/)**

---

## 🇺🇸 미국 주식 모듈

미국 시장을 위한 동일한 AI 기반 워크플로우:

```bash
# 미국 주식 분석 실행
python prism-us/us_stock_analysis_orchestrator.py --mode morning --no-telegram

# 영어 리포트로 실행
python prism-us/us_stock_analysis_orchestrator.py --mode morning --language en
```

**데이터 소스**: yahoo-finance-mcp, sec-edgar-mcp (SEC 공시, 내부자 거래)

---

## 📚 문서

| 문서 | 설명 |
|-----|------|
| [docs/SETUP_ko.md](docs/SETUP_ko.md) | 완전한 설치 가이드 |
| [docs/CLAUDE_AGENTS.md](docs/CLAUDE_AGENTS.md) | AI 에이전트 시스템 상세 |
| [docs/TRIGGER_BATCH_ALGORITHMS.md](docs/TRIGGER_BATCH_ALGORITHMS.md) | 급등주 탐지 알고리즘 |
| [docs/TRADING_JOURNAL.md](docs/TRADING_JOURNAL.md) | 매매 메모리 시스템 |

---

## 💡 MCP 서버

### 한국 시장
- **[kospi_kosdaq](https://github.com/dragon1086/kospi-kosdaq-stock-server)** - KRX 주식 데이터
- **[firecrawl](https://github.com/mendableai/firecrawl-mcp-server)** - 웹 크롤링
- **[perplexity](https://github.com/perplexityai/modelcontextprotocol)** - 웹 검색
- **[sqlite](https://github.com/modelcontextprotocol/servers-archived)** - 매매 시뮬레이션 DB

### 미국 시장
- **[yahoo-finance-mcp](https://pypi.org/project/yahoo-finance-mcp/)** - OHLCV, 재무제표
- **[sec-edgar-mcp](https://pypi.org/project/sec-edgar-mcp/)** - SEC 공시, 내부자 거래

---

## 🤝 기여하기

1. 프로젝트를 포크합니다
2. 기능 브랜치를 생성합니다 (`git checkout -b feature/멋진기능`)
3. 변경사항을 커밋합니다 (`git commit -m '멋진 기능 추가'`)
4. 브랜치에 푸시합니다 (`git push origin feature/멋진기능`)
5. Pull Request를 생성합니다

---

## 📄 라이선스

**이중 라이선스:**

### 개인 및 오픈소스 사용
[![License: AGPL v3](https://img.shields.io/badge/License-AGPL%20v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)

개인 사용, 비상업적 프로젝트, 오픈소스 개발에 AGPL-3.0으로 무료 사용 가능합니다.

### 상업적 SaaS 사용
SaaS 기업은 별도의 상업 라이선스가 필요합니다.

📧 **연락처**: dragon1086@naver.com
📄 **상세**: [LICENSE-COMMERCIAL-ko.md](LICENSE-COMMERCIAL-ko.md)

---

## ⚠️ 면책 조항

분석 정보는 참고용이며 투자 권유가 아닙니다. 모든 투자 결정과 그에 따른 손익은 투자자 본인의 책임입니다.

---

## 💝 후원

### 프로젝트 지원

월간 운영 비용 (~₩290,000/월):
- OpenAI API: ~₩170,000/월
- Anthropic API: ~₩30,000/월
- Firecrawl + Perplexity: ~₩45,000/월
- 서버 인프라: ~₩45,000/월

현재 450명 이상이 무료로 사용하고 있습니다.

<div align="center">
  <a href="https://github.com/sponsors/dragon1086">
    <img src="https://img.shields.io/badge/Sponsor_on_GitHub-❤️-ff69b4?style=for-the-badge&logo=github-sponsors" alt="GitHub에서 후원하기">
  </a>
</div>

### 개인 후원자
<!-- sponsors -->
- [@jk5745](https://github.com/jk5745) 💙
<!-- sponsors -->

---

## ⭐ 프로젝트 성장

출시 이후 **단 10주 만에 250+ Stars** 달성!

[![Star History Chart](https://api.star-history.com/svg?repos=dragon1086/prism-insight&type=Date)](https://star-history.com/#dragon1086/prism-insight&Date)

---

**⭐ 이 프로젝트가 도움이 되었다면 Star를 눌러주세요!**

📞 **문의**: [GitHub Issues](https://github.com/dragon1086/prism-insight/issues) | [텔레그램](https://t.me/stock_ai_agent) | [디스커션](https://github.com/dragon1086/prism-insight/discussions)
