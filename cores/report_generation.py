from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from cores.agents.report_agent import ReportAgent, report_time_contract
from cores.llm.agent_bridge import ensure_openai_agents_configured
from cores.llm.backends.openai_agents_backend import OpenAIAgentsBackend
from cores.llm.config_loader import load_report_mcp_registry
from cores.llm.ports import AgentSpec, LLMParams
from report_model_config import REPORT_EFFORT, REPORT_MODEL
from cores.openai_error_logging import log_openai_error
from prism_core.report_presentation import report_narrative_contract

# Report LLM model/effort are shared with macro, summaries and artifact names.
# Long-form reports keep medium reasoning for cross-source reconciliation and
# numeric strategy synthesis. Auxiliary report tasks use the separate low-effort
# contract; the Responses API backend remains required for gpt-5.6 tool calls.

_report_backend = None


def synthesis_evidence_contract(language='ko'):
    """All supplied chapters inform synthesis, without adding trading rules."""
    from prism_core.report_evidence_contract import financial_evidence_contract
    if language == 'ko':
        return (financial_evidence_contract(language)
                + '\n종합 입력 계약: 제공된 공시 심층 분석·비교기업·공식 실적/가이던스·정량 수급·'
                '시장 계산값·거시 위험을 기존 기술·기업·뉴스 분석과 함께 검토하세요. '
                '중요한 변화와 위험이 결론에 미치는 영향을 반영하되 요약에 모든 문장을 반복하지 마세요. '
                '연결/별도·실적/예상·기간·단위·관측시점을 섞지 말고, 누락 자료를 만들어내지 마세요. '
                '부록에 표시될 근거도 같은 입력입니다. 자료 범위의 한계를 유지하고 기존 매매 정책을 바꾸지 마세요.\n')
    return (financial_evidence_contract(language)
            + '\nSynthesis evidence contract: consider all supplied filing-depth, peer, official results/guidance, '
            'quantified flow, market calculations and macro-risk evidence alongside technical, company and news sections. '
            'Reflect material changes and risks, not every sentence. Preserve consolidated/standalone, actual/forecast, '
            'period, unit and observation-time distinctions. Do not invent missing evidence or alter trading policy. '
            'Evidence displayed in an appendix is still part of the same synthesis input.\n')


def _get_report_backend():
    """Lazily configure the SDK and native MCP registry for report calls."""
    global _report_backend
    if _report_backend is None:
        ensure_openai_agents_configured()
        _report_backend = OpenAIAgentsBackend(load_report_mcp_registry())
    return _report_backend


async def _generate_agent_text(
    agent,
    message: str,
    *,
    max_tokens: int,
    max_iterations: int,
) -> str:
    """Run one SDK-neutral report definition through the shared LLM port."""
    spec = AgentSpec(
        name=agent.name,
        instructions=agent.instruction,
        model=REPORT_MODEL,
        mcp_servers=tuple(agent.server_names),
        params=LLMParams(
            max_tokens=max_tokens,
            reasoning_effort=REPORT_EFFORT,
            parallel_tool_calls=True,
            max_iterations=max_iterations,
        ),
    )
    result = await _get_report_backend().run(spec, message)
    return result.text


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

## 말투 규칙 (매우 중요):
- 보고서 본문은 반드시 '~입니다', '~합니다', '~됩니다', '~있습니다' 등 높임말(합쇼체)로 작성하세요.
- '~한다', '~된다', '~이다', '~있다' 등 반말(해라체) 사용을 금지합니다.
- 예시: "상승세를 보인다" (X) → "상승세를 보이고 있습니다" (O)
- 예시: "주목할 필요가 있다" (X) → "주목할 필요가 있습니다" (O)

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

    try:
        report = await _generate_agent_text(
            agent,
            message + report_time_contract(reference_date, language),
            max_tokens=32000,
            max_iterations=10,
        )
    except Exception as e:
        log_openai_error(logger, e, f"report generation for {section}")
        raise
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

