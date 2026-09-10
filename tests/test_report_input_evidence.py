"""Report input contracts only; no model calls or trading rule changes."""
from cores.data_prefetch import _dict_to_markdown
from cores.agents import get_agent_directory


def test_metadata_without_note_keeps_source_time_and_status():
    text = _dict_to_markdown({"2026-09-10": {"Close": 27200}, "__meta__": {
        "data_status": "intraday_estimate", "as_of": "2026-09-10T14:50:00+09:00",
        "source": "registered_quote_source", "secret": "DO_NOT_RENDER"}})
    assert "intraday_estimate" in text
    assert "2026-09-10T14:50:00+09:00" in text
    assert "registered_quote_source" in text
    assert "DO_NOT_RENDER" not in text


def test_dated_rows_are_chronological_without_changing_prices():
    text = _dict_to_markdown({"2026-09-10": {"Close": 27200}, "2026-09-09": {"Close": 24700}})
    assert text.index("2026-09-09") < text.index("2026-09-10")
    assert "27200" in text and "24700" in text


def test_existing_ohlcv_reaches_investor_analysis_without_extra_tools():
    agents = get_agent_directory("HDC", "012630", "20260910", ["investor_trading_analysis"],
        prefetched_data={"stock_ohlcv": "PRICE_SERIES_CANARY", "trading_volume": "FLOW_SERIES_CANARY"})
    agent = agents["investor_trading_analysis"]
    assert "PRICE_SERIES_CANARY" in agent.instruction
    assert "FLOW_SERIES_CANARY" in agent.instruction
    assert tuple(agent.server_names) == ()
    assert "동일 날짜" in agent.instruction


def test_all_report_sections_get_current_row_finality_contract():
    for language in ("ko", "en"):
        agents = get_agent_directory("HDC", "012630", "20260910",
            ["price_volume_analysis", "investor_trading_analysis", "company_status",
             "company_overview", "news_analysis", "market_index_analysis"], language,
            prefetched_data={"stock_ohlcv": "prices", "trading_volume": "flows"})
        for agent in agents.values():
            assert "20260910" in agent.instruction
            assert "BAR_FINALITY_UNKNOWN" in agent.instruction
