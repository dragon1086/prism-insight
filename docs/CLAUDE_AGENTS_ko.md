# AI 에이전트 시스템 - PRISM-INSIGHT

> **참고**: 이 문서는 AI 에이전트 시스템의 상세 참조 문서입니다. 간략한 개요는 메인 [CLAUDE.md](../CLAUDE.md)를 참조하세요.
>
> **언어**: [English](CLAUDE_AGENTS.md) | [한국어](CLAUDE_AGENTS_ko.md)

---

## 13개 이상의 전문화된 에이전트

### 분석 팀 (6개 에이전트) - GPT-5 기반

<img src="images/aiagent/technical_analyst.jpeg" alt="기술적 분석가" width="150" align="right"/>

**1. 기술적 분석가** (`create_price_volume_analysis_agent`)
- **파일**: `cores/agents/stock_price_agents.py`
- **목적**: 주가 및 거래량 기술적 분석
- **분석 항목**: 추세, 이동평균선, 지지/저항선, RSI, MACD, 볼린저 밴드
- **출력**: 리포트의 기술적 분석 섹션

<br clear="both"/>

<img src="images/aiagent/tranding_flow_analyst.jpeg" alt="매매 동향 분석가" width="150" align="right"/>

**2. 매매 동향 분석가** (`create_investor_trading_analysis_agent`)
- **파일**: `cores/agents/stock_price_agents.py`
- **목적**: 투자자별 매매 패턴 분석
- **분석 항목**: 기관/외국인/개인 매매 동향, 거래량 패턴
- **출력**: 매매 동향 섹션

<br clear="both"/>

<img src="images/aiagent/financial_analyst.jpeg" alt="재무 분석가" width="150" align="right"/>

**3. 재무 분석가** (`create_company_status_agent`)
- **파일**: `cores/agents/company_info_agents.py`
- **목적**: 재무 지표 및 밸류에이션 분석
- **분석 항목**: PER, PBR, ROE, 부채비율, 목표주가, 컨센서스
- **출력**: 기업 현황 섹션

<br clear="both"/>

<img src="images/aiagent/industry_analyst.jpeg" alt="산업 분석가" width="150" align="right"/>

**4. 산업 분석가** (`create_company_overview_agent`)
- **파일**: `cores/agents/company_info_agents.py`
- **목적**: 비즈니스 모델 및 경쟁 위치 분석
- **분석 항목**: 사업 포트폴리오, 시장 점유율, 경쟁사, R&D, 성장 동력
- **출력**: 기업 개요 섹션

<br clear="both"/>

<img src="images/aiagent/information_analyst.jpeg" alt="정보 분석가" width="150" align="right"/>

**5. 정보 분석가** (`create_news_analysis_agent`)
- **파일**: `cores/agents/news_strategy_agents.py`
- **목적**: 뉴스 및 촉매 식별
- **분석 항목**: 최신 뉴스, 공시, 산업 동향, 정치/경제 이슈
- **출력**: 뉴스 분석 섹션

<br clear="both"/>

<img src="images/aiagent/market_analyst.jpeg" alt="시장 분석가" width="150" align="right"/>

**6. 시장 분석가** (`create_market_index_analysis_agent`)
- **파일**: `cores/agents/market_index_agents.py`
- **목적**: 시장 및 거시경제 환경 분석
- **분석 항목**: 코스피/코스닥 지수, 거시경제 지표, 글로벌 연관성
- **출력**: 시장 분석 섹션
- **참고**: API 호출 절감을 위해 결과가 캐싱됨

<br clear="both"/>

### 전략 팀 (1개 에이전트) - GPT-5 기반

<img src="images/aiagent/investment_strategist.jpeg" alt="투자 전략가" width="150" align="right"/>

**7. 투자 전략가** (`create_investment_strategy_agent`)
- **파일**: `cores/agents/news_strategy_agents.py`
- **목적**: 모든 분석을 종합하여 실행 가능한 전략 수립
- **통합**: 6개 분석 리포트 전체
- **출력**: 투자자 유형별 추천이 포함된 투자 전략

<br clear="both"/>

### 커뮤니케이션 팀 (3개 에이전트)

<img src="images/aiagent/summary_specialist.jpeg" alt="요약 최적화 전문가" width="150" align="right"/>

**8-1. 요약 최적화 전문가** (`telegram_summary_optimizer_agent`)
- **파일**: `cores/agents/telegram_summary_optimizer_agent.py`
- **모델**: GPT-5
- **목적**: 상세 리포트를 텔레그램 최적화 요약으로 변환
- **제약조건**: 최대 400자, 핵심 포인트 추출
- **출력**: 간결한 텔레그램 메시지

<br clear="both"/>

<img src="images/aiagent/quality_inspector.jpeg" alt="품질 평가 전문가" width="150" align="right"/>

**8-2. 품질 평가 전문가** (`telegram_summary_evaluator_agent`)
- **파일**: `cores/agents/telegram_summary_evaluator_agent.py`
- **모델**: GPT-5
- **목적**: 요약 품질 평가 및 개선 제안
- **점검 항목**: 정확성, 명확성, 형식 준수, 환각 탐지
- **프로세스**: EXCELLENT 등급까지 반복 개선 루프

<br clear="both"/>

<img src="images/aiagent/translator_specialist.png" alt="번역 전문가" width="150" align="right"/>

**8-3. 번역 전문가** (`translate_telegram_message`)
- **파일**: `cores/agents/telegram_translator_agent.py`
- **모델**: GPT-5
- **목적**: 다국어 번역
- **지원 언어**: en, ja, zh, es, fr, de
- **보존**: 기술 용어, 시장 맥락, 서식

<br clear="both"/>

### 매매 시뮬레이션 팀 (3개 에이전트) - GPT-5 기반

