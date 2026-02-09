def get_agent_directory(company_name, company_code, reference_date, base_sections, language: str = "ko", prefetched_data: dict = None):
    """
    Return agent directory for each section

    Args:
        company_name: Company name
        company_code: Stock code
        reference_date: Analysis reference date (YYYYMMDD)
        base_sections: List of agent sections to create
        language: Language code ("ko" or "en")
        prefetched_data: Dictionary of prefetched data for agents (optional)

    Returns:
        Dict[str, Agent]: Agent dictionary with section names as keys
    """
    from cores.agents.stock_price_agents import (
        create_price_volume_analysis_agent,
        create_investor_trading_analysis_agent
    )
    from cores.agents.company_info_agents import (
        create_company_status_agent,
        create_company_overview_agent
    )
    from cores.agents.news_strategy_agents import (
        create_news_analysis_agent
    )
    from cores.agents.market_index_agents import (
        create_market_index_analysis_agent
    )
    from cores.agents.trading_agents import (
        create_trading_scenario_agent,
        create_sell_decision_agent
    )
    from cores.utils import get_wise_report_url

    # Create URL mapping
    urls = {k: get_wise_report_url(k, company_code) for k in [
        "기업현황", "기업개요", "재무분석", "투자지표",
        "컨센서스", "경쟁사분석", "지분현황", "업종분석", "최근리포트"
    ]}

    # Date calculation (limited to 1 year to reduce input tokens - sufficient for trading analysis)
    from datetime import datetime, timedelta
    ref_date = datetime.strptime(reference_date, "%Y%m%d")
    max_years = 1
    max_years_ago = (ref_date - timedelta(days=365*max_years)).strftime("%Y%m%d")

    # Extract prefetched data for each agent
    pf = prefetched_data or {}

    agent_creators = {
        "price_volume_analysis": lambda: create_price_volume_analysis_agent(
            company_name, company_code, reference_date, max_years_ago, max_years, language,
            prefetched_data=pf.get("stock_ohlcv")
        ),
        "investor_trading_analysis": lambda: create_investor_trading_analysis_agent(
            company_name, company_code, reference_date, max_years_ago, max_years, language,
            prefetched_data=pf.get("trading_volume")
        ),
        "company_status": lambda: create_company_status_agent(
            company_name, company_code, reference_date, urls, language
        ),
        "company_overview": lambda: create_company_overview_agent(
            company_name, company_code, reference_date, urls, language
        ),
        "news_analysis": lambda: create_news_analysis_agent(
            company_name, company_code, reference_date, language
        ),
        "market_index_analysis": lambda: create_market_index_analysis_agent(
            reference_date, max_years_ago, max_years, language,
            prefetched_kospi=pf.get("kospi_index"),
            prefetched_kosdaq=pf.get("kosdaq_index")
        )
    }
    
    agents = {}
    for section in base_sections:
        if section in agent_creators:
            agents[section] = agent_creators[section]()
    
    return agents
