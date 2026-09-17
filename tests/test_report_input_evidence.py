"""Report input contracts only; no model calls or trading rule changes."""
from cores.agents import get_agent_directory
from cores.data_prefetch import _dict_to_markdown


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


def test_finality_contract_forbids_contradictory_caveat_but_keeps_historical_and_conditional_close():
    from cores.agents.report_agent import report_time_contract
    for language, forbidden, historical, conditional in (
        ("ko", "거래를 마쳤다", "과거 확정 일봉", "조건부 시나리오"),
        ("en", "closed at", "Confirmed historical closes", "conditional future scenarios"),
    ):
        contract = report_time_contract("20260917", language)
        assert forbidden in contract
        assert historical in contract
        assert conditional in contract
        assert ("단서를 붙여도" if language == "ko" else "does not repair") in contract


def test_us_ohlcv_input_carries_observation_not_invented_finality(monkeypatch):
    import importlib.util
    from pathlib import Path
    from types import SimpleNamespace

    import pandas as pd

    path = Path(__file__).resolve().parents[1] / "prism-us/cores/data_prefetch.py"
    spec = importlib.util.spec_from_file_location("us_prefetch_finality_contract", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    frame = pd.DataFrame({"close": [40.53], "volume": [49050000]},
                         index=pd.to_datetime(["2026-09-17"]))
    original = frame.copy(deep=True)
    monkeypatch.setattr(module, "_get_us_data_client", lambda: SimpleNamespace(
        get_ohlcv=lambda *a, **kw: frame.copy(deep=True)))
    result = module.prefetch_us_stock_ohlcv("SMCI")
    for text in ("source=yfinance", "fetched_at_utc=", "latest_row_date=2026-09-17",
                 "latest_row_finality=BAR_FINALITY_UNKNOWN", "not proof", "40.53", "49050000"):
        assert text in result
    pd.testing.assert_frame_equal(frame, original)
