from datetime import datetime
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest

from prism_app import live_data_uat
from prism_app.live_data_uat import run_kr_live_data_uat
from prism_core.data import SecurityId
from prism_core.data.providers.kis import KISInstrument, KISMarketDataProvider
from tests.features.test_market_inputs import HistoricalTransport


KST = ZoneInfo("Asia/Seoul")
AS_OF = datetime(2026, 7, 24, 18, 0, tzinfo=KST)
STOCK_ID = SecurityId(value=UUID("00000000-0000-0000-0000-000000000081"))
BENCHMARK_ID = SecurityId(value=UUID("00000000-0000-0000-0000-000000000082"))


@pytest.mark.asyncio
async def test_live_data_uat_exposes_real_gate_and_never_claims_llm_or_broker_execution() -> None:
    provider = KISMarketDataProvider(
        transport=HistoricalTransport(),
        instruments=(
            KISInstrument(security_id=STOCK_ID, kis_symbol="005930"),
            KISInstrument(security_id=BENCHMARK_ID, kis_symbol="069500"),
        ),
        clock=lambda: datetime(2026, 7, 24, 18, 1, tzinfo=KST),
    )

    result = await run_kr_live_data_uat(
        provider=provider,
        stock_id=STOCK_ID,
        benchmark_id=BENCHMARK_ID,
        stock_symbol="005930",
        benchmark_symbol="069500",
        as_of=AS_OF,
    )

    assert result["stage"] == "PHASE1_LIVE_DATA_UAT"
    assert result["provider"] == "KIS"
    assert result["provider_quality"] == "FRESH"
    assert result["aligned_sessions"] == 25
    assert result["quality_disposition"] == "REJECT"
    assert result["decision"] is None
    assert result["scenario_state"] == "ANALYSIS_INCOMPLETE"
    assert result["llm_called"] is False
    assert result["broker_called"] is False
    assert result["missing_inputs"] == ["catalyst_evidence", "regime_observation"]
    assert result["uat_passed"] is False
    assert "credentials" not in str(result).lower()


@pytest.mark.asyncio
async def test_live_data_uat_redacts_provider_failure_without_inventing_no_entry() -> None:
    class FailingProvider:
        async def fetch_result(self, **kwargs):
            raise RuntimeError("secret provider response detail")

    result = await run_kr_live_data_uat(
        provider=FailingProvider(),
        stock_id=STOCK_ID,
        benchmark_id=BENCHMARK_ID,
        stock_symbol="005930",
        benchmark_symbol="069500",
        as_of=AS_OF,
    )

    assert result["provider_quality"] == "UNAVAILABLE"
    assert result["quality_disposition"] == "REJECT"
    assert result["decision"] is None
    assert result["scenario_state"] == "DATA_UNAVAILABLE"
    assert result["provider_error_type"] == "RuntimeError"
    assert "secret provider response detail" not in str(result)
    assert result["llm_called"] is False
    assert result["broker_called"] is False


@pytest.mark.asyncio
async def test_live_data_uat_redacts_normalization_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = KISMarketDataProvider(
        transport=HistoricalTransport(),
        instruments=(
            KISInstrument(security_id=STOCK_ID, kis_symbol="005930"),
            KISInstrument(security_id=BENCHMARK_ID, kis_symbol="069500"),
        ),
        clock=lambda: datetime(2026, 7, 24, 18, 1, tzinfo=KST),
    )

    def fail_normalization(**kwargs):
        raise ValueError("sensitive malformed row detail")

    monkeypatch.setattr(
        live_data_uat, "build_feature_computation_input", fail_normalization
    )
    result = await run_kr_live_data_uat(
        provider=provider,
        stock_id=STOCK_ID,
        benchmark_id=BENCHMARK_ID,
        stock_symbol="005930",
        benchmark_symbol="069500",
        as_of=AS_OF,
    )

    assert result["provider_quality"] == "UNAVAILABLE"
    assert result["provider_error_type"] == "ValueError"
    assert result["decision"] is None
    assert result["scenario_state"] == "DATA_UNAVAILABLE"
    assert "sensitive malformed row detail" not in str(result)


def test_live_data_cli_returns_nonzero_when_uat_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def rejected(**kwargs):
        return {"uat_passed": False, "decision": "NO_ENTRY"}

    monkeypatch.setattr(live_data_uat, "run_kr_live_data_uat", rejected)
    monkeypatch.setattr(
        live_data_uat,
        "_build_live_provider",
        lambda **kwargs: (object(), STOCK_ID, BENCHMARK_ID),
    )

    assert live_data_uat.main([]) == 2


def test_live_data_provider_reuses_secure_cross_process_token_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class CapturingTransport:
        def __init__(self, **kwargs) -> None:
            captured.update(kwargs)

    class CapturingProvider:
        def __init__(self, **kwargs) -> None:
            captured["provider"] = kwargs

    monkeypatch.setenv("KIS_APP_KEY", "fixture-app-key")
    monkeypatch.setenv("KIS_APP_SECRET", "fixture-app-secret")
    monkeypatch.setattr(live_data_uat, "KISHTTPTransport", CapturingTransport)
    monkeypatch.setattr(live_data_uat, "KISMarketDataProvider", CapturingProvider)

    live_data_uat._build_live_provider(
        stock_symbol="005930",
        benchmark_symbol="069500",
        lookback_calendar_days=60,
    )

    assert type(captured["token_cache"]).__name__ == "SecureFileKISTokenCache"
    assert "kis-market-data-token.json" in repr(captured["token_cache"])
