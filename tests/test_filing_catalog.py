"""Selection of normalized metadata, not proof of full provider coverage."""
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from itertools import permutations

import pytest

from prism_core.filing_catalog import FilingCandidate, select_periodic_filings

CUTOFF = datetime(2026, 9, 18, 6, 30, tzinfo=timezone.utc)


def annual(**overrides):
    row = FilingCandidate("annual", "KRX:327260", "https://example.com/annual", "annual",
                          date(2025, 1, 1), date(2025, 12, 31), "consolidated",
                          date(2026, 3, 18), True, None, "available")
    return replace(row, **overrides)


def interim(**overrides):
    row = replace(annual(), filing_id="half", source_url="https://example.com/half", kind="interim",
                  period_start=date(2026, 1, 1), period_end=date(2026, 6, 30),
                  published_at=date(2026, 8, 14))
    return replace(row, **overrides)


def select(rows, **overrides):
    args = {"entity_id": "KRX:327260", "scope": "consolidated", "decision_at": CUTOFF,
            "market_timezone": "Asia/Seoul", "listing_complete": True}
    args.update(overrides)
    return select_periodic_filings(rows, **args)


def test_half_is_base_and_annual_only_supplements():
    result = select([annual(), interim()])
    assert result["status"] == "SELECTED"
    assert result["primary_id"] == result["latest_candidate_id"] == "half"
    assert result["annual_supplement_id"] == "annual"
    assert result["latest_confirmed"] and not result["fact_validated"]
    assert result["publication_bounds"]["half"]["precision"] == "date"


def test_partial_listing_never_certifies_global_latest():
    result = select([annual(), interim()], listing_complete=False)
    assert result["primary_id"] == "half"
    assert result["status"] == "INCOMPLETE"
    assert result["latest_confirmed"] is False


def test_date_only_same_day_is_uncertain_not_assigned_midnight():
    result = select([annual(), interim(published_at=date(2026, 9, 18))])
    assert result["status"] == "INCOMPLETE"
    assert result["primary_id"] == "annual"  # Explicitly best-known, not certified latest.
    assert not result["latest_confirmed"]
    assert any(row["reason"] == "PUBLICATION_TIME_UNKNOWN" for row in result["exclusions"])


@pytest.mark.parametrize("value", [date(2026, 9, 19), datetime(2026, 9, 18, 7, tzinfo=timezone.utc)])
def test_future_publication_cannot_enter_past_decision(value):
    result = select([annual(), interim(published_at=value)])
    assert result["primary_id"] == "annual"
    assert result["latest_confirmed"]


def test_exact_cutoff_inclusive():
    assert select([interim(published_at=CUTOFF)])["primary_id"] == "half"


def test_date_only_upper_bound_is_market_midnight():
    bounds = select([interim()])["publication_bounds"]["half"]
    assert bounds["upper_exclusive"] is True
    assert bounds["upper"] == "2026-08-14T15:00:00+00:00"


@pytest.mark.parametrize("changes", [
    {"published_at": None}, {"published_at": CUTOFF.replace(tzinfo=None)},
    {"publication_verified": False}, {"publication_verified": 1},
    {"period_end": None}, {"scope": None}, {"scope": []}, {"scope": "unknown"},
    {"kind": "interim_report"}, {"source_url": "https://example.com/?token=private"},
    {"period_start": date(2027, 1, 1)}, {"body_status": "invented"},
])
def test_unresolved_metadata_does_not_fabricate_latest(changes):
    result = select([annual(), replace(interim(), **changes)])
    assert not result["latest_confirmed"]
    assert result["status"] == "INCOMPLETE"


def test_other_entity_and_scope_are_excluded():
    wrong = replace(interim(), entity_id="KRX:OTHER", filing_id="other")
    standalone = replace(interim(), scope="standalone", filing_id="standalone")
    result = select([annual(), wrong, standalone])
    assert result["primary_id"] == "annual" and result["latest_confirmed"]


@pytest.mark.parametrize("state", ["unavailable", "unread"])
def test_latest_body_failure_never_falls_back_to_annual(state):
    result = select([annual(), interim(body_status=state)])
    assert result["status"] == "LATEST_BODY_UNAVAILABLE"
    assert result["primary_id"] is None
    assert result["latest_candidate_id"] == "half"


def test_correction_of_old_annual_does_not_replace_newer_period():
    amended = replace(annual(), filing_id="annual2", amendment_of="annual",
                      published_at=date(2026, 9, 1))
    result = select([annual(), amended, interim()])
    assert result["primary_id"] == "half"
    assert result["annual_supplement_id"] == "annual2"


def test_current_corrected_edition_is_selected():
    fixed = replace(interim(), filing_id="half2", amendment_of="half", published_at=date(2026, 8, 20))
    result = select([interim(), fixed])
    assert result["primary_id"] == "half2"


def test_future_correction_is_ignored_not_backfilled():
    fixed = replace(interim(), filing_id="half2", amendment_of="half", published_at=date(2026, 9, 19))
    assert select([interim(), fixed])["primary_id"] == "half"


