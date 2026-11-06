import asyncio

from prism_core.sector_names import fill_missing_sector


def _fill(scenario, lookup):
    return asyncio.run(fill_missing_sector(scenario, lookup))


def test_missing_sector_is_filled_from_system_lookup():
    # MSFT 2026-11-05: the BUY model left "sector" out and the message said "업종 미확인".
    out = _fill({"decision": "진입"}, lambda: "Technology")
    assert out["sector"] == "Technology" and out["_sector_source"] == "system"
    assert _fill({"sector": "알 수 없음"}, lambda: "전기·전자")["sector"] == "전기·전자"


def test_model_sector_wins_and_failures_stay_unknown():
    assert _fill({"sector": "Energy"}, lambda: "Technology") == {"sector": "Energy"}
    assert _fill({"sector": "Unknown"}, lambda: 1 / 0) == {"sector": "Unknown"}
    assert _fill({}, lambda: "") == {}
    failed = {"sector": "Unknown", "analysis_failed": True}
    assert _fill(failed, lambda: "Technology")["sector"] == "Unknown"
