from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from mcp_agent.agents.agent import Agent
from mcp_agent.workflows.llm.augmented_llm import RequestParams
from mcp_agent.workflows.llm.augmented_llm_openai import OpenAIAugmentedLLM


# Language name mapping for report generation
LANGUAGE_NAMES = {
    "ko": "Korean",
    "en": "English",
    "ja": "Japanese",
    "zh": "Chinese",
    "es": "Spanish",
    "fr": "French",
    "de": "German"
}


@retry(
    stop=stop_after_attempt(2),  # Maximum 2 attempts (initial + 1 retry)
    wait=wait_exponential(multiplier=1, min=10, max=30),  # Exponentially increasing wait time
    retry=retry_if_exception_type(Exception)  # Retry on all exceptions
)
async def generate_report(agent, section, company_name, company_code, reference_date, logger, language="ko"):
    """
    Generate report using agent with retry logic

    Args:
        agent: Analysis agent
        section: Report section name
        company_name: Company name
        company_code: Stock code
        reference_date: Analysis reference date (YYYYMMDD)
        logger: Logger
        language: Report language code (default: "ko")
    """
    language_name = LANGUAGE_NAMES.get(language, language.upper())

    llm = await agent.attach_llm(OpenAIAugmentedLLM)

    # Create language-specific message
    if language == "ko":
        message = f"""{company_name}({company_code})의 {section} 분석 보고서를 작성해주세요.

## 분석 및 보고서 작성 지침:
1. 데이터 수집부터 분석까지 모든 과정을 수행하세요.
2. 보고서는 충분히 상세하되 핵심 정보에 집중하세요.
3. 일반 개인 투자자가 쉽게 이해할 수 있는 수준으로 작성하세요.
4. 투자 결정에 직접적으로 도움이 되는 실용적인 내용에 집중하세요.
5. 실제 수집된 데이터에만 기반하여 분석하고, 없는 데이터는 추측하지 마세요.

## 형식 요구사항:
1. 보고서 시작 시 제목을 넣기 전에 반드시 개행문자를 2번 넣어 시작하세요 (\\n\\n).
2. 섹션 제목과 구조는 에이전트 지침에 명시된 형식을 따르세요.
3. 가독성을 위해 적절히 단락을 나누고, 중요한 내용은 강조하세요.

## 출력 형식 규칙:
- 문장은 자연스러운 산문체로 작성하세요. 문장 중간에 개행하지 마세요.
- 불필요한 bullet point 사용을 금지합니다. 나열이 꼭 필요한 경우에만 사용하세요.
- 하나의 문단은 완결된 문장들로 구성하세요.
- 표 데이터가 아닌 일반 설명은 반드시 문장 형태로 작성하세요.
- ⚠️ 본문 중간에 ##(h2 헤더)를 임의로 사용하지 마세요. 소제목이 필요하면 **굵은 글씨**나 ###를 사용하세요.

## ⚠️ 글자수 제한: 반드시 3000자 이내로 작성하세요. 핵심만 간결하게!

##분석일: {reference_date}(YYYYMMDD 형식)
"""
    else:  # English or other languages
        message = f"""Please write an analysis report for {section} of {company_name}({company_code}).
(Report language: {language_name})

## Analysis and Report Writing Guidelines:
1. Perform all processes from data collection to analysis.
2. Write detailed reports while focusing on key information.
3. Write at a level that is easy for general individual investors to understand.
4. Focus on practical content that directly helps investment decisions.
5. Analyze based only on actual collected data, and do not speculate on missing data.
6. **Always translate company names to {language_name}.** (e.g., "삼성전자" → "Samsung Electronics")

## Format Requirements:
1. Always start the report with two newline characters (\\n\\n) before the title.
2. Follow the format specified in the agent's instructions for section titles and structure.
3. Divide paragraphs appropriately for readability and emphasize important content.

## Output Format Rules:
- Write sentences in natural prose style. Do not break lines in the middle of sentences.
- Do not use unnecessary bullet points. Use them only when listing is absolutely necessary.
- Each paragraph should consist of complete sentences.
- General explanations (not table data) must be written in sentence form.
- ⚠️ Do NOT use ## (h2 headers) arbitrarily in the middle of content. Use **bold text** or ### for sub-sections.

## ⚠️ CHARACTER LIMIT: Keep the report under 3000 characters. Be concise and focus on key insights!

##Analysis Date: {reference_date} (YYYYMMDD format)
"""

    report = await llm.generate_str(
        message=message,
        request_params=RequestParams(
            model="gpt-5.2",
            reasoning_effort="none",
            maxTokens=32000,
            parallel_tool_calls=True,
            use_history=True
        )
    )
    logger.info(f"Completed {section} - {len(report)} characters")
    return report