def test_unread_current_correction_does_not_revert_to_original():
    fixed = replace(interim(), filing_id="half2", amendment_of="half",
                    published_at=date(2026, 8, 20), body_status="unread")
    result = select([interim(), fixed])
    assert result["status"] == "LATEST_BODY_UNAVAILABLE" and result["primary_id"] is None
    assert result["latest_candidate_id"] == "half2"


def test_undated_current_correction_blocks_original():
    fixed = replace(interim(), filing_id="half2", amendment_of="half", published_at=None)
    result = select([interim(), fixed])
    assert result["status"] == "UNRESOLVED_CURRENT_EDITION"
    assert result["primary_id"] is None and "half2" in result["blocked_by"]


def test_unrelated_old_undated_correction_keeps_best_known_half():
    fixed = replace(annual(), filing_id="annual2", amendment_of="annual", published_at=None)
    result = select([annual(), fixed, interim()])
    assert result["status"] == "INCOMPLETE" and result["primary_id"] == "half"
    assert result["annual_supplement_id"] is None


def test_sibling_amendments_are_not_arbitrarily_combined():
    first = replace(interim(), filing_id="half2", amendment_of="half", published_at=date(2026, 8, 20))
    second = replace(first, filing_id="half3", published_at=date(2026, 8, 21))
    result = select([interim(), first, second])
    assert result["status"] == "AMBIGUOUS" and result["primary_id"] is None


def test_explicit_correction_chain_and_permutation_invariance():
    first = replace(interim(), filing_id="half2", amendment_of="half", published_at=date(2026, 8, 20))
    second = replace(first, filing_id="half3", amendment_of="half2", published_at=date(2026, 8, 21))
    results = [select(list(rows)) for rows in permutations([interim(), first, second])]
    assert all(result == results[0] for result in results)
    assert results[0]["primary_id"] == "half3"


def test_same_period_unrelated_reports_are_ambiguous():
    result = select([interim(), replace(interim(), filing_id="different")])
    assert result["status"] == "AMBIGUOUS" and result["primary_id"] is None


def test_duplicate_exact_is_idempotent_but_conflicting_id_blocks():
    assert select([interim(), interim()]) == select([interim()])
    result = select([interim(), interim(body_status="unavailable")])
    assert result["status"] == "AMBIGUOUS"


def test_orphan_correction_is_not_selected():
    result = select([replace(interim(), amendment_of="missing")])
    assert result["primary_id"] is None and not result["latest_confirmed"]


def test_cycle_cannot_loop_or_select():
    first = replace(interim(), amendment_of="half2")
    second = replace(first, filing_id="half2", amendment_of="half")
    assert select([first, second])["primary_id"] is None


def test_cross_scope_correction_does_not_replace():
    fixed = replace(interim(), filing_id="half2", amendment_of="half",
                    period_end=date(2026, 3, 31), published_at=date(2026, 8, 20))
    result = select([interim(), fixed])
    assert not result["latest_confirmed"] and result["primary_id"] is None


def test_known_other_scope_correction_cannot_certify_old_current_body():
    fixed = replace(interim(), filing_id="half2", amendment_of="half", scope="standalone",
                    published_at=date(2026, 8, 20))
    result = select([interim(), fixed])
    assert result["status"] == "UNRESOLVED_CURRENT_EDITION" and result["primary_id"] is None


def test_other_entity_linked_correction_is_conflict_not_unrelated():
    fixed = replace(interim(), filing_id="half2", amendment_of="half", entity_id="KRX:OTHER",
                    published_at=date(2026, 8, 20))
    result = select([interim(), fixed])
    assert result["status"] == "UNRESOLVED_CURRENT_EDITION" and result["primary_id"] is None


def test_future_linked_bad_scope_correction_does_not_block_past():
    fixed = replace(interim(), filing_id="half2", amendment_of="half", scope=[],
                    published_at=date(2026, 9, 19))
    result = select([interim(), fixed])
    assert result["primary_id"] == "half" and result["latest_confirmed"]


@pytest.mark.parametrize("value", [datetime.min.replace(tzinfo=timezone(timedelta(hours=1))),
                                  datetime.max.replace(tzinfo=timezone(timedelta(hours=-1)))])
def test_unrepresentable_publication_is_unresolved_not_crash(value):
    result = select([annual(), interim(published_at=value)])
    assert not result["latest_confirmed"]


def test_unrepresentable_cutoff_raises_policy_error():
    with pytest.raises(ValueError):
        select([], decision_at=datetime.min.replace(tzinfo=timezone(timedelta(hours=1))))


def test_known_nonperiodic_event_not_selected_as_current_financials():
    result = select([annual(), interim(kind="press_release")])
    assert result["primary_id"] == "annual" and result["latest_confirmed"]


@pytest.mark.parametrize("args", [
    {"decision_at": CUTOFF.replace(tzinfo=None)}, {"listing_complete": 1},
    {"entity_id": ""}, {"scope": "unknown"}, {"market_timezone": "unknown/timezone"},
])
def test_bad_policy_rejected(args):
    with pytest.raises(ValueError):
        select([], **args)


def test_empty_result_never_claims_latest():
    result = select([])
    assert result["status"] == "NO_ELIGIBLE" and not result["latest_confirmed"]