> **참고**: 모든 에이전트는 이제 GPT-5 (gpt-5)를 기본 모델로 사용합니다. GPT-5 출력 형식은 `cores/utils.py`에서 추가 정리가 필요합니다 (도구 아티팩트, 헤더).

<img src="images/aiagent/buy_specialist.jpeg" alt="매수 전문가" width="150" align="right"/>

**9-1. 매수 전문가** (`create_trading_scenario_agent`)
- **파일**: `cores/agents/trading_agents.py`
- **목적**: 매수 결정 및 진입 전략 수립
- **평가 항목**: 밸류에이션, 모멘텀, 포트폴리오 제약
- **시장 적응형 기준**:
  - 상승장: 최소 점수 6, 손익비 ≥ 1.5, 손절폭 ≤ 10%
  - 하락/횡보장: 최소 점수 7, 손익비 ≥ 2.0, 손절폭 ≤ 7%
- **출력**: 진입/청산 전략이 포함된 JSON 매매 시나리오

<br clear="both"/>

<img src="images/aiagent/sell_specialist.jpeg" alt="매도 전문가" width="150" align="right"/>

**9-2. 매도 전문가** (`create_sell_decision_agent`)
- **파일**: `cores/agents/trading_agents.py`
- **목적**: 보유 종목 모니터링 및 매도 시점 결정
- **모니터링**: 손절선, 수익 목표, 기술적 추세, 시장 상황
- **출력**: 신뢰도 점수가 포함된 JSON 매도 결정

<br clear="both"/>

**9-3. 매매 저널 에이전트** (선택)
- **파일**: `stock_tracking_agent.py`
- **목적**: 회고적 거래 분석 및 장기 기억 축적
- **기능**:
  - 매수/매도 컨텍스트 비교 및 교훈 추출
  - 계층적 메모리 압축 (상세 → 요약 → 직관)
  - 과거 경험 기반 매수 점수 조정
- **활성화**: `.env`에서 `ENABLE_TRADING_JOURNAL=true` 설정
- **상세 정보**: [TRADING_JOURNAL.md](TRADING_JOURNAL.md)

<br clear="both"/>

### 사용자 상담 팀 (2개 에이전트) - Claude Sonnet 4.5

<img src="images/aiagent/portfolio_consultant.jpeg" alt="포트폴리오 상담가" width="150" align="right"/>

**10-1. 포트폴리오 상담가**
- **파일**: `telegram_ai_bot.py`
- **목적**: 사용자 포트폴리오 평가 및 조언
- **기능**: 사용자 보유 종목, 시장 데이터, 최신 뉴스 기반 맞춤 조언
- **적응**: 사용자 선호도에 따른 응답 스타일

<br clear="both"/>

<img src="images/aiagent/dialogue_manager.jpeg" alt="대화 관리자" width="150" align="right"/>

**10-2. 대화 관리자**
- **파일**: `telegram_ai_bot.py`
- **목적**: 대화 컨텍스트 유지
- **기능**: 컨텍스트 메모리, 후속 질문 처리, 데이터 조회

<br clear="both"/>

---

## 에이전트 협업 패턴

```python
# cores/analysis.py의 패턴
async def analyze_stock(company_name, company_code, reference_date, language="ko"):
    # 1. 에이전트 디렉토리 가져오기
    agents = get_agent_directory(company_name, company_code, reference_date,
                                  base_sections, language)

    # 2. 순차 실행 (레이트 리밋 친화적)
    section_reports = {}
    for section in base_sections:
        if section in agents:
            agent = agents[section]

            # 시장 분석은 특별 처리 (캐시 사용)
            if section == "market_index_analysis":
                report = get_cached_or_generate_market_analysis(...)
            else:
                report = await generate_report(agent, section, ...)

            section_reports[section] = report

    # 3. 투자 전략 생성 (모든 리포트 통합)
    strategy = await generate_investment_strategy(
        agents["investment_strategy"],
        section_reports,
        ...
    )

    return {
        "sections": section_reports,
        "strategy": strategy
    }
```

---

## 새 에이전트 생성하기

**템플릿 패턴**:

```python
# 파일: cores/agents/your_agent.py
from mcp_agent import Agent

def create_your_agent(company_name, company_code, reference_date, language="ko"):
    """
    커스텀 에이전트 생성.

    Args:
        company_name: 회사명
        company_code: 종목코드
        reference_date: 분석일 (YYYY-MM-DD)
        language: "ko" 또는 "en"

    Returns:
        Agent 인스턴스
    """
    if language == "en":
        instruction = """
        You are a specialized analyst focusing on [YOUR DOMAIN].

        Analyze the stock data and provide:
        1. [Specific point 1]
        2. [Specific point 2]

        Be concise and data-driven.
        """
    else:  # 한국어 (기본)
        instruction = """
        당신은 [도메인]을 전문으로 하는 애널리스트입니다.

        다음을 분석하세요:
        1. [분석 항목 1]
        2. [분석 항목 2]

        간결하고 데이터 중심으로 작성하세요.
        """

    return Agent(
        instruction=instruction,
        description=f"Custom Agent for {company_name}",
        # 필요시 MCP 도구 추가
        mcp_servers=["kospi_kosdaq", "firecrawl", "perplexity"],
    )

# cores/agents/__init__.py에 등록
def get_agent_directory(...):
    agents = {
        # ... 기존 에이전트
        "your_section_name": lambda: create_your_agent(...),
    }
    return agents
```

---

*참고: [CLAUDE.md](../CLAUDE.md) | [CLAUDE_TASKS.md](CLAUDE_TASKS.md) | [CLAUDE_TROUBLESHOOTING.md](CLAUDE_TROUBLESHOOTING.md)*