async def generate_market_report(agent, section, reference_date, logger, language="ko"):
    """
    Generate market analysis report using agent

    Args:
        agent: Analysis agent
        section: Report section name
        reference_date: Analysis reference date (YYYYMMDD)
        logger: Logger
        language: Report language code (default: "ko")
    """
    language_name = LANGUAGE_NAMES.get(language, language.upper())

    llm = await agent.attach_llm(OpenAIAugmentedLLM)

    # Create language-specific message
    if language == "ko":
        message = f"""시장과 거시환경 분석 보고서를 작성해주세요.

## 분석 및 보고서 작성 지침:
1. 데이터 수집부터 분석까지 모든 과정을 수행하세요.
2. 보고서는 충분히 상세하되 핵심 정보에 집중하세요.
3. 일반 개인 투자자가 쉽게 이해할 수 있는 수준으로 작성하세요.
4. 투자 결정에 직접적으로 도움이 되는 실용적인 내용에 집중하세요.
5. 실제 수집된 데이터에만 기반하여 분석하고, 없는 데이터는 추측하지 마세요.

## 형식 요구사항:
1. 보고서 시작 시 제목을 넣기 전에 반드시 개행문자를 2번 넣어 시작하세요 (\\n\\n).
2. 섹션 제목과 구조는 에이전트 지침에 명시된 형식을 따르세요.
3. 가독성을 위해 적절히 단락을 나누고, 중요한 내용은 강조하세요.

## 출력 형식 규칙:
- 문장은 자연스러운 산문체로 작성하세요. 문장 중간에 개행하지 마세요.
- 불필요한 bullet point 사용을 금지합니다. 나열이 꼭 필요한 경우에만 사용하세요.
- 하나의 문단은 완결된 문장들로 구성하세요.
- 표 데이터가 아닌 일반 설명은 반드시 문장 형태로 작성하세요.
- ⚠️ 본문 중간에 ##(h2 헤더)를 임의로 사용하지 마세요. 소제목이 필요하면 **굵은 글씨**나 ###를 사용하세요.

## ⚠️ 글자수 제한: 반드시 3000자 이내로 작성하세요. 핵심만 간결하게!

##분석일: {reference_date}(YYYYMMDD 형식)
"""
    else:  # English or other languages
        message = f"""Please write a market and macroeconomic analysis report.
(Report language: {language_name})

## Analysis and Report Writing Guidelines:
1. Perform all processes from data collection to analysis.
2. Write detailed reports while focusing on key information.
3. Write at a level that is easy for general individual investors to understand.
4. Focus on practical content that directly helps investment decisions.
5. Analyze based only on actual collected data, and do not speculate on missing data.
6. **Always translate company names to {language_name}.** (e.g., "삼성전자" → "Samsung Electronics")

## Format Requirements:
1. Always start the report with two newline characters (\\n\\n) before the title.
2. Follow the format specified in the agent's instructions for section titles and structure.
3. Divide paragraphs appropriately for readability and emphasize important content.

## Output Format Rules:
- Write sentences in natural prose style. Do not break lines in the middle of sentences.
- Do not use unnecessary bullet points. Use them only when listing is absolutely necessary.
- Each paragraph should consist of complete sentences.
- General explanations (not table data) must be written in sentence form.
- ⚠️ Do NOT use ## (h2 headers) arbitrarily in the middle of content. Use **bold text** or ### for sub-sections.

## ⚠️ CHARACTER LIMIT: Keep the report under 3000 characters. Be concise and focus on key insights!

##Analysis Date: {reference_date} (YYYYMMDD format)
"""

    report = await llm.generate_str(
        message=message,
        request_params=RequestParams(
            model="gpt-5.2",
            reasoning_effort="none",
            maxTokens=32000,
            max_iterations=3,
            parallel_tool_calls=True,
            use_history=True
        )
    )
    logger.info(f"Completed {section} - {len(report)} characters")
    return report


