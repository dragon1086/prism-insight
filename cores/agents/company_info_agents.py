from mcp_agent.agents.agent import Agent
from cores.language_config import Language
from cores.agents.prompt_templates import PromptTemplates


def create_company_status_agent(company_name, company_code, reference_date, urls, language: str = "ko"):
    """
    기업 현황 분석 에이전트 생성

    Args:
        company_name: 기업명
        company_code: 종목 코드
        reference_date: 분석 기준일 (YYYYMMDD)
        urls: WiseReport URL 딕셔너리
        language: Language code ("ko" or "en")

    Returns:
        Agent: 기업 현황 분석 에이전트
    """
    lang = Language(language)
    instruction = PromptTemplates.get_company_status_prompt(
        company_name, company_code, reference_date, urls, lang
    )

    return Agent(
        name="company_status_agent",
        instruction=instruction,
        server_names=["firecrawl"]
    )


def create_company_overview_agent(company_name, company_code, reference_date, urls, language: str = "ko"):
    """
    기업 개요 분석 에이전트 생성

    Args:
        company_name: 기업명
        company_code: 종목 코드
        reference_date: 분석 기준일 (YYYYMMDD)
        urls: WiseReport URL 딕셔너리
        language: Language code ("ko" or "en")

    Returns:
        Agent: 기업 개요 분석 에이전트
    """
    lang = Language(language)
    instruction = PromptTemplates.get_company_overview_prompt(
        company_name, company_code, reference_date, urls, lang
    )

    return Agent(
        name="company_overview_agent",
        instruction=instruction,
        server_names=["firecrawl"]
    )
