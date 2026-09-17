"""Offline fixtures: a successful source match must never imply truth."""
import json

import pytest

from prism_core.competitive_prefetch import build_packet


@pytest.fixture
def inputs():
    return (
        {"id": "case", "entity": "A", "asof": "2026-09-17T12:00:00Z"},
        [{"source_id": "s1", "provider": "firecrawl", "url": "https://example.com/ir",
          "fetched_at": "2026-09-17T10:00:00Z", "success": True,
          "text": "Quarterly revenue was USD 20 million.", "usage": None}],
        [{"id": "c1", "entity": "A", "peer_universe": ["A", "B"],
          "metric": "revenue", "period": "2026Q2", "unit": "USD million",
          "value": 20, "actual_or_estimate": "actual", "source_id": "s1",
          "excerpt": "revenue was USD 20 million."}],
    )


def test_match_not_truth(inputs):
    packet = build_packet(*inputs)
    claim = packet["claims"][0]
    assert claim["provenance_status"] == "SOURCE_TEXT_MATCHED_NOT_FACT_VALIDATED"
    assert claim["fact_status"] == claim["comparability"] == "UNKNOWN"
    assert inputs[1][0]["text"] not in packet["context"]
    assert packet["sources"][0]["publication_date"] == "UNKNOWN"
    assert packet["sources"][0]["usage"] is None


def test_fake_checked_label_ignored(inputs):
    inputs[2][0].update(excerpt="invented", status="SOURCE_CHECKED", verified=True)
    assert build_packet(*inputs)["claims"][0]["provenance_status"] == "UNKNOWN"


def test_quote_normalization(inputs):
    inputs[2][0]["excerpt"] = "revenue\nwas ＵＳＤ  20 million."
    assert build_packet(*inputs)["claims"][0]["context_included"]


@pytest.mark.parametrize("field,value,flag", [
    ("success", False, "RETRIEVAL_FAILED_OR_EMPTY"),
    ("text", "", "RETRIEVAL_FAILED_OR_EMPTY"),
    ("publication_date", "2026-09-18", "FUTURE_PUBLICATION"),
    ("fetched_at", "2026-09-18T00:00:00Z", "INVALID_FETCH_ASOF"),
    ("publication_date", "bad", "INVALID_PUBLICATION_DATE"),
    ("paywall", True, "BLOCKED_SOURCE"),
    ("text", "자료를 요청 중입니다 <img src='ajax-loader.gif'>", "BLOCKED_SOURCE"),
])
def test_bad_receipt(inputs, field, value, flag):
    inputs[1][0][field] = value
    claim = build_packet(*inputs)["claims"][0]
    assert flag in claim["flags"]
    assert claim["provenance_status"] == "UNKNOWN"


def test_credentials_not_forwarded(inputs):
    inputs[1][0]["url"] = "https://user:secret@example.com/ir?token=hidden"
    packet = build_packet(*inputs)
    assert "secret" not in json.dumps(packet)
    assert "hidden" not in json.dumps(packet)
    assert packet["sources"][0]["url"] is None


def test_query_tokens_stripped(inputs):
    inputs[1][0]["url"] += "?token=secret#hidden"
    assert build_packet(*inputs)["sources"][0]["url"] == "https://example.com/ir"


def test_actual_estimate_and_peer_unknown(inputs):
    inputs[2].append(dict(inputs[2][0], id="c2", entity="B", actual_or_estimate="estimate"))
    claims = build_packet(*inputs)["claims"]
    assert [c["actual_or_estimate"] for c in claims] == ["actual", "estimate"]
    assert "ENTITY_ALIGNMENT_UNCHECKED" in claims[1]["flags"]
    assert all(c["peer_universe_status"] == "UNKNOWN" for c in claims)
    assert all(c["source_subject_status"] == "UNKNOWN" for c in claims)


def test_duplicates_preserved_but_ambiguous(inputs):
    inputs[2].append(dict(inputs[2][0], value=999))
    packet = build_packet(*inputs)
    assert packet["counts"]["claims"] == 2
    assert all("AMBIGUOUS_CLAIM_ID" in c["flags"] for c in packet["claims"])


def test_duplicate_requests_not_fake_cache_savings(inputs):
    inputs[1].append(dict(inputs[1][0], source_id="s2"))
    packet = build_packet(*inputs)
    assert packet["counts"]["unique_requests"] == 1
    assert packet["counts"]["duplicate_requests"] == 1
    assert packet["cost"]["cache_savings"] is None


def test_duplicate_sources_ambiguous(inputs):
    inputs[1].append(dict(inputs[1][0]))
    assert "AMBIGUOUS_SOURCE_ID" in build_packet(*inputs)["claims"][0]["flags"]


@pytest.mark.parametrize("budget", [0, 10, 300, 750, 6000])
def test_budget_preserves_denominator(inputs, budget):
    inputs[2].append(dict(inputs[2][0], id="c2"))
    packet = build_packet(*inputs, max_context_chars=budget)
    assert len(packet["context"]) <= budget
    assert [c["id"] for c in packet["claims"]] == ["c1", "c2"]
    assert all(c["context_included"] or c["context_omission_reason"] for c in packet["claims"])


def test_hash_stable_and_input_unchanged(inputs):
    before = json.dumps(inputs)
    assert build_packet(*inputs) == build_packet(*inputs)
    assert json.dumps(inputs) == before


def test_invalid_asof_rejected(inputs):
    inputs[0]["asof"] = "2026-09-17"
    with pytest.raises(ValueError, match="timezone-aware"):
        build_packet(*inputs)


def test_wrong_entity_and_value_are_never_flat_facts(inputs):
    inputs[2][0].update(entity="WrongCompany", value=999)
    context = json.loads(build_packet(*inputs)["context"])
    claim = context["claims"][0]
    assert "entity" not in claim and "value" not in claim
    assert claim["unverified_claim"]["value"] == 999
    assert claim["unverified_claim"]["entity"] == "WrongCompany"
    assert claim["retrieved_excerpt"] == "revenue was USD 20 million."
    assert claim["semantic_validation"].startswith("NOT_PERFORMED")


def test_model_receives_dates_source_and_redaction_notice(inputs):
    inputs[1][0]["url"] = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=1234"
    context = json.loads(build_packet(*inputs)["context"])
    source = context["sources"]["s1"]
    assert source["url_redacted"] is True
    assert source["url_role"] == "REDACTED_LOCATOR_NOT_EXACT_SOURCE"
    assert source["publication_date"] == "UNKNOWN"
    assert source["fetched_at"] == "2026-09-17T10:00:00+00:00"
    assert len(source["request_key"]) == 64


def test_all_gap_ids_and_reasons_visible_to_model(inputs):
    inputs[2].append(dict(inputs[2][0], id="missing", excerpt="invented"))
    context = json.loads(build_packet(*inputs)["context"])
    assert context["omitted_claims"][0]["id"] == "missing"
    assert context["omitted_claims"][0]["reason"] == "UNSUPPORTED_PROVENANCE"
    packet = build_packet(*inputs, max_context_chars=700)
    context = json.loads(packet["context"])
    assert not context["claims"]
    assert [gap["id"] for gap in context["omitted_claims"]] == ["c1", "missing"]
    assert context["omitted_claims"][0]["reason"] == "BUDGET"
    too_small = build_packet(*inputs, max_context_chars=30)
    assert too_small["context"] == ""
    assert too_small["context_budget_status"] == "BELOW_MINIMUM"