async def generate_summary(section_reports, company_name, company_code, reference_date, logger, language="ko"):
    """
    Generate executive summary based on section reports

    Args:
        section_reports: Dictionary of reports by section
        company_name: Company name
        company_code: Stock code
        reference_date: Analysis reference date (YYYYMMDD)
        logger: Logger
        language: Report language code (default: "ko")
    """
    try:
        from mcp_agent.agents.agent import Agent
        from mcp_agent.workflows.llm.augmented_llm import RequestParams
        from mcp_agent.workflows.llm.augmented_llm_openai import OpenAIAugmentedLLM

        language_name = LANGUAGE_NAMES.get(language, language.upper())

        # Generate comprehensive report including all sections
        all_reports = ""
        for section, report in section_reports.items():
            all_reports += f"\n\n--- {section.upper()} ---\n\n"
            all_reports += report

        logger.info(f"Generating executive summary for {company_name}...")

        # Create language-specific instruction and message
        if language == "ko":
            instruction = f"""
당신은 {company_name} ({company_code}) 기업분석 보고서의 핵심 요약을 작성하는 투자 전문가입니다.
전체 보고서의 각 섹션에서 가장 중요한 3-5개의 핵심 포인트를 추출하여 간결하게 요약해야 합니다.
투자자가 빠르게 읽고 핵심을 파악할 수 있는 요약을 제공하세요.

##분석일 : {reference_date}(YYYYMMDD 형식)
"""
            message = f"""아래 {company_name}({company_code})의 종합 분석 보고서를 바탕으로 핵심 투자 포인트 요약을 작성해주세요.

요약에는 기업의 현재 상황, 투자 매력 포인트, 주요 리스크 요소, 적합한 투자자 유형 등이 포함되어야 합니다.
500-800자 정도의 간결하면서도 통찰력 있는 요약을 작성해주세요.

## 형식 가이드라인:
- 제목: "## 핵심 요약" (마크다운 ## 필수)
- 첫 문단: 기업 현재 상황 및 투자 관점 개요
- 불릿 포인트: 3-5개의 핵심 투자 포인트
- 마지막 문단: 적합한 투자자 유형 및 접근법 제안

## 스타일 가이드라인:
- 간결하고 명확한 문장 사용
- 투자 결정에 직접적으로 도움되는 실질적 내용 중심
- 확정적 표현보다 조건부/확률적 표현 사용
- 모든 포인트는 기술적/기본적 분석 데이터에 기반

종합 분석 보고서:
{all_reports}
"""
        else:  # English or other languages
            instruction = f"""
You are an investment expert who writes executive summaries of the {company_name} ({company_code}) company analysis report.
Extract and concisely summarize the 3-5 most important key points from each section of the entire report.
Provide a summary that investors can quickly read and understand the key points.

**Always translate company names to {language_name}.** (e.g., "삼성전자" → "Samsung Electronics")

##Analysis Date: {reference_date} (YYYYMMDD format)
"""
            message = f"""Based on the comprehensive analysis report of {company_name}({company_code}) below, please write a summary of key investment points.
(Report language: {language_name})

The summary should include the company's current situation, investment attraction points, major risk factors, suitable investor types, etc.
Write a concise yet insightful summary of about 500-800 characters.

## Format Guidelines:
- Title: "## Executive Summary" (markdown ## required)
- First paragraph: Overview of the company's current situation and investment perspective
- Bullet points: 3-5 key investment points
- Last paragraph: Suggested investor types and approaches

## Style Guidelines:
- Use concise and clear sentences
- Focus on practical content that directly helps investment decisions
- Use conditional/probabilistic expressions rather than definitive expressions
- All points are based on technical/fundamental analysis data
- **Always translate company names to {language_name}.**

Comprehensive Analysis Report:
{all_reports}
"""

        summary_agent = Agent(
            name="summary_agent",
            instruction=instruction
        )

        llm = await summary_agent.attach_llm(OpenAIAugmentedLLM)
        executive_summary = await llm.generate_str(
            message=message,
            request_params=RequestParams(
                model="gpt-5.2",
                reasoning_effort="none",
                maxTokens=16000,
                max_iterations=2,
                parallel_tool_calls=True,
                use_history=True
            )
        )
        return executive_summary
    except Exception as e:
        logger.error(f"Error generating executive summary: {e}")
        if language == "ko":
            return "## 핵심 요약\n\n분석 요약을 생성하는 데 문제가 발생했습니다."
        else:
            return "## Executive Summary\n\nA problem occurred while generating the analysis summary."


