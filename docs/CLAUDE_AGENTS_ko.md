# AI 에이전트 시스템

> PRISM-INSIGHT의 보고서·거시경제·매매·메모리·커뮤니케이션 에이전트 현행 구조입니다.

**언어**: [English](CLAUDE_AGENTS.md) | [한국어](CLAUDE_AGENTS_ko.md)
**소스 검증 기준**: 2026-07-29

과거의 “13개 이상 에이전트”라는 숫자는 더 이상 정확한 구조 계약이 아닙니다. 현재는 정적 팩토리, 보고서 조립 중 생성되는 인라인 에이전트, 상담 시점에 생성되는 동적 에이전트, 그리고 LLM이 아닌 워크플로우·컨텍스트 객체가 함께 존재합니다. 이 문서는 홍보용 개수 대신 실제 실행 경로로 구분합니다.

## 1. 용어

| 용어 | 의미 |
|---|---|
| 에이전트 정의 | 프롬프트, 논리 이름, MCP 서버 이름의 묶음. KR 보고서 정의는 `ReportAgent`를 사용합니다. |
| 런타임 에이전트 | LLM 백엔드에 전달되는 실행 가능한 `AgentSpec` 또는 `mcp_agent.agents.agent.Agent`입니다. |
| 워크플로우 객체 | 결정론적 오케스트레이션이나 대화 상태입니다. 반드시 LLM 에이전트인 것은 아닙니다. |
| 에이전트 디렉터리 | 안정적인 보고서 섹션 키를 인스턴스화된 정의에 연결하는 매핑입니다. |

예를 들어 `telegram_ai_bot.py`의 `ConversationContext`는 별도의 “Dialogue Manager” LLM 에이전트가 아니라 대화 상태입니다.

## 2. 전체 아키텍처

한국·미국 최상위 실행 순서는 다음과 같습니다.

1. 시장 데이터를 수집하고 거시경제 인텔리전스를 생성합니다.
2. 트리거 선별을 실행하고 선택적으로 시그널을 알립니다.
3. 선택된 각 종목의 보고서를 생성합니다.
4. 보고서를 아카이브에 적재합니다.
5. PDF와 텔레그램 요약을 생성합니다.
6. 보유 종목을 갱신하고 매도 및 신규 진입을 평가합니다.
7. 기능이 켜져 있으면 매매일지와 메모리 피드백을 저장합니다.

선별부터 피드백까지의 상세 흐름은 [PIPELINE_ARCHITECTURE_ko.md](PIPELINE_ARCHITECTURE_ko.md)를 참조하십시오.

### 종목별 보고서 흐름

KR:

```text
사전 수집
  -> 기본 섹션 6개 정의
  -> 기본값은 순차 실행
     (PRISM_PARALLEL_REPORT=true일 때 선택적 병렬)
  -> 투자전략
  -> 핵심 요약
  -> 차트 / 선택적 비전
  -> 최종 Markdown
```

US:

```text
사전 수집
  -> yfinance 기반 섹션 5개 순차 실행
     + 뉴스 분석 동시 실행
  -> 투자전략
  -> 핵심 요약
  -> 차트
  -> 최종 Markdown
```

이 차이는 의도적입니다. KR은 순서 의존성을 보존하는 기본 경로를 사용하고, US는 데이터 기반 섹션을 순차 실행하는 동안 뉴스 조사를 겹쳐 수행합니다.

## 3. 실행 계층 및 공급자

공유 보고서 경로의 경계는 다음과 같습니다.

```text
ReportAgent
  -> AgentSpec + LLMParams
  -> OpenAIAgentsBackend
  -> OpenAI Responses API
```

핵심 파일:

- `cores/agents/report_agent.py`: 가벼운 `ReportAgent` 정의
- `cores/llm/ports.py`: 백엔드 중립 `AgentSpec`, `LLMParams`, 결과 타입
- `cores/llm/backends/openai_agents_backend.py`: OpenAI Agents 실행 백엔드
- `cores/report_generation.py`: 런타임 변환, 투자전략, 요약 생성

매매·거시경제·일부 커뮤니케이션·US 보고서 정의는 augmented LLM과 MCP 연결이 필요한 곳에서 여전히 `mcp_agent.agents.agent.Agent`를 사용합니다. 두 타입의 생성자가 같다고 가정하면 안 됩니다.

## 4. 기본 모델 매트릭스

아래 값은 호출부 또는 환경 변수의 기본값입니다. 모든 에이전트가 하나의 모델을 쓴다는 의미가 아닙니다.

