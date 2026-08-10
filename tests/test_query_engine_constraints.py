import pytest

from cores.archive import query_engine as qe


@pytest.mark.asyncio
async def test_retrieve_applies_date_constraint_to_fts_hits(monkeypatch):
    old_fts = {
        "id": 1, "ticker": "MU", "company_name": "Micron",
        "report_date": "2026-07-24", "market": "us", "mode": "analysis",
        "snippet": "old but text-relevant",
    }
    exact_date = {
        "id": 2, "ticker": "MU", "company_name": "Micron",
        "report_date": "2026-08-10", "market": "us", "mode": "analysis",
        "snippet": "exact date",
    }

    async def fake_search(*args, **kwargs):
        return [old_fts, exact_date]

    async def fake_structured(*args, **kwargs):
        return [exact_date]

    async def fake_empty(*args, **kwargs):
        return {}

    monkeypatch.setattr(qe, "search_fts", fake_search)
    monkeypatch.setattr(qe, "get_report_ids", fake_structured)
    monkeypatch.setattr(qe, "_fetch_enrichments", fake_empty)
    monkeypatch.setattr(qe, "_fetch_content_excerpts", fake_empty)

    reports = await qe.QueryEngine(db_path=":memory:").retrieve(
        text="MU", market="us", ticker=None,
        date_from="2026-08-10", date_to="2026-08-10",
    )

    assert [report.report_id for report in reports] == [2]