async def generate_investment_strategy(section_reports, combined_reports, company_name, company_code, reference_date, logger, language="ko"):
    """
    Generate investment strategy report

    Args:
        section_reports: Dictionary of reports by section
        combined_reports: Combined report content
        company_name: Company name
        company_code: Stock code
        reference_date: Analysis reference date (YYYYMMDD)
        logger: Logger
        language: Report language code (default: "ko")
    """
    from mcp_agent.agents.agent import Agent
    from mcp_agent.workflows.llm.augmented_llm import RequestParams
    from mcp_agent.workflows.llm.augmented_llm_openai import OpenAIAugmentedLLM

    language_name = LANGUAGE_NAMES.get(language, language.upper())

    try:
        logger.info(f"Processing investment_strategy for {company_name}...")

        # Create language-specific instruction and message
        if language == "ko":
            instruction = f"""당신은 투자 전략 전문가입니다. 앞서 분석된 기술적 분석, 기업 정보, 재무 분석, 뉴스 트렌드, 시장분석을 종합하여 투자 전략 및 의견을 제시해야 합니다.

## 분석 통합 요소
1. 주가/거래량 분석 요약 - 주가 추세, 주요 지지/저항선, 거래량 패턴
2. 투자자 거래 동향 분석 요약 - 기관/외국인/개인 매매 패턴
3. 기업 기본 정보 요약 - 핵심 사업 모델, 경쟁력, 성장 동력
4. 뉴스 분석 요약 - 주요 이슈, 시장 반응, 향후 이벤트
5. 시장 분석 요약 - 시장 변동 요인, 현황, 추세, 거시환경, 기술적 분석, 시장 투자 전략

## 투자 전략 구성 요소
1. 종합 투자 관점 - 기술적/기본적 분석을 종합한 투자 전망
2. 투자자 유형별 전략
   - 단기 트레이더 관점 (1개월 이내)
   - 스윙 트레이더 관점 (1-3개월)
   - 중기 투자자 관점 (3-12개월)
   - 장기 투자자 관점 (1년 이상)
   - 신규 진입자, 기존 보유자 각각의 관점 (비중 활용한 설명)
3. 주요 매매 포인트
   - 매수 고려 가격대 및 조건
   - 매도/손절 가격대 및 조건
   - 수익 실현 전략
4. 핵심 모니터링 요소
   - 주시해야 할 기술적 신호
   - 주목해야 할 실적 지표
   - 체크해야 할 뉴스 및 이벤트
   - 체크해야 할 시장 환경
5. 리스크 요소
   - 잠재적 하방 리스크
   - 상방 기회 요소
   - 리스크 관리 방안

## 작성 스타일
- 객관적인 데이터에 기반한 투자 견해 제시
- 확정적 예측보다는 조건부 시나리오 제시
- 다양한 투자 성향과 기간을 고려한 차별화된 전략 제공
- 구체적인 가격대와 실행 가능한 전략 제시
- 균형 잡힌 리스크-리워드 분석

## 출력 형식 규칙
- 문장은 자연스러운 산문체로 작성하세요. 문장 중간에 개행하지 마세요.
- 불필요한 bullet point 사용을 금지합니다. 나열이 꼭 필요한 경우에만 사용하세요.
- 하나의 문단은 완결된 문장들로 구성하세요.
- 표 데이터가 아닌 일반 설명은 반드시 문장 형태로 작성하세요.
- ⚠️ 본문 중간에 ##(h2 헤더)를 임의로 추가하지 마세요. 정해진 섹션 구조만 사용하세요.

## 보고서 형식
- 보고서 시작 시 개행문자 2번 삽입(\\n\\n)
- 제목: "### 5-1. 투자 전략 및 의견" (마크다운 ### 필수 - 메인 섹션 제목은 별도 추가됨)
- 소제목은 반드시 "#### 소제목명" 형식 사용 (마크다운 #### 필수)
- 투자자 유형별 전략은 명확히 구분하여 제시
- 주요 매매 포인트는 구체적인 가격대와 조건으로 표현
- 리스크 요소는 중요도에 따라 구분하여 설명

## 주의사항
- "투자 권유"가 아닌 "투자 참고 정보" 형태로 제공
- 일방적인 매수/매도 권유는 피하고, 조건부 접근법 제시
- 과도한 낙관론이나 비관론은 지양
- 모든 투자 전략은 기술적/기본적 분석의 실제 데이터에 근거
- "반드시", "확실히" 등의 단정적 표현보다 "~할 가능성", "~로 예상" 등 사용
- 모든 투자에는 리스크가 있음을 명시

## 결론 부분
- 마지막에 간략한 요약과 핵심 투자 포인트 3-5개 제시
- "본 보고서는 투자 참고용이며, 투자 책임은 투자자 본인에게 있습니다." 문구 포함

기업: {company_name} ({company_code})
##분석일: {reference_date}(YYYYMMDD 형식)
"""
            message = f"""{company_name}({company_code})의 투자 전략 분석 보고서를 작성해주세요.

## 앞서 분석된 다른 섹션의 내용:
{combined_reports}

## 투자 전략 작성 지침:
앞서 분석된 모든 정보를 바탕으로 종합적인 투자 전략 보고서를 작성하세요.
기존에 설정된 투자 전략 에이전트의 지침에 따라 작성하되, 특히 다음 사항에 중점을 두세요:

1. 앞서 분석된 다양한 데이터(기술적/기본적/뉴스)를 단순 요약이 아닌 통합적 관점에서 재해석
2. 현 시점({reference_date})의 주가 수준에서 투자 매력도 평가
3. 밸류에이션과 실적 전망을 연계한 투자 시나리오 제시
4. 업종 및 시장 전체 흐름 속에서의 상대적 투자 매력도 분석

일관성 있고 실행 가능한 투자 전략을 제시하여 투자자가 실제 의사결정에 활용할 수 있도록 해주세요.

## 형식 및 스타일 요구사항:
- 앞서 설정된 형식(제목, 구조, 스타일)을 그대로 따르세요
- 투자자가 행동으로 옮길 수 있는 실질적인 전략 제시에 초점을 맞추세요

## ⚠️ 글자수 제한: 반드시 3000자 이내로 작성하세요. 핵심만 간결하게!
"""
        else:  # English or other languages
            instruction = f"""You are an investment strategy expert. Synthesize the previously analyzed technical analysis, company information, financial analysis, news trends, and market analysis to present investment strategies and opinions.

**Always translate company names to {language_name}.** (e.g., "삼성전자" → "Samsung Electronics")

## Analysis Integration Elements
1. Stock Price/Volume Analysis Summary - Price trends, major support/resistance levels, volume patterns
2. Investor Trading Trends Analysis Summary - Institutional/foreign/retail trading patterns
3. Company Basic Information Summary - Core business model, competitiveness, growth drivers
4. News Analysis Summary - Major issues, market reactions, upcoming events
5. Market Analysis Summary - Market volatility factors, current status, trends, macroeconomic environment, technical analysis, market investment strategy

## Investment Strategy Components
1. Comprehensive Investment Perspective - Investment outlook combining technical/fundamental analysis
2. Strategies by Investor Type
   - Short-term trader perspective (within 1 month)
   - Swing trader perspective (1-3 months)
   - Mid-term investor perspective (3-12 months)
   - Long-term investor perspective (over 1 year)
   - Perspectives for new entrants and existing holders (explained using position sizing)
3. Key Trading Points
   - Buy consideration price range and conditions
   - Sell/stop-loss price range and conditions
   - Profit-taking strategy
4. Core Monitoring Elements
   - Technical signals to watch
   - Performance indicators to pay attention to
   - News and events to check
   - Market conditions to check
5. Risk Factors
   - Potential downside risks
   - Upside opportunity factors
   - Risk management measures

## Writing Style
- Present investment views based on objective data
- Present conditional scenarios rather than definitive predictions
- Provide differentiated strategies considering various investment preferences and timeframes
- Present specific price ranges and executable strategies
- Balanced risk-reward analysis

## Output Format Rules
- Write sentences in natural prose style. Do not break lines in the middle of sentences.
- Do not use unnecessary bullet points. Use them only when listing is absolutely necessary.
- Each paragraph should consist of complete sentences.
- General explanations (not table data) must be written in sentence form.
- ⚠️ Do NOT add arbitrary ## (h2 headers) in the middle of content. Use only the defined section structure.

## Report Format
- Insert 2 newline characters at the start of the report (\\n\\n)
- Title: "### 5-1. Investment Strategy and Opinion" (markdown ### required - main section header is added separately)
- Sub-sections MUST use "#### Sub-section Title" format (markdown #### required)
- Clearly distinguish strategies by investor type
- Express key trading points with specific price ranges and conditions
- Explain risk factors according to importance

## Cautions
- Provide as "investment reference information" not "investment solicitation"
- Avoid unilateral buy/sell solicitation, present conditional approaches
- Avoid excessive optimism or pessimism
- All investment strategies are based on actual data from technical/fundamental analysis
- Use expressions like "~possibility", "~expected" rather than definitive expressions like "certainly", "definitely"
- Clearly state that all investments involve risks

## Conclusion
- Provide a brief summary and 3-5 key investment points at the end
- Include the statement "This report is for investment reference only, and investment decisions and responsibilities lie with the investor."

Company: {company_name} ({company_code})
##Analysis Date: {reference_date} (YYYYMMDD format)
"""
            message = f"""Please write an investment strategy analysis report for {company_name}({company_code}).
(Report language: {language_name})

## Contents of Other Previously Analyzed Sections:
{combined_reports}

## Investment Strategy Writing Guidelines:
Based on all previously analyzed information, write a comprehensive investment strategy report.
Follow the guidelines set in the investment strategy agent, but pay particular attention to the following:

1. Reinterpret the various analyzed data (technical/fundamental/news) from an integrated perspective, not just a simple summary
2. Evaluate investment attractiveness at the current stock price level ({reference_date})
3. Present investment scenarios linking valuation and earnings outlook
4. Analyze relative investment attractiveness within the overall industry and market flow
5. **Always translate company names to {language_name}.**

Please present a consistent and executable investment strategy that investors can use for actual decision-making.

## Format and Style Requirements:
- Follow the previously set format (title, structure, style) as is
- Focus on presenting practical strategies that investors can act on

## ⚠️ CHARACTER LIMIT: Keep the report under 3000 characters. Be concise and focus on key insights!
"""

        investment_strategy_agent = Agent(
            name="investment_strategy_agent",
            instruction=instruction
        )

        llm = await investment_strategy_agent.attach_llm(OpenAIAugmentedLLM)
        investment_strategy = await llm.generate_str(
            message=message,
            request_params=RequestParams(
                model="gpt-5.2",
                reasoning_effort="none",
                maxTokens=32000,
                max_iterations=3,
                parallel_tool_calls=True,
                use_history=True
            )
        )
        logger.info(f"Completed investment_strategy - {len(investment_strategy)} characters")
        return investment_strategy
    except Exception as e:
        logger.error(f"Error processing investment_strategy: {e}")
        if language == "ko":
            return "투자 전략 분석 실패"
        else:
            return "Investment strategy analysis failed"


def get_disclaimer(language="ko"):
    """
    Get disclaimer text

    Args:
        language: Disclaimer language code (default: "ko")

    Returns:
        Disclaimer text in specified language
    """
    if language == "ko":
        return """## 투자 유의사항

본 보고서는 정보 제공을 목적으로 작성되었으며, 투자 권유를 목적으로 하지 않습니다.
본 보고서에 기재된 내용은 작성 시점 기준으로 신뢰할 수 있는 자료에 근거하여 AI로 작성되었으나,
그 정확성과 완전성을 보장하지 않습니다.

투자는 본인의 판단과 책임 하에 신중하게 이루어져야 하며,
본 보고서를 참고하여 발생하는 투자 결과에 대한 책임은 투자자 본인에게 있습니다."""
    else:  # English or other languages
        return """## Investment Disclaimer

This report is provided for informational purposes only and is not intended as investment advice.
The content in this report is AI-generated based on reliable sources as of the time of writing,
but its accuracy and completeness are not guaranteed.

Investments should be made carefully at your own judgment and risk,
and you are solely responsible for any investment results based on this report."""