| 워크플로우 | 기본 모델 | 추론 강도 | 출처/재정의 |
|---|---|---|---|
| 공유 보고서 섹션·전략·요약 | `gpt-5.6-terra` | `medium` | `REPORT_MODEL`, `REPORT_REASONING_EFFORT` |
| KR/US 매매 판단 | `gpt-5.6-sol` | `high` | 추적 에이전트 호출부 |
| 텔레그램 상담 | `gpt-5.6-terra` | `medium` | `report_generator.py` |
| 텔레그램 번역 | `gpt-5.6-luna` | 백엔드 기본값 | `translate_telegram_message()` |
| 거시경제 인텔리전스 | `gpt-5.4-mini` | `none` | 시장 오케스트레이터 |
| 텔레그램 요약 평가·최적화 | `gpt-5.4-mini` | `none` | `telegram_summary_agent.py` |
| 매매일지 | `gpt-5.4-mini` | `none` | `tracking/journal.py` |
| 메모리 압축 | `gpt-5.4` | `none` | `tracking/compression.py` |

모델 동작을 바꾸기 전에는 현재 호출부를 확인하십시오. 팩토리 안의 모델 이름은 실행 시점에 재정의될 수 있습니다.

## 5. 에이전트 목록

### 5.1 KR 보고서 에이전트

`cores.agents.create_agents()`가 반환하는 디렉터리에는 기본 정의 6개가 들어 있습니다.

| 섹션 키 | 팩토리/정의 | 주요 근거 |
|---|---|---|
| `price_volume_analysis` | `create_price_volume_analysis_agent()` | 주가·거래량·기술 구조 |
| `investor_trading_analysis` | `create_investor_trading_analysis_agent()` | 투자자 수급 |
| `company_status` | `create_company_status_agent()` | 재무 및 사업 현황 |
| `company_overview` | `create_company_overview_agent()` | 기업·제품·경쟁사 |
| `news_analysis` | `create_news_analysis_agent()` | 최근 뉴스·동종 주도주·촉매 |
| `market_index_analysis` | `create_market_index_analysis_agent()` | KOSPI/KOSDAQ 및 시장 맥락 |

사전 수집 데이터를 우선 사용하고, 데이터가 없을 때 관련 MCP 서버를 폴백으로 남깁니다.

`investment_strategy_agent`와 `summary_agent`는 레지스트리 항목이 아닙니다. 기본 6개 섹션이 끝난 뒤 `cores/report_generation.py`가 인라인으로 생성합니다.

### 5.2 US 보고서 에이전트

`prism-us/cores/agents/__init__.py`는 다음 US 정의를 연결합니다.

| 섹션 키 | 팩토리 |
|---|---|
| `price_volume_analysis` | `create_us_price_volume_analysis_agent()` |
| `institutional_holdings_analysis` | `create_us_institutional_holdings_analysis_agent()` |
| `company_status` | `create_us_company_status_agent()` |
| `company_overview` | `create_us_company_overview_agent()` |
| `news_analysis` | `create_us_news_analysis_agent()` |
| `market_index_analysis` | `create_us_market_index_analysis_agent()` |

투자전략과 요약은 공유 생성기를 재사용합니다.

### 5.3 거시경제 인텔리전스

- KR: `create_macro_intelligence_agent()`
- US: `create_us_macro_intelligence_agent()`

이 에이전트들은 주도·소외 업종, 리스크, 테마, 이벤트를 조사합니다. 숫자 기반 선별 체제는 프롬프트 실행 전에 결정론적으로 계산되며 LLM 추정값으로 대체하지 않습니다.

### 5.4 매매 에이전트

| 시장 | 매수 | 매도 |
|---|---|---|
| KR | `create_trading_scenario_agent()` | `create_sell_decision_agent()` |
| US | `create_us_trading_scenario_agent()` | `create_us_sell_decision_agent()` |

이들은 결정론적 점수·포트폴리오·재진입 쿨다운·주문 게이트 안에서 시나리오 판단을 제공합니다. 보고서 6개 레지스트리가 매매 에이전트를 소유하지는 않습니다.

### 5.5 저널 및 메모리

| 정의 | 상태 |
|---|---|
| `create_trading_journal_agent()` | 기능 활성 시 `JournalManager`/US 매니저를 통해 사용 |
| `create_memory_compressor_agent()` | KR 압축에서 사용 |
| `create_context_retriever_agent()` | 정의됨, 현재 운영 호출부는 찾지 못함 |
| `create_intuition_validator_agent()` | 정의됨, 현재 운영 호출부는 찾지 못함 |

