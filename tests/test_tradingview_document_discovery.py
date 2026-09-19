"""Saved provider shape, synthetic identifiers: discovery is not filing proof."""
from prism_core.tradingview_evidence import normalize_evidence


def document():
    return {"id": "synthetic:report:1", "category": {"id": "interim_report", "title": "Interim report"},
            "fiscal_year": 2026, "fiscal_period": "Q2", "reported": 1785373200,
            "status": "usable", "symbols": [{"symbol": "KRX:005930"}],
            "views": [{"id": "synthetic:view:exact", "type": "pdf"}]}


def test_metadata_survives_without_inventing_publication_or_calendar_period():
    result = normalize_evidence("get_documents", {"items": [document()], "total": 152},
                                requested_symbols=["KRX:005930"])
    facts = result["facts"]
    row = facts["rows"][0]
    assert row["category"]["id"] == "interim_report"
    assert row["fiscal_year"] == 2026 and row["fiscal_period"] == "Q2"
    assert row["reported"] == 1785373200
    assert row["symbols"] == ["KRX:005930"]
    assert row["views"][0]["id"] == "synthetic:view:exact"
    assert row["provider_status"] == "usable"
    assert facts["provider_total"] == 152
    assert facts["missing_symbols"] == []
    assert result["status"] == "PARTIAL"
    assert facts["discovery_only"] and facts["official_listing_complete"] is False
    assert facts["publication_basis"] == "UNVERIFIED_PROVIDER_METADATA"
    assert "published_at" not in row and "period_end" not in row


def test_total_does_not_certify_official_catalog_completeness():
    facts = normalize_evidence("get_documents", {"items": [document()], "total": 1})["facts"]
    assert facts["provider_total"] == 1 and not facts["official_listing_complete"]


def test_nested_membership_keeps_missing_entity_unknown():
    result = normalize_evidence("get_documents", {"items": [document()], "total": 1},
                                requested_symbols=["KRX:005930", "NASDAQ:MSFT"])
    assert result["facts"]["observed_symbols"] == ["KRX:005930"]
    assert result["facts"]["missing_symbols"] == ["NASDAQ:MSFT"]


def test_bad_fiscal_field_does_not_become_a_period():
    row = document()
    row.update(fiscal_year=True, fiscal_period={"date": "invented"})
    result = normalize_evidence("get_documents", {"items": [row]})
    assert "fiscal_year" not in result["facts"]["rows"][0]
    assert "fiscal_period" not in result["facts"]["rows"][0]


def test_empty_discovery_preserves_total_and_missing_coverage():
    result = normalize_evidence("get_documents", {"items": [], "total": 12},
                                requested_symbols=["KRX:005930"])
    assert result["status"] == "EMPTY"
    assert result["facts"]["provider_total"] == 12
    assert result["facts"]["missing_symbols"] == ["KRX:005930"]
    assert not result["facts"]["official_listing_complete"]


def test_empty_discovery_containers_are_not_evidence():
    for row in ({"category": {}}, {"symbols": []}, {"symbols": [{"symbol": ""}]},
                {"symbols": [{}] * 101}, {"status": "usable"}, {"views": []},
                {"views": [{}]}, {"provider": {}}, {"id": ""}, {"id": "  "}):
        result = normalize_evidence("get_documents", {"items": [row]})
        assert not result["usable_for_report"]
