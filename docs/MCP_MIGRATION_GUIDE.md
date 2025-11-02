# PRISM-INSIGHT MCP 마이그레이션 가이드

## 문서 개요

본 문서는 PRISM-INSIGHT 프로젝트에서 현재 사용 중인 MCP (Model Context Protocol) 방식을 일반적인 Agent 프레임워크로 마이그레이션하기 위한 상세한 가이드입니다.

**작성일:** 2025-11-02
**대상 독자:** 백엔드 개발자, AI/ML 엔지니어
**난이도:** 중급-고급

---

## 목차

1. [현재 아키텍처 분석](#1-현재-아키텍처-분석)
2. [MCP 사용 패턴 분석](#2-mcp-사용-패턴-분석)
3. [마이그레이션 전략](#3-마이그레이션-전략)
4. [추천 프레임워크](#4-추천-프레임워크)
5. [단계별 마이그레이션 가이드](#5-단계별-마이그레이션-가이드)
6. [코드 예시](#6-코드-예시)
7. [테스트 및 검증](#7-테스트-및-검증)
8. [배포 전략](#8-배포-전략)

---

## 1. 현재 아키텍처 분석

### 1.1 전체 시스템 구조

PRISM-INSIGHT는 다음과 같은 구조로 동작합니다:

```
┌─────────────────────────────────────────────────────────┐
│  stock_analysis_orchestrator.py (메인 오케스트레이터)   │
└────────────────┬────────────────────────────────────────┘
                 │
                 ├──> trigger_batch.py (급등주 포착)
                 │
                 ├──> cores/analysis.py (핵심 분석 엔진)
                 │    │
                 │    ├──> MCPApp 초기화
                 │    │
                 │    ├──> 다중 Agent 실행
                 │    │    ├─> price_volume_analysis_agent
                 │    │    ├─> investor_trading_analysis_agent
                 │    │    ├─> company_status_agent
                 │    │    ├─> company_overview_agent
                 │    │    ├─> news_analysis_agent
                 │    │    ├─> market_index_analysis_agent
                 │    │    └─> investment_strategy_agent
                 │    │
                 │    └──> 보고서 통합 및 생성
                 │
                 ├──> pdf_converter.py (PDF 변환)
                 │
                 ├──> telegram_summary_agent.py (요약 생성)
                 │
                 ├──> telegram_bot_agent.py (텔레그램 전송)
                 │
                 └──> stock_tracking_enhanced_agent.py (매매 시뮬레이션)
```

### 1.2 MCP 사용 컴포넌트

#### 주요 MCP 의존 파일:

1. **cores/analysis.py**
   - `MCPApp` 초기화 및 실행
   - 여러 agent의 순차적 실행 관리

2. **cores/agents/*.py**
   - 각 전문 에이전트 정의
   - Agent 클래스 사용
   - server_names 지정

3. **cores/report_generation.py**
   - `OpenAIAugmentedLLM` 사용
   - 보고서 생성 로직

4. **stock_tracking_agent.py**
   - 매매 시나리오 생성 agent
   - SQLite MCP 서버 사용

5. **telegram_summary_agent.py**
   - 요약 생성 agent

#### MCP 서버 목록:

| 서버명 | 용도 | 제공 기능 |
|--------|------|-----------|
| `kospi_kosdaq` | 한국 주식 데이터 | OHLCV, 거래량, 투자자별 거래 데이터 |
| `firecrawl` | 웹 크롤링 | 웹페이지 수집 및 파싱 |
| `perplexity` | 웹 검색 | 실시간 정보 검색 |
| `sqlite` | 데이터베이스 | 매매 내역 저장 및 조회 |
| `time` | 시간 정보 | 현재 시간 조회 |

---

## 2. MCP 사용 패턴 분석

### 2.1 Agent 초기화 패턴

**현재 코드 (MCP 방식):**
```python
from mcp_agent.app import MCPApp
from mcp_agent.agents.agent import Agent

app = MCPApp(name="stock_analysis")

agent = Agent(
    name="price_volume_analysis_agent",
    instruction="당신은 주식 기술적 분석 전문가입니다...",
    server_names=["kospi_kosdaq"]
)
```

**핵심 특징:**
- `MCPApp`: 전체 애플리케이션 컨테이너
- `Agent`: 개별 에이전트 정의
- `server_names`: 사용할 MCP 서버 지정
- `instruction`: 에이전트의 역할 및 작업 정의

### 2.2 LLM 실행 패턴

**현재 코드:**
```python
from mcp_agent.workflows.llm.augmented_llm_openai import OpenAIAugmentedLLM
from mcp_agent.workflows.llm.augmented_llm import RequestParams

async with app.run():
    llm = await agent.attach_llm(OpenAIAugmentedLLM)

    response = await llm.generate_str(
        message="분석 요청 메시지",
        request_params=RequestParams(
            model="gpt-4.1",
            maxTokens=16000,
            max_iterations=3,
            parallel_tool_calls=True
        )
    )
```

**핵심 특징:**
- `attach_llm`: 에이전트에 LLM 연결
- `generate_str`: 텍스트 생성
- `RequestParams`: 요청 파라미터 설정
- `parallel_tool_calls`: 병렬 도구 호출

### 2.3 Tool Calling 패턴

MCP는 각 서버가 제공하는 도구를 자동으로 에이전트에 연결합니다:

```python
# Agent instruction 내에서
"""
## 수집해야 할 데이터
1. 주가/거래량 데이터: tool call(name : kospi_kosdaq-get_stock_ohlcv)을 사용하여...
"""
```

LLM이 instruction을 읽고 필요한 도구를 자동으로 호출합니다.

### 2.4 데이터베이스 패턴

SQLite MCP 서버를 통한 데이터 저장:

```python
agent = Agent(
    name="trading_scenario_agent",
    instruction="...",
    server_names=["kospi_kosdaq", "sqlite", "perplexity", "time"]
)

# instruction 내에서 SQL 쿼리 실행
"""
stock_holdings 테이블에서 다음 정보를 확인하세요:
- 현재 보유 종목 수
- 산업군 분포
"""
```

---

## 3. 마이그레이션 전략

### 3.1 마이그레이션 목표

1. **MCP 의존성 제거**: `mcp-agent` 패키지 제거
2. **표준 프레임워크 사용**: 널리 사용되는 agent 프레임워크 도입
3. **기능 유지**: 기존의 모든 기능 보존
4. **성능 개선**: 더 나은 성능 및 확장성
5. **유지보수성 향상**: 커뮤니티 지원 및 문서화

### 3.2 마이그레이션 단계

#### Phase 1: 준비 및 설계 (1-2주)
- [ ] 대체 프레임워크 선정
- [ ] 아키텍처 설계
- [ ] 프로토타입 개발
- [ ] 테스트 계획 수립

#### Phase 2: 핵심 컴포넌트 마이그레이션 (2-3주)
- [ ] Agent 시스템 재구현
- [ ] Tool 시스템 재구현
- [ ] LLM 인터페이스 재구현
- [ ] 단위 테스트 작성

#### Phase 3: 통합 및 검증 (1-2주)
- [ ] 전체 워크플로우 통합
- [ ] 통합 테스트
- [ ] 성능 테스트
- [ ] 버그 수정

#### Phase 4: 배포 및 모니터링 (1주)
- [ ] 스테이징 배포
- [ ] 프로덕션 배포
- [ ] 모니터링 설정
- [ ] 롤백 계획 준비

---

## 4. 추천 프레임워크

### 4.1 프레임워크 비교

| 프레임워크 | 장점 | 단점 | 추천도 |
|-----------|------|------|--------|
| **LangGraph** | • 복잡한 워크플로우 표현<br>• LangChain 생태계<br>• 상태 관리 우수 | • 학습 곡선<br>• 상대적으로 새로운 프레임워크 | ⭐⭐⭐⭐⭐ |
| **LangChain** | • 성숙한 생태계<br>• 풍부한 문서<br>• 다양한 통합 | • 추상화 과다<br>• 복잡한 구조 | ⭐⭐⭐⭐ |
| **LlamaIndex** | • 데이터 중심<br>• RAG 최적화<br>• 쉬운 사용 | • Agent 기능 제한적 | ⭐⭐⭐ |
| **CrewAI** | • 멀티 에이전트 특화<br>• 역할 기반 설계 | • 제한적인 커스터마이징 | ⭐⭐⭐ |
| **AutoGen** | • Microsoft 지원<br>• 강력한 대화 기능 | • 복잡한 설정 | ⭐⭐⭐ |
| **Pydantic AI** | • 타입 안정성<br>• 모던한 설계 | • 아직 초기 단계 | ⭐⭐⭐ |

### 4.2 최종 추천: LangGraph

**선정 이유:**

1. **복잡한 워크플로우 지원**: PRISM-INSIGHT는 여러 agent가 순차적/병렬로 실행되는 복잡한 워크플로우를 가지고 있습니다. LangGraph는 이를 명확하게 표현할 수 있습니다.

2. **상태 관리**: 각 분석 단계의 결과를 다음 단계로 전달하는 stateful한 워크플로우를 쉽게 구현할 수 있습니다.

3. **LangChain 생태계**: LangChain의 모든 도구와 통합을 활용할 수 있습니다.

4. **유연성**: 기존 MCP의 agent 패턴을 쉽게 재현할 수 있습니다.

---

## 5. 단계별 마이그레이션 가이드

### 5.1 환경 설정

#### 기존 의존성 제거
```bash
# requirements.txt에서 제거할 항목
# mcp-agent>=0.1.10
# mcp-server-sqlite
# kospi_kosdaq_stock_server>=0.2.1
```

#### 새로운 의존성 추가
```bash
# requirements.txt에 추가
langgraph>=0.2.0
langchain>=0.3.0
langchain-openai>=0.2.0
langchain-anthropic>=0.2.0
langchain-community>=0.3.0

# 주식 데이터 처리 (MCP 서버 대체)
pykrx==1.0.48
requests>=2.32.3
beautifulsoup4>=4.12.0

# 데이터베이스
sqlalchemy>=2.0.0
aiosqlite>=0.17.0
```

#### 설치
```bash
pip install -r requirements.txt
```

### 5.2 프로젝트 구조 재구성

```
prism-insight/
├── cores/
│   ├── agents/           # Agent 정의
│   │   ├── base_agent.py           # 기본 Agent 클래스
│   │   ├── stock_agents.py         # 주가 분석 Agents
│   │   ├── company_agents.py       # 기업 분석 Agents
│   │   └── strategy_agents.py      # 전략 Agents
│   ├── tools/            # Tool 정의
│   │   ├── stock_data_tools.py     # 주식 데이터 도구
│   │   ├── web_tools.py            # 웹 검색/크롤링 도구
│   │   └── database_tools.py       # DB 도구
│   ├── workflows/        # 워크플로우 정의
│   │   ├── analysis_workflow.py    # 분석 워크플로우
│   │   └── trading_workflow.py     # 매매 워크플로우
│   ├── analysis.py       # 메인 분석 엔진
│   └── utils.py
├── config/
│   ├── agents_config.yaml          # Agent 설정
│   └── tools_config.yaml           # Tool 설정
└── requirements.txt
```

### 5.3 Core Components 구현

#### 5.3.1 기본 Agent 클래스

**파일: cores/agents/base_agent.py**

```python
from typing import List, Dict, Any, Optional
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from langchain_anthropic import ChatAnthropic
from langchain.agents import AgentExecutor, create_openai_functions_agent
from langchain_core.tools import Tool


class BaseAgent:
    """기본 Agent 클래스"""

    def __init__(
        self,
        name: str,
        instruction: str,
        tools: List[Tool],
        model: str = "gpt-4.1",
        temperature: float = 0.0,
        max_iterations: int = 3,
        verbose: bool = True
    ):
        """
        Args:
            name: Agent 이름
            instruction: Agent 지침 (시스템 프롬프트)
            tools: 사용 가능한 도구 리스트
            model: LLM 모델 이름
            temperature: 샘플링 온도
            max_iterations: 최대 반복 횟수
            verbose: 상세 로그 출력 여부
        """
        self.name = name
        self.instruction = instruction
        self.tools = tools
        self.model = model
        self.temperature = temperature
        self.max_iterations = max_iterations
        self.verbose = verbose

        # LLM 초기화
        if "gpt" in model.lower():
            self.llm = ChatOpenAI(
                model=model,
                temperature=temperature
            )
        elif "claude" in model.lower():
            self.llm = ChatAnthropic(
                model=model,
                temperature=temperature
            )
        else:
            raise ValueError(f"지원하지 않는 모델: {model}")

        # 프롬프트 템플릿
        self.prompt = ChatPromptTemplate.from_messages([
            ("system", self.instruction),
            ("human", "{input}"),
            ("placeholder", "{agent_scratchpad}"),
        ])

        # Agent 생성
        self.agent = create_openai_functions_agent(
            llm=self.llm,
            tools=self.tools,
            prompt=self.prompt
        )

        # AgentExecutor
        self.executor = AgentExecutor(
            agent=self.agent,
            tools=self.tools,
            max_iterations=self.max_iterations,
            verbose=self.verbose,
            return_intermediate_steps=True,
            handle_parsing_errors=True
        )

    async def run(self, input_message: str) -> str:
        """Agent 실행"""
        result = await self.executor.ainvoke({"input": input_message})
        return result["output"]

    async def stream(self, input_message: str):
        """Agent 스트리밍 실행"""
        async for chunk in self.executor.astream({"input": input_message}):
            yield chunk
```

#### 5.3.2 주식 데이터 도구

**파일: cores/tools/stock_data_tools.py**

```python
from typing import Optional
from datetime import datetime, timedelta
from langchain_core.tools import Tool
from pykrx import stock
import pandas as pd


class StockDataTools:
    """주식 데이터 도구 모음"""

    @staticmethod
    def get_stock_ohlcv(
        ticker: str,
        start_date: str,
        end_date: str
    ) -> str:
        """
        주식 OHLCV 데이터 조회

        Args:
            ticker: 종목 코드
            start_date: 시작일 (YYYYMMDD)
            end_date: 종료일 (YYYYMMDD)

        Returns:
            JSON 형식의 OHLCV 데이터
        """
        try:
            df = stock.get_market_ohlcv(start_date, end_date, ticker)

            if df.empty:
                return f"종목 코드 {ticker}의 데이터를 찾을 수 없습니다."

            # 데이터 요약
            result = {
                "ticker": ticker,
                "period": f"{start_date} ~ {end_date}",
                "data_points": len(df),
                "latest_price": int(df['종가'].iloc[-1]),
                "price_change": f"{((df['종가'].iloc[-1] / df['종가'].iloc[0] - 1) * 100):.2f}%",
                "average_volume": int(df['거래량'].mean()),
                "high": int(df['고가'].max()),
                "low": int(df['저가'].min()),
                "recent_data": df.tail(10).to_dict('records')
            }

            return str(result)
        except Exception as e:
            return f"데이터 조회 중 오류 발생: {str(e)}"

    @staticmethod
    def get_trading_volume(
        ticker: str,
        start_date: str,
        end_date: str
    ) -> str:
        """
        투자자별 거래량 조회

        Args:
            ticker: 종목 코드
            start_date: 시작일 (YYYYMMDD)
            end_date: 종료일 (YYYYMMDD)

        Returns:
            투자자별 거래 데이터
        """
        try:
            df = stock.get_market_trading_volume_by_date(
                start_date, end_date, ticker
            )

            if df.empty:
                return f"종목 코드 {ticker}의 거래량 데이터를 찾을 수 없습니다."

            result = {
                "ticker": ticker,
                "period": f"{start_date} ~ {end_date}",
                "institutional_net": int(df['기관'].sum()),
                "foreign_net": int(df['외국인'].sum()),
                "individual_net": int(df['개인'].sum()),
                "recent_data": df.tail(10).to_dict('records')
            }

            return str(result)
        except Exception as e:
            return f"거래량 조회 중 오류 발생: {str(e)}"

    @staticmethod
    def create_langchain_tools() -> list[Tool]:
        """LangChain Tool 객체 생성"""
        return [
            Tool(
                name="get_stock_ohlcv",
                description=(
                    "주식의 OHLCV(시가, 고가, 저가, 종가, 거래량) 데이터를 조회합니다. "
                    "입력: ticker(종목코드), start_date(YYYYMMDD), end_date(YYYYMMDD)"
                ),
                func=lambda x: StockDataTools.get_stock_ohlcv(**eval(x))
            ),
            Tool(
                name="get_trading_volume",
                description=(
                    "투자자별(기관, 외국인, 개인) 거래량 데이터를 조회합니다. "
                    "입력: ticker(종목코드), start_date(YYYYMMDD), end_date(YYYYMMDD)"
                ),
                func=lambda x: StockDataTools.get_trading_volume(**eval(x))
            )
        ]
```

#### 5.3.3 웹 검색 도구

**파일: cores/tools/web_tools.py**

```python
from langchain_community.tools import DuckDuckGoSearchRun
from langchain_community.utilities import DuckDuckGoSearchAPIWrapper
from langchain_core.tools import Tool
import requests
from bs4 import BeautifulSoup


class WebTools:
    """웹 검색 및 크롤링 도구"""

    @staticmethod
    def search_web(query: str, max_results: int = 5) -> str:
        """
        웹 검색

        Args:
            query: 검색 쿼리
            max_results: 최대 결과 수

        Returns:
            검색 결과
        """
        try:
            wrapper = DuckDuckGoSearchAPIWrapper(max_results=max_results)
            search = DuckDuckGoSearchRun(api_wrapper=wrapper)
            results = search.run(query)
            return results
        except Exception as e:
            return f"검색 중 오류 발생: {str(e)}"

    @staticmethod
    def scrape_webpage(url: str) -> str:
        """
        웹페이지 스크래핑

        Args:
            url: 웹페이지 URL

        Returns:
            웹페이지 텍스트 내용
        """
        try:
            response = requests.get(url, timeout=10)
            response.raise_for_status()

            soup = BeautifulSoup(response.content, 'html.parser')

            # 불필요한 태그 제거
            for tag in soup(['script', 'style', 'nav', 'footer']):
                tag.decompose()

            text = soup.get_text(separator='\n', strip=True)

            # 텍스트 정리
            lines = [line.strip() for line in text.split('\n') if line.strip()]
            clean_text = '\n'.join(lines)

            # 길이 제한
            if len(clean_text) > 5000:
                clean_text = clean_text[:5000] + "...(중략)"

            return clean_text
        except Exception as e:
            return f"웹페이지 스크래핑 중 오류 발생: {str(e)}"

    @staticmethod
    def create_langchain_tools() -> list[Tool]:
        """LangChain Tool 객체 생성"""
        return [
            Tool(
                name="search_web",
                description="웹 검색을 수행합니다. 최신 뉴스나 정보를 찾을 때 사용하세요.",
                func=WebTools.search_web
            ),
            Tool(
                name="scrape_webpage",
                description="특정 웹페이지의 내용을 가져옵니다. URL을 입력하세요.",
                func=WebTools.scrape_webpage
            )
        ]
```

#### 5.3.4 데이터베이스 도구

**파일: cores/tools/database_tools.py**

```python
from typing import List, Dict, Any
from langchain_core.tools import Tool
import aiosqlite
import sqlite3
import json


class DatabaseTools:
    """데이터베이스 도구"""

    def __init__(self, db_path: str = "stock_tracking_db.sqlite"):
        self.db_path = db_path

    def execute_query(self, query: str) -> str:
        """
        SQL 쿼리 실행

        Args:
            query: SQL 쿼리

        Returns:
            쿼리 결과
        """
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            cursor.execute(query)

            # SELECT 쿼리인 경우
            if query.strip().upper().startswith("SELECT"):
                rows = cursor.fetchall()
                results = [dict(row) for row in rows]
                conn.close()
                return json.dumps(results, ensure_ascii=False, indent=2)

            # INSERT/UPDATE/DELETE 쿼리인 경우
            else:
                conn.commit()
                affected_rows = cursor.rowcount
                conn.close()
                return f"쿼리 실행 완료. 영향받은 행 수: {affected_rows}"

        except Exception as e:
            return f"쿼리 실행 중 오류 발생: {str(e)}"

    def get_holdings(self) -> str:
        """보유 종목 조회"""
        query = """
        SELECT ticker, company_name, buy_price, current_price,
               buy_date, scenario
        FROM stock_holdings
        ORDER BY buy_date DESC
        """
        return self.execute_query(query)

    def get_trading_history(self, limit: int = 10) -> str:
        """매매 이력 조회"""
        query = f"""
        SELECT ticker, company_name, buy_price, sell_price,
               profit_rate, holding_days, sell_date
        FROM trading_history
        ORDER BY sell_date DESC
        LIMIT {limit}
        """
        return self.execute_query(query)

    def create_langchain_tools(self) -> list[Tool]:
        """LangChain Tool 객체 생성"""
        return [
            Tool(
                name="execute_sql",
                description=(
                    "SQL 쿼리를 실행합니다. SELECT, INSERT, UPDATE, DELETE 등을 "
                    "지원합니다. 테이블: stock_holdings, trading_history"
                ),
                func=self.execute_query
            ),
            Tool(
                name="get_holdings",
                description="현재 보유 중인 종목 목록을 조회합니다.",
                func=lambda x: self.get_holdings()
            ),
            Tool(
                name="get_trading_history",
                description="최근 매매 이력을 조회합니다.",
                func=lambda x: self.get_trading_history()
            )
        ]
```

### 5.4 Agent 구현

#### 5.4.1 주가 분석 Agent

**파일: cores/agents/stock_agents.py**

```python
from cores.agents.base_agent import BaseAgent
from cores.tools.stock_data_tools import StockDataTools
from cores.tools.web_tools import WebTools
from datetime import datetime, timedelta


def create_price_volume_agent(
    company_name: str,
    company_code: str,
    reference_date: str
) -> BaseAgent:
    """주가 및 거래량 분석 Agent 생성"""

    # 날짜 계산
    ref_date = datetime.strptime(reference_date, "%Y%m%d")
    start_date = (ref_date - timedelta(days=730)).strftime("%Y%m%d")

    instruction = f"""당신은 주식 기술적 분석 전문가입니다.
{company_name}({company_code})의 주가 데이터와 거래량 데이터를 분석하여 기술적 분석 보고서를 작성해야 합니다.

## 분석 작업 순서

1. **데이터 수집**: get_stock_ohlcv 도구를 사용하여 {start_date}부터 {reference_date}까지의 OHLCV 데이터를 수집하세요.

2. **주가 추세 분석**:
   - 최근 주가 흐름 (상승/하락/횡보)
   - 이동평균선 분석 (단기/중기/장기)
   - 주요 지지선과 저항선 식별

3. **거래량 분석**:
   - 거래량 패턴 분석
   - 주가 움직임과의 상관관계
   - 특이 거래량 시점 파악

4. **기술적 지표**:
   - RSI, MACD 등 주요 지표 계산 및 해석
   - 과매수/과매도 구간 판단

5. **전망 제시**:
   - 단기/중기 기술적 전망
   - 주시해야 할 가격대

## 보고서 형식

반드시 다음 형식으로 작성하세요:

\\n\\n# 1-1. 주가 및 거래량 분석

## 주가 데이터 개요
(최근 추세, 주요 가격대, 변동성)

## 거래량 분석
(거래량 패턴, 주가와의 상관관계)

## 기술적 지표 분석
(이동평균선, 지지/저항선, 기타 지표)

## 기술적 전망
(단기/중기 예상 흐름, 주시해야 할 가격대)

## 주의사항
- 실제 데이터에서 확인된 내용만 포함하세요.
- 확실하지 않은 내용은 "가능성이 있습니다", "~로 보입니다" 등으로 표현하세요.
- 투자 권유가 아닌 정보 제공 관점에서 작성하세요.
- 도구 사용 과정은 보고서에 포함하지 마세요.

분석일: {reference_date}
"""

    # 도구 생성
    tools = StockDataTools.create_langchain_tools()

    return BaseAgent(
        name="price_volume_analysis_agent",
        instruction=instruction,
        tools=tools,
        model="gpt-4.1",
        max_iterations=5
    )


def create_investor_trading_agent(
    company_name: str,
    company_code: str,
    reference_date: str
) -> BaseAgent:
    """투자자 거래 동향 분석 Agent 생성"""

    ref_date = datetime.strptime(reference_date, "%Y%m%d")
    start_date = (ref_date - timedelta(days=730)).strftime("%Y%m%d")

    instruction = f"""당신은 투자자별 거래 데이터 분석 전문가입니다.
{company_name}({company_code})의 투자자별(기관/외국인/개인) 거래 데이터를 분석하여 투자자 동향 보고서를 작성해야 합니다.

## 분석 작업 순서

1. **데이터 수집**: get_trading_volume 도구를 사용하여 {start_date}부터 {reference_date}까지의 투자자별 거래 데이터를 수집하세요.

2. **투자자별 분석**:
   - 기관 투자자 매매 패턴
   - 외국인 투자자 매매 패턴
   - 개인 투자자 매매 패턴

3. **상관관계 분석**:
   - 투자자별 거래와 주가 움직임의 관계
   - 집중 매수/매도 구간 식별

4. **시사점 도출**:
   - 투자자 동향이 주가에 미치는 영향
   - 향후 전망

## 보고서 형식

\\n\\n# 1-2. 투자자 거래 동향 분석

## 투자자별 거래 개요
## 기관 투자자 분석
## 외국인 투자자 분석
## 개인 투자자 분석
## 종합 분석 및 시사점

분석일: {reference_date}
"""

    tools = StockDataTools.create_langchain_tools()

    return BaseAgent(
        name="investor_trading_analysis_agent",
        instruction=instruction,
        tools=tools,
        model="gpt-4.1",
        max_iterations=5
    )
```

### 5.5 워크플로우 구현 (LangGraph)

**파일: cores/workflows/analysis_workflow.py**

```python
from typing import TypedDict, Annotated
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver
from cores.agents.stock_agents import (
    create_price_volume_agent,
    create_investor_trading_agent
)
import logging

logger = logging.getLogger(__name__)


class AnalysisState(TypedDict):
    """분석 워크플로우 상태"""
    company_name: str
    company_code: str
    reference_date: str

    # 각 섹션 보고서
    price_volume_report: str
    investor_trading_report: str
    company_status_report: str
    company_overview_report: str
    news_analysis_report: str
    market_analysis_report: str
    investment_strategy_report: str

    # 최종 보고서
    final_report: str

    # 메타데이터
    current_step: str
    errors: list[str]


async def analyze_price_volume(state: AnalysisState) -> AnalysisState:
    """주가 및 거래량 분석"""
    logger.info("주가 및 거래량 분석 시작")

    try:
        agent = create_price_volume_agent(
            state["company_name"],
            state["company_code"],
            state["reference_date"]
        )

        message = f"{state['company_name']}({state['company_code']})의 주가 및 거래량을 분석해주세요."
        report = await agent.run(message)

        state["price_volume_report"] = report
        state["current_step"] = "price_volume_done"

        logger.info(f"주가 분석 완료: {len(report)} 글자")

    except Exception as e:
        logger.error(f"주가 분석 중 오류: {str(e)}")
        state["errors"].append(f"price_volume: {str(e)}")
        state["price_volume_report"] = "분석 실패"

    return state


async def analyze_investor_trading(state: AnalysisState) -> AnalysisState:
    """투자자 거래 동향 분석"""
    logger.info("투자자 거래 동향 분석 시작")

    try:
        agent = create_investor_trading_agent(
            state["company_name"],
            state["company_code"],
            state["reference_date"]
        )

        message = f"{state['company_name']}({state['company_code']})의 투자자별 거래 동향을 분석해주세요."
        report = await agent.run(message)

        state["investor_trading_report"] = report
        state["current_step"] = "investor_trading_done"

        logger.info(f"투자자 분석 완료: {len(report)} 글자")

    except Exception as e:
        logger.error(f"투자자 분석 중 오류: {str(e)}")
        state["errors"].append(f"investor_trading: {str(e)}")
        state["investor_trading_report"] = "분석 실패"

    return state


def should_continue(state: AnalysisState) -> str:
    """다음 단계 결정"""
    if state["current_step"] == "price_volume_done":
        return "investor_trading"
    elif state["current_step"] == "investor_trading_done":
        return "company_status"
    # ... 추가 단계들
    else:
        return END


def create_analysis_workflow():
    """분석 워크플로우 생성"""

    # 그래프 생성
    workflow = StateGraph(AnalysisState)

    # 노드 추가
    workflow.add_node("price_volume", analyze_price_volume)
    workflow.add_node("investor_trading", analyze_investor_trading)
    # ... 추가 노드들

    # 엣지 추가
    workflow.set_entry_point("price_volume")

    workflow.add_conditional_edges(
        "price_volume",
        should_continue,
        {
            "investor_trading": "investor_trading",
            END: END
        }
    )

    workflow.add_conditional_edges(
        "investor_trading",
        should_continue,
        {
            "company_status": "company_status",
            END: END
        }
    )

    # 메모리 추가
    memory = MemorySaver()

    return workflow.compile(checkpointer=memory)


async def run_analysis(
    company_code: str,
    company_name: str,
    reference_date: str
) -> str:
    """분석 실행"""

    # 워크플로우 생성
    app = create_analysis_workflow()

    # 초기 상태
    initial_state = {
        "company_name": company_name,
        "company_code": company_code,
        "reference_date": reference_date,
        "price_volume_report": "",
        "investor_trading_report": "",
        "company_status_report": "",
        "company_overview_report": "",
        "news_analysis_report": "",
        "market_analysis_report": "",
        "investment_strategy_report": "",
        "final_report": "",
        "current_step": "start",
        "errors": []
    }

    # 실행
    config = {"configurable": {"thread_id": f"{company_code}_{reference_date}"}}

    final_state = await app.ainvoke(initial_state, config)

    # 최종 보고서 조합
    final_report = ""
    final_report += final_state.get("price_volume_report", "") + "\n\n"
    final_report += final_state.get("investor_trading_report", "") + "\n\n"
    # ... 추가 섹션들

    return final_report
```

### 5.6 메인 분석 엔진 수정

**파일: cores/analysis.py**

```python
import os
from datetime import datetime
from cores.workflows.analysis_workflow import run_analysis
from cores.utils import clean_markdown
import logging

logger = logging.getLogger(__name__)


async def analyze_stock(
    company_code: str = "000660",
    company_name: str = "SK하이닉스",
    reference_date: str = None
):
    """
    주식 종합 분석 보고서 생성 (LangGraph 기반)

    Args:
        company_code: 종목 코드
        company_name: 회사명
        reference_date: 분석 기준일 (YYYYMMDD 형식)

    Returns:
        str: 생성된 최종 보고서 마크다운 텍스트
    """

    # reference_date가 없으면 오늘 날짜 사용
    if reference_date is None:
        reference_date = datetime.now().strftime("%Y%m%d")

    logger.info(f"시작: {company_name}({company_code}) 분석 - 기준일: {reference_date}")

    # 워크플로우 실행
    final_report = await run_analysis(
        company_code=company_code,
        company_name=company_name,
        reference_date=reference_date
    )

    # 마크다운 정리
    final_report = clean_markdown(final_report)

    logger.info(f"완료: {company_name} - {len(final_report)} 글자")

    return final_report
```

---

## 6. 코드 예시

### 6.1 간단한 실행 예시

```python
import asyncio
from cores.analysis import analyze_stock

async def main():
    # 분석 실행
    report = await analyze_stock(
        company_code="005930",
        company_name="삼성전자",
        reference_date="20251102"
    )

    # 보고서 저장
    with open("삼성전자_분석보고서.md", "w", encoding="utf-8") as f:
        f.write(report)

    print(f"보고서 생성 완료: {len(report)} 글자")

if __name__ == "__main__":
    asyncio.run(main())
```

### 6.2 매매 시뮬레이션 Agent

**파일: stock_tracking_agent_v2.py**

```python
from cores.agents.base_agent import BaseAgent
from cores.tools.database_tools import DatabaseTools
from cores.tools.stock_data_tools import StockDataTools
from cores.tools.web_tools import WebTools


class TradingAgent:
    """매매 시나리오 생성 Agent"""

    def __init__(self, db_path: str = "stock_tracking_db.sqlite"):
        self.db_path = db_path

        # 도구 생성
        db_tools = DatabaseTools(db_path).create_langchain_tools()
        stock_tools = StockDataTools.create_langchain_tools()
        web_tools = WebTools.create_langchain_tools()

        all_tools = db_tools + stock_tools + web_tools

        instruction = """당신은 신중하고 분석적인 주식 매매 시나리오 생성 전문가입니다.
기본적으로는 가치투자 원칙을 따르되, 상승 모멘텀이 확인될 때는 보다 적극적으로 진입합니다.

## 분석 프로세스

1. **포트폴리오 현황 분석**: get_holdings 도구로 현재 보유 종목 확인
2. **종목 평가**: 주가 데이터, 거래량, 밸류에이션 분석
3. **진입 결정**: 매수 점수(1-10점) 부여
4. **시나리오 생성**: 목표가, 손절가, 매매 시나리오 작성

## 출력 형식

JSON 형식으로 매매 시나리오를 생성하세요:

{
    "portfolio_analysis": "현재 포트폴리오 상황 요약",
    "buy_score": 1~10,
    "decision": "진입" 또는 "관망",
    "target_price": 목표가,
    "stop_loss": 손절가,
    "rationale": "투자 근거"
}
"""

        self.agent = BaseAgent(
            name="trading_scenario_agent",
            instruction=instruction,
            tools=all_tools,
            model="gpt-5",
            max_iterations=10
        )

    async def analyze_report(self, report_path: str) -> dict:
        """보고서 분석하여 매매 시나리오 생성"""

        # 보고서 읽기
        with open(report_path, 'r', encoding='utf-8') as f:
            report_content = f.read()

        message = f"""
다음 주식 분석 보고서를 읽고 매매 시나리오를 생성해주세요.

보고서:
{report_content}
"""

        # Agent 실행
        response = await self.agent.run(message)

        # JSON 파싱
        import json
        scenario = json.loads(response)

        return scenario
```

---

## 7. 테스트 및 검증

### 7.1 단위 테스트

**파일: tests/test_agents.py**

```python
import pytest
import asyncio
from cores.agents.stock_agents import create_price_volume_agent


@pytest.mark.asyncio
async def test_price_volume_agent():
    """주가 분석 Agent 테스트"""

    agent = create_price_volume_agent(
        company_name="삼성전자",
        company_code="005930",
        reference_date="20251102"
    )

    message = "삼성전자의 주가 및 거래량을 분석해주세요."
    result = await agent.run(message)

    # 결과 검증
    assert result is not None
    assert len(result) > 100
    assert "주가" in result or "거래량" in result

    print(f"테스트 성공: {len(result)} 글자")
```

### 7.2 통합 테스트

**파일: tests/test_workflow.py**

```python
import pytest
from cores.workflows.analysis_workflow import run_analysis


@pytest.mark.asyncio
async def test_full_analysis_workflow():
    """전체 분석 워크플로우 테스트"""

    report = await run_analysis(
        company_code="005930",
        company_name="삼성전자",
        reference_date="20251102"
    )

    # 검증
    assert report is not None
    assert len(report) > 1000
    assert "주가" in report
    assert "분석" in report

    print(f"워크플로우 테스트 성공: {len(report)} 글자")
```

### 7.3 성능 테스트

```python
import time
import asyncio
from cores.analysis import analyze_stock


async def performance_test():
    """성능 측정"""

    start = time.time()

    report = await analyze_stock(
        company_code="005930",
        company_name="삼성전자"
    )

    end = time.time()

    print(f"실행 시간: {end - start:.2f}초")
    print(f"보고서 길이: {len(report):,} 글자")
    print(f"초당 글자 수: {len(report) / (end - start):.2f}")


if __name__ == "__main__":
    asyncio.run(performance_test())
```

---

## 8. 배포 전략

### 8.1 점진적 마이그레이션

#### Phase 1: 병렬 운영 (1-2주)
```python
# cores/analysis.py

async def analyze_stock(...):
    # 환경 변수로 구현 선택
    use_langgraph = os.getenv("USE_LANGGRAPH", "false") == "true"

    if use_langgraph:
        # 새로운 구현
        from cores.workflows.analysis_workflow import run_analysis
        return await run_analysis(...)
    else:
        # 기존 MCP 구현
        from cores.analysis_mcp import analyze_stock_mcp
        return await analyze_stock_mcp(...)
```

#### Phase 2: A/B 테스트 (1주)
- 50% 트래픽을 새로운 구현으로 전환
- 결과 비교 및 검증
- 성능 모니터링

#### Phase 3: 완전 전환 (1주)
- 100% 새로운 구현으로 전환
- MCP 관련 코드 제거
- 문서 업데이트

### 8.2 롤백 계획

```bash
# Git 태그로 버전 관리
git tag -a v2.0.0-langgraph -m "LangGraph 마이그레이션"
git push origin v2.0.0-langgraph

# 문제 발생 시 롤백
git revert HEAD
git push origin main
```

### 8.3 모니터링

```python
# cores/monitoring.py

import logging
from datetime import datetime

class AnalysisMonitor:
    """분석 모니터링"""

    def __init__(self):
        self.logger = logging.getLogger("analysis_monitor")

    def log_analysis(
        self,
        company_code: str,
        execution_time: float,
        report_length: int,
        success: bool,
        error: str = None
    ):
        """분석 실행 로그"""

        log_data = {
            "timestamp": datetime.now().isoformat(),
            "company_code": company_code,
            "execution_time": execution_time,
            "report_length": report_length,
            "success": success,
            "error": error
        }

        self.logger.info(f"Analysis: {log_data}")

        # 데이터베이스에 저장하거나 모니터링 도구로 전송
        # ...
```

---

## 9. 추가 고려사항

### 9.1 비용 최적화

- **캐싱**: 동일한 데이터 요청 시 캐시 활용
- **배치 처리**: 여러 종목을 효율적으로 처리
- **모델 선택**: 간단한 작업은 저렴한 모델 사용

```python
from functools import lru_cache
from datetime import datetime, timedelta

@lru_cache(maxsize=100)
def get_cached_stock_data(ticker, date):
    """캐시된 주식 데이터 조회"""
    # ...
```

### 9.2 오류 처리

```python
from tenacity import retry, stop_after_attempt, wait_exponential

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=4, max=10)
)
async def run_agent_with_retry(agent, message):
    """재시도 로직이 있는 Agent 실행"""
    return await agent.run(message)
```

### 9.3 확장성

- **멀티프로세싱**: CPU 집약적 작업 병렬화
- **비동기 처리**: I/O 작업 최적화
- **큐 시스템**: Celery, RabbitMQ 등 활용

---

## 10. 요약 및 체크리스트

### 마이그레이션 체크리스트

#### 준비 단계
- [ ] LangGraph 및 LangChain 설치
- [ ] 기존 MCP 코드 백업
- [ ] 테스트 환경 구성
- [ ] 팀원 교육 및 공유

#### 개발 단계
- [ ] BaseAgent 클래스 구현
- [ ] 주식 데이터 도구 구현
- [ ] 웹 검색 도구 구현
- [ ] 데이터베이스 도구 구현
- [ ] 각 전문 Agent 구현
- [ ] 워크플로우 구현
- [ ] 단위 테스트 작성

#### 검증 단계
- [ ] 통합 테스트 수행
- [ ] 성능 테스트 수행
- [ ] 결과 비교 검증
- [ ] 엣지 케이스 테스트

#### 배포 단계
- [ ] 스테이징 환경 배포
- [ ] A/B 테스트 실행
- [ ] 모니터링 설정
- [ ] 프로덕션 배포
- [ ] MCP 의존성 제거

### 예상 타임라인

| 단계 | 기간 | 담당 |
|------|------|------|
| 준비 및 설계 | 1-2주 | 전체 팀 |
| 개발 | 2-3주 | 백엔드 개발자 |
| 테스트 | 1-2주 | QA 팀 |
| 배포 | 1주 | DevOps 팀 |
| **총 기간** | **5-8주** | |

---

## 11. 참고 자료

### 공식 문서
- [LangGraph 공식 문서](https://langchain-ai.github.io/langgraph/)
- [LangChain 공식 문서](https://python.langchain.com/)
- [OpenAI API 문서](https://platform.openai.com/docs)

### 예제 및 튜토리얼
- [LangGraph Tutorials](https://langchain-ai.github.io/langgraph/tutorials/)
- [Multi-Agent Systems with LangGraph](https://github.com/langchain-ai/langgraph/tree/main/examples)

### 커뮤니티
- [LangChain Discord](https://discord.gg/langchain)
- [LangGraph GitHub Discussions](https://github.com/langchain-ai/langgraph/discussions)

---

## 문의 및 지원

마이그레이션 과정에서 문제가 발생하면 다음 채널을 활용하세요:

- **GitHub Issues**: 기술적 문제 및 버그 리포트
- **팀 슬랙**: 내부 문의 및 협업
- **공식 문서**: 상세한 API 레퍼런스

---

**작성자**: AI Assistant
**최종 업데이트**: 2025-11-02
**버전**: 1.0.0