“정의되어 있으나 연결되지 않음” 상태를 운영 중인 에이전트로 설명하지 마십시오.

### 5.6 커뮤니케이션

- `create_telegram_summary_optimizer_agent()`
- `create_telegram_summary_evaluator_agent()`
- `create_telegram_translator_agent()`
- `translate_telegram_message()`

평가·최적화 루프는 짧은 텔레그램 메시지를 목표로 하며 설정된 품질 기준을 만족할 때까지 반복합니다.

### 5.7 동적 상담·리서치 에이전트

`report_generator.py`는 실행 중 다음 에이전트를 추가로 만듭니다.

- KR/US 평가 및 후속 질문 에이전트
- 저널 대화 에이전트
- Firecrawl 검색 분석가 및 후속 에이전트

이들은 동적으로 생성되는 프롬프트 인스턴스이며 `cores/agents/__init__.py`의 고정 항목이 아닙니다.

### 5.8 역할별 이미지

아래 그림은 기존 PRISM-INSIGHT 에이전트 문서에서 사용하던 역할 이미지입니다.
현재 책임 기준으로 다시 배치했으며, 실제 실행 계약은 위에 정리한 팩토리와
호출부를 따릅니다.

<table>
  <tr>
    <td align="center"><img src="images/aiagent/technical_analyst.jpeg" alt="기술적 분석가" width="150"><br><strong>기술적 분석가</strong><br><code>price_volume_analysis</code></td>
    <td align="center"><img src="images/aiagent/tranding_flow_analyst.jpeg" alt="매매 동향 분석가" width="150"><br><strong>매매 동향 분석가</strong><br><code>investor_trading_analysis</code></td>
    <td align="center"><img src="images/aiagent/financial_analyst.jpeg" alt="재무 분석가" width="150"><br><strong>재무 분석가</strong><br><code>company_status</code></td>
  </tr>
  <tr>
    <td align="center"><img src="images/aiagent/industry_analyst.jpeg" alt="산업 분석가" width="150"><br><strong>산업 분석가</strong><br><code>company_overview</code></td>
    <td align="center"><img src="images/aiagent/information_analyst.jpeg" alt="정보 분석가" width="150"><br><strong>정보 분석가</strong><br><code>news_analysis</code></td>
    <td align="center"><img src="images/aiagent/market_analyst.jpeg" alt="시장 분석가" width="150"><br><strong>시장 분석가</strong><br><code>market_index_analysis</code></td>
  </tr>
  <tr>
    <td align="center"><img src="images/aiagent/investment_strategist.jpeg" alt="투자 전략가" width="150"><br><strong>투자 전략가</strong><br>인라인 전략 종합</td>
    <td align="center"><img src="images/aiagent/summary_specialist.jpeg" alt="요약 최적화 전문가" width="150"><br><strong>요약 최적화 전문가</strong><br>텔레그램 요약</td>
    <td align="center"><img src="images/aiagent/quality_inspector.jpeg" alt="품질 평가 전문가" width="150"><br><strong>품질 평가 전문가</strong><br>평가·최적화 루프</td>
  </tr>
  <tr>
    <td align="center"><img src="images/aiagent/translator_specialist.png" alt="번역 전문가" width="150"><br><strong>번역 전문가</strong><br>다국어 배포</td>
    <td align="center"><img src="images/aiagent/buy_specialist.jpeg" alt="매수 전문가" width="150"><br><strong>매수 전문가</strong><br>진입 시나리오 판단</td>
    <td align="center"><img src="images/aiagent/sell_specialist.jpeg" alt="매도 전문가" width="150"><br><strong>매도 전문가</strong><br>청산 판단</td>
  </tr>
  <tr>
    <td align="center"><img src="images/aiagent/portfolio_consultant.jpeg" alt="포트폴리오 상담가" width="150"><br><strong>포트폴리오 상담가</strong><br>동적 상담</td>
    <td align="center"><img src="images/aiagent/dialogue_manager.jpeg" alt="대화 관리자" width="150"><br><strong>대화 컨텍스트</strong><br>독립 LLM 에이전트가 아닌 워크플로우 상태</td>
    <td></td>
  </tr>
</table>

## 6. 레지스트리 및 오케스트레이션 계약

현재 KR 공개 진입점은 다음과 같습니다.

```python
from cores.analysis import analyze_stock

report_markdown = await analyze_stock(
    company_code="005930",
    company_name="삼성전자",
    reference_date="20260729",
    language="ko",
    macro_context=macro_context,
)
```