## 말투 규칙 (매우 중요):
- 보고서 본문은 반드시 '~입니다', '~합니다', '~됩니다', '~있습니다' 등 높임말(합쇼체)로 작성하세요.
- '~한다', '~된다', '~이다', '~있다' 등 반말(해라체) 사용을 금지합니다.
- 예시: "상승세를 보인다" (X) → "상승세를 보이고 있습니다" (O)
- 예시: "주목할 필요가 있다" (X) → "주목할 필요가 있습니다" (O)

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

    try:
        report = await _generate_agent_text(
            agent,
            message + report_time_contract(reference_date, language),
            max_tokens=32000,
            max_iterations=3,
        )
    except Exception as e:
        log_openai_error(logger, e, f"market report generation for {section}")
        raise
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
당신은 {company_name} ({company_code}) 기업분석 보고서의 첫 페이지인 핵심 요약을 쓰는 투자 분석가입니다.
독자는 회계·공시 용어에 익숙하지 않은 일반 개인 투자자입니다. 독자는 "그래서 이게 무슨 말인데?", "이 사실들이 엮이면 어떤 이야기가 되는데?", "공시 내용이 지금 보이는 주가·거래량·투자자 매매와 무슨 관계인데?"를 가장 먼저 궁금해합니다.
각 장을 차례로 요약하지 말고, 여러 장의 사실을 연결해 하나의 이야기와 인사이트로 풀어 주세요. 겉으로 보이는 숫자와 공시를 들여다봐야 보이는 사실이 어떻게 다른지, 일반 투자자가 놓치기 쉬운 내용을 짚어 주세요.

##분석일 : {reference_date}(YYYYMMDD 형식)
"""
            message = f"""아래 {company_name}({company_code})의 종합 분석 보고서를 바탕으로 핵심 요약을 작성해주세요.
분량은 약 900~1,400자입니다.