`analyze_stock()`는 최종 Markdown 문자열을 반환합니다. `reference_date` 형식은 `YYYY-MM-DD`가 아니라 `YYYYMMDD`입니다.

안정적인 섹션 키 6개는 분석 및 보고서 조립 경로가 사용합니다. 키 이름을 바꾸면 소비자와 테스트를 함께 수정해야 합니다. 에이전트 디렉터리는 팩토리 람다가 아니라 생성된 에이전트 객체를 값으로 반환합니다.

## 7. 에이전트 추가·변경 방법

### 7.1 공유 KR 보고서 정의 추가

`name`, `instruction`, `server_names` 계약을 가진 `ReportAgent`를 사용합니다.

```python
# cores/agents/ownership_agent.py
from cores.agents.report_agent import ReportAgent


def create_ownership_agent(
    company_name: str,
    company_code: str,
    reference_date: str,
    language: str = "ko",
) -> ReportAgent:
    return ReportAgent(
        name="ownership_analysis_agent",
        instruction=f"""
Analyze ownership changes for {company_name} ({company_code})
as of {reference_date}. Write in {language}.
""".strip(),
        server_names=["kospi_kosdaq"],
    )
```

이후 작업:

1. `cores/agents/__init__.py`에 팩토리와 안정적인 키를 추가합니다.
2. `cores/analysis.py`의 기본 섹션 순서에 키를 추가합니다.
3. 사전 수집으로 MCP 의존성을 제거할 수 있는지 결정합니다.
4. 섹션 목록을 열거하는 보고서 조립 및 프롬프트를 갱신합니다.
5. 디렉터리 키, 프롬프트 입력, 최종 섹션을 확인하는 집중 테스트를 추가합니다.

`ReportAgent`에 `description=`이나 `mcp_servers=`를 넘기지 마십시오. 이는 오래된 예시의 계약입니다.

### 7.2 MCP 런타임 에이전트 추가

augmented LLM을 직접 실행하는 워크플로우에서만 이 형태를 사용합니다.

```python
from mcp_agent.agents.agent import Agent


def create_runtime_agent() -> Agent:
    return Agent(
        name="runtime_agent",
        instruction="Return a structured decision with evidence.",
        server_names=["sqlite", "time"],
    )
```

호출부가 설정된 LLM/백엔드를 연결하고 실행해야 합니다. 이 생성자를 공유 보고서 디렉터리에 섞지 말고 기존 매매 또는 저널 호출부를 따르십시오.

## 8. MCP 서버 및 설정

논리 서버 이름은 `cores/llm/mcp_servers.yaml`과 레거시 호환 설정 로더에서 가져옵니다.

- `firecrawl`
- `perplexity`
- `webresearch`
- `deepsearch`
- `kospi_kosdaq`
- `sqlite`
- `time`
- `yahoo_finance`

레거시 예제에는 `sec_edgar`도 있지만 현재 native 레지스트리에는 없습니다.

native 레지스트리와 `mcp_agent.config.yaml.example`은 같은 값을 유지해야 합니다. Firecrawl 고정 버전과 Perplexity 실행 방식은 [SETUP_ko.md](SETUP_ko.md)를 참조하십시오.

## 9. 테스트·검증·문서 동기화

에이전트 변경 시 가장 작은 관련 테스트와 보고서 경로를 검증합니다.

```bash
pytest tests -q
python3 demo.py AAPL --language ko
python3 stock_analysis_orchestrator.py --mode morning --no-telegram
```

마지막 두 명령은 네트워크·LLM 호출과 비용을 발생시킬 수 있습니다.

유지관리 원칙:

- 이 문서와 `CLAUDE_AGENTS.md`를 함께 갱신합니다.
- 모델 표는 홍보 문구가 아니라 실행 호출부에서 도출합니다.
- 정적 팩토리, 인라인 에이전트, 동적 에이전트, 워크플로우 상태를 구분합니다.
- 기존 병렬 플래그·경로를 명시적으로 시험하는 경우가 아니면 순차 보고서 순서를 보존합니다.
- KR과 US의 차이를 “동일 구현”으로 숨기지 않습니다.

## 10. 관련 문서

- [English version](CLAUDE_AGENTS.md)
- [파이프라인 아키텍처](PIPELINE_ARCHITECTURE_ko.md)
- [선별 및 배치 알고리즘](TRIGGER_BATCH_ALGORITHMS.md)
- [매매일지 및 메모리](TRADING_JOURNAL.md)