## 형식
- 제목: "## 핵심 요약" (마크다운 ## 필수). 이 밖에 새로운 ##·### 제목은 만들지 마세요.
- 제목 아래에 다음 굵은 머리말을 순서대로 쓰고, 각 머리말로 시작하는 짧은 문단(3번은 머리말 다음 글머리표)으로 작성하세요. 번호는 붙이지 마세요.
1. **한 줄 결론** — 이 종목에 지금 무슨 일이 일어나고 있는지와 전체 관점을 한 문장으로 씁니다. 매수·매도 지시가 아닌 조건부 판단으로 씁니다.
2. **지금 무슨 일이 일어나고 있나** — 2~3문장. 겉으로 보이는 신호(주가 추세, 거래량, 외국인·기관·개인 중 누가 사고파는지)를 그 원인으로 보이는 요인(실적, 뉴스, 업종 흐름)과 연결합니다. 투자자별 매매 자료가 없으면 한 구절로 그 사실만 밝힙니다.
3. **숫자 뒤에 숨은 이야기** — 글머리표 2~3개. 각 항목은 "겉으로 보면 …, 하지만 공시를 들여다보면 … → 그래서 투자자에게 의미하는 것은 …"의 흐름으로 씁니다. 국내 종목은 'DART 주요 재무·사업 위험 분석' 장의 핵심 포인트와 본문에서, 미국 종목은 공식 실적·가이던스와 재무의 질에 관한 사실에서 가져옵니다. 누구나 아는 사실보다 드러나지 않은 사실을 고르세요. 예: 이익이 일회성 법인세 수익으로 부풀려진 경우, 전환사채가 주식으로 바뀌어 부채는 줄었지만 주식 수가 늘어난 경우(상환 부담은 줄고 희석은 늘어남), 매출채권·재고가 매출보다 빠르게 늘어나는 경우, 차입 만기가 한꺼번에 몰리거나 자산이 담보로 묶인 경우, 특수관계자 거래가 큰 경우. 회계·공시 용어는 처음 나올 때 괄호 안에 쉬운 말로 풀어 씁니다(예: 전환사채(나중에 주식으로 바꿀 수 있는 채권)). 금액은 전체 자릿수 대신 억원·조원(미국 종목은 $B·$M) 단위로 씁니다. 공시 심층 자료가 없으면 기업 현황·재무 분석에서 확인되는 사실만 사용합니다.
4. **경쟁사와 비교하면** — 1~2문장. '경쟁사 비교 분석' 표를 근거로 이 회사가 비교 기업보다 빠르게 또는 느리게 성장하고 이익을 내는지, 밸류에이션이 프리미엄인지 할인인지, 그 프리미엄(또는 할인)이 숫자로 설명되는지를 씁니다. 비교 기업 1~2곳의 이름을 적습니다. 표가 없으면 이 항목은 통째로 생략하고 만들어내지 마세요.
5. **공시와 주가·수급의 연결** — 1~2문장. 공시·재무 사실이 현재 주가와 투자자 매매가 반영하는 기대를 뒷받침하는지, 어긋나는지를 씁니다(예: 외국인·기관 순매수가 실적 개선을 따라가는지, 기대가 실적보다 앞서 있는지).
6. **앞으로 확인할 것** — 구체적 확인 항목 2~3개(일정·이벤트, 지표, 투자 전략 장에 나온 가격대)와 각 항목이 판단을 어떻게 바꿀 수 있는지 씁니다.

## 작성 규칙
- 일반 투자자가 읽는 쉬운 한국어와 짧은 문장으로 쓰고, 보고서 본문은 반드시 높임말(합쇼체)로 작성합니다 ('~입니다', '~합니다' 등).
- 확인된 사실과 해석을 구분합니다. 해석은 "~로 보입니다", "~일 가능성이 있습니다"처럼 씁니다.
- 모든 장을 반복하지 말고 판단에 중요한 연결만 씁니다.
- 가격대·손절 기준·매매 조건은 입력의 투자 전략 장(INVESTMENT_STRATEGY)에 있는 것만 쓰고, 새로운 가격대나 매매 규칙을 만들지 않습니다. 투자 전략 장의 관점과 어긋나지 않게 씁니다.
- 내부 라벨, 상태 코드, 영어 필드명(예: SOURCE_CHECKED, NOT_IN_INPUT, UNKNOWN, company_status)을 본문에 쓰지 않습니다.
- 입력에 없는 자료를 만들어내지 않습니다. 필요한 자료가 없으면 없다고만 짧게 씁니다.
- 확정적 표현보다 조건부/확률적 표현을 쓰고, 투자를 권유하지 않습니다.

종합 분석 보고서:
{all_reports}
"""
        else:  # English or other languages
            instruction = f"""
You are an investment analyst writing the executive summary, the first page of the {company_name} ({company_code}) company analysis report.
Readers are ordinary retail investors who are not familiar with accounting or filing terms. Their first questions are: "So what does this actually mean?", "How do these facts connect into a story?", and "How do the filings relate to the price, volume and investor buying/selling we can see right now?"
Do not summarize each chapter in turn. Connect facts across chapters into one story with insight, and point out where the surface numbers differ from what the filings reveal, and what ordinary investors are likely to miss.

**Always translate company names to {language_name}.** (e.g., "삼성전자" → "Samsung Electronics")

##Analysis Date: {reference_date} (YYYYMMDD format)
"""
            message = f"""Based on the comprehensive analysis report of {company_name}({company_code}) below, please write the executive summary.
(Report language: {language_name})
Length: about 1,400-2,200 characters.

## Format
- Title: "## Executive Summary" (markdown ## required). Do not create any other ## or ### headings.
- Under the title, use the following bold lead-ins in this order, each starting a short paragraph (item 3 is followed by bullets). Do not number them.
1. **Bottom line** — One sentence: what is happening with this stock now and the overall view, as a conditional judgment, not a buy/sell instruction.
2. **What is happening now** — 2-3 sentences linking the surface signals (price trend, volume, who is buying or selling: foreign, institutional, retail investors) to their likely cause (earnings, news, sector). If investor flow data is missing, say so in one clause.
3. **The story behind the numbers** — 2-3 bullets, each in the pattern "On the surface …, but the filings show … → so for investors this means …". For KR stocks use the key points and body of the 'DART 주요 재무·사업 위험 분석' (DART filing-risk) chapter; for US stocks use official results/guidance and financial-quality facts. Pick the non-obvious facts, e.g., profit boosted by one-off tax income; debt that fell because a convertible bond turned into shares (less repayment risk, but more shares and dilution); receivables or inventory growing faster than sales; clustered debt maturities or assets pledged as collateral; large related-party transactions. Explain any accounting term in plain words in parentheses the first time (e.g., "convertible bond (a bond that can later be exchanged for shares)"). Use rounded units ($B/$M, or 100-million/trillion won) rather than full-digit amounts. If filing-depth evidence is absent, use only facts from the company status/financial chapters.
4. **Versus peers** — 1-2 sentences from the '경쟁사 비교 분석' (competitor comparison) table: whether the company grows and profits faster or slower than its peers, whether its valuation is a premium or a discount, and whether that premium (or discount) looks justified by the numbers. Name 1-2 peers. If there is no table, omit this item entirely; do not invent it.
5. **Filings vs. price and flows** — 1-2 sentences on whether the filing/fundamental facts support or contradict what the price and investor flows are pricing in (e.g., whether foreign/institutional net buying follows improving results, or expectations run ahead of results).
6. **What to watch next** — 2-3 concrete checkpoints (event/date, metric, price level from the investment strategy chapter) and how each could change the view.

## Writing Rules
- Plain language for non-experts and short sentences. **Always translate company names to {language_name}.**
- Separate facts from interpretation; phrase interpretation as "appears to" or "may".
- Do not repeat every chapter; keep only the connections that matter for the judgment.
- Use only price levels, stop-loss levels and trading conditions that appear in the investment strategy chapter (INVESTMENT_STRATEGY) of the input; do not introduce new price levels or trading rules, and stay consistent with that chapter's view.
- Do not write internal labels, status codes or English field names (e.g., SOURCE_CHECKED, NOT_IN_INPUT, UNKNOWN, company_status) in the text.
- Do not invent missing data; if something needed is missing, say so briefly.
- Use conditional/probabilistic language and do not solicit investment.

Comprehensive Analysis Report:
{all_reports}
"""

        summary_agent = ReportAgent(
            name="summary_agent",
            instruction=instruction + report_narrative_contract(language) + synthesis_evidence_contract(language)
        )

        executive_summary = await _generate_agent_text(
            summary_agent,
            message + report_time_contract(reference_date, language),
            max_tokens=16000,
            max_iterations=2,
        )
        return executive_summary
    except Exception as e:
        log_openai_error(logger, e, f"executive summary generation for {company_name}")
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
    language_name = LANGUAGE_NAMES.get(language, language.upper())

    try:
        logger.info(f"Processing investment_strategy for {company_name}...")

        # Create language-specific instruction and message
        if language == "ko":
            instruction = f"""당신은 투자 전략 전문가입니다. 앞서 분석된 기술적 분석, 기업 정보, 재무 분석, 뉴스 트렌드, 시장분석과 입력에 있는 경우 공시 심층분석·경쟁사 비교 표·공식 실적/가이던스를 종합하여 투자 전략 및 의견을 제시해야 합니다.

## 분석 통합 요소
1. 주가/거래량 분석 요약 - 주가 추세, 주요 지지/저항선, 거래량 패턴
2. 투자자 거래 동향 분석 요약 - 기관/외국인/개인 매매 패턴
3. 기업 기본 정보 요약 - 핵심 사업 모델, 경쟁력, 성장 동력
4. 뉴스 분석 요약 - 주요 이슈, 시장 반응, 향후 이벤트
5. 시장 분석 요약 - 시장 변동 요인, 현황, 추세, 거시환경, 기술적 분석, 시장 투자 전략
6. DART 공시 심층분석 (국내 종목, 해당 장이 있을 때만) - 이익의 질·일회성 요인, 차입 만기·이자 부담, 희석·자본변동, 담보·보증·우발위험, 특수관계자 거래
7. 경쟁사 비교 분석 표 (있을 때만) - 비교 기업 대비 성장률·수익성·밸류에이션의 상대 위치
8. 공식 실적·가이던스 (미국 종목, 있을 때만) - 최근 발표 실적과 회사 가이던스, 그 변화
6~8번 자료는 입력에 있을 때만 사용하고, 없으면 언급하거나 만들어내지 마세요.

## 투자 전략 구성 요소
1. 핵심 투자 논리: 겉과 속 (첫 번째 소제목) - 3~5문장으로 겉(주가·수급·시장 심리), 속(공시·재무의 질), 상대 비교(경쟁사)를 하나의 이야기로 엮습니다. 강세 논리와 약세 논리를 각각의 근거와 함께 제시하고, 현재 주가가 이미 반영한 것과 아직 반영하지 않았을 수 있는 것을 구분합니다.
2. 종합 투자 관점 - 기술적/기본적 분석을 종합한 투자 전망
3. 투자자 유형별 전략
   - 단기 트레이더 관점 (1개월 이내)
   - 스윙 트레이더 관점 (1-3개월)
   - 중기 투자자 관점 (3-12개월)
   - 장기 투자자 관점 (1년 이상)
   - 신규 진입자, 기존 보유자 각각의 관점 (비중 활용한 설명)
4. 주요 매매 포인트
   - 매수 고려 가격대 및 조건
   - 매도/손절 가격대 및 조건
   - 수익 실현 전략
5. 핵심 모니터링 요소
   - 주시해야 할 기술적 신호
   - 주목해야 할 실적 지표
   - 체크해야 할 뉴스 및 이벤트
   - 체크해야 할 시장 환경
6. 리스크 요소
   - 잠재적 하방 리스크
   - 상방 기회 요소
   - 리스크 관리 방안

## 작성 스타일
- 객관적인 데이터에 기반한 투자 견해 제시
- 확정적 예측보다는 조건부 시나리오 제시
- 다양한 투자 성향과 기간을 고려한 차별화된 전략 제공
- 구체적인 가격대와 실행 가능한 전략 제시
- 균형 잡힌 리스크-리워드 분석
- 회계·공시 용어는 처음 나올 때 괄호 안에 쉬운 말로 풀어 씁니다 (예: 전환사채(나중에 주식으로 바꿀 수 있는 채권))
- 보고서 본문은 반드시 높임말(합쇼체)로 작성 ('~입니다', '~합니다' 등). 반말('~한다', '~된다') 사용 금지.

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
- 첫 번째 소제목은 "#### 핵심 투자 논리: 겉과 속"이며, 이어서 종합 투자 관점, 투자자 유형별 전략, 주요 매매 포인트, 핵심 모니터링 요소, 리스크 요소, 결론 순서로 작성
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

## ⚠️ 글자수 제한: 반드시 3800자 이내로 작성하세요. 핵심만 간결하게!
"""
        else:  # English or other languages
            instruction = f"""You are an investment strategy expert. Synthesize the previously analyzed technical analysis, company information, financial analysis, news trends, and market analysis, plus the filing-depth analysis, competitor comparison table and official results/guidance when they are in the input, to present investment strategies and opinions.

**Always translate company names to {language_name}.** (e.g., "삼성전자" → "Samsung Electronics")

## Analysis Integration Elements
1. Stock Price/Volume Analysis Summary - Price trends, major support/resistance levels, volume patterns
2. Investor Trading Trends Analysis Summary - Institutional/foreign/retail trading patterns
3. Company Basic Information Summary - Core business model, competitiveness, growth drivers
4. News Analysis Summary - Major issues, market reactions, upcoming events
5. Market Analysis Summary - Market volatility factors, current status, trends, macroeconomic environment, technical analysis, market investment strategy
6. DART Filing-Depth Analysis (KR stocks, only when the chapter is present) - Earnings quality and one-off items, debt maturities and interest burden, dilution and capital changes, collateral/guarantees/contingent risks, related-party transactions
7. Competitor Comparison Table (only when present) - Relative growth, profitability and valuation versus the compared peers
8. Official Results and Guidance (US stocks, only when present) - Latest reported results, company guidance and how it changed
Use items 6-8 only when they are present in the input; if absent, do not mention or invent them.

## Investment Strategy Components
1. Core Investment Thesis: Surface vs. Substance (first sub-section) - In 3-5 sentences, weave the surface (price, flows, sentiment), the substance (filing and financial quality) and the relative view (peers) into one story. State the bull case and the bear case with the evidence behind each, and separate what the current price already reflects from what it may not.
2. Comprehensive Investment Perspective - Investment outlook combining technical/fundamental analysis
3. Strategies by Investor Type
   - Short-term trader perspective (within 1 month)
   - Swing trader perspective (1-3 months)
   - Mid-term investor perspective (3-12 months)
   - Long-term investor perspective (over 1 year)
   - Perspectives for new entrants and existing holders (explained using position sizing)
4. Key Trading Points
   - Buy consideration price range and conditions
   - Sell/stop-loss price range and conditions
   - Profit-taking strategy
5. Core Monitoring Elements
   - Technical signals to watch
   - Performance indicators to pay attention to
   - News and events to check
   - Market conditions to check
6. Risk Factors
   - Potential downside risks
   - Upside opportunity factors
   - Risk management measures

## Writing Style
- Present investment views based on objective data
- Present conditional scenarios rather than definitive predictions
- Provide differentiated strategies considering various investment preferences and timeframes
- Present specific price ranges and executable strategies
- Balanced risk-reward analysis
- Explain any accounting or filing term in plain words in parentheses the first time it appears (e.g., "convertible bond (a bond that can later be exchanged for shares)")

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
- The first sub-section is "#### Core Investment Thesis: Surface vs. Substance", followed in order by Comprehensive Investment Perspective, Strategies by Investor Type, Key Trading Points, Core Monitoring Elements, Risk Factors and Conclusion
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

## ⚠️ CHARACTER LIMIT: Keep the report under 3800 characters. Be concise and focus on key insights!
"""

        investment_strategy_agent = ReportAgent(
            name="investment_strategy_agent",
            instruction=instruction + report_narrative_contract(language) + synthesis_evidence_contract(language)
        )

        investment_strategy = await _generate_agent_text(
            investment_strategy_agent,
            message + report_time_contract(reference_date, language),
            max_tokens=32000,
            max_iterations=3,
        )
        logger.info(f"Completed investment_strategy - {len(investment_strategy)} characters")
        return investment_strategy
    except Exception as e:
        log_openai_error(logger, e, f"investment strategy generation for {company_name}")
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
