import json

from prism_core.search_discovery_fallback import discovery_fallback


def extract(texts=(), structured=None, size=10000):
    return discovery_fallback(texts, structured, size=size, digest="a" * 64)


def test_duplicated_response_is_locators_only():
    prose = "UNVERIFIED causal claim 2026-09-20\n[1] https://example.com/issuer\n[2] https://example.com/wrong-company"
    result = extract([prose], {"response": prose})
    assert result["sources"] == [{"reference": "1", "url": "https://example.com/issuer"},
                                  {"reference": "2", "url": "https://example.com/wrong-company"}]
    assert result["issuer_match"] == result["publication_time"] == "UNKNOWN"
    assert result["source_content_verified"] is False
    assert result["representation_ambiguity"] is False
    assert "UNVERIFIED" not in json.dumps(result)


def test_filter_dedupe_and_conflicting_representations():
    result = extract([json.dumps({"citations": ["https://example.com/a", "https://127.0.0.1/a",
                                               "https://example.com/a?token=SECRET"]})],
                     {"search_results": [{"url": "https://example.com/b", "title": "SECRET", "date": "today"},
                                         {"url": "https://example.com/a"}]})
    assert result["filtered_count"] == 2
    assert len(result["sources"]) == 2
    assert result["representation_ambiguity"] is True
    assert "SECRET" not in json.dumps(result)


def test_bounds_and_terminal_references_only():
    assert extract(["x" * 65537]) is None
    assert extract(["x"] * 5) is None
    assert extract(size=131073) is None
    assert extract(["[1] https://example.com/a\nnot a terminal reference block"]) is None
    assert extract(["\n" * 1000 + "[1] https://example.com/a"]) is None
    assert extract([], {"nested": {"citations": ["https://example.com/a"]}}) is None
    result = extract([], {"citations": [f"https://example.com/{i}" for i in range(60)]})
    assert len(result["sources"]) == 10
    assert result["candidates_seen"] == 50
    assert result["truncated"] is True


def test_reference_conflict_is_not_claim_agreement():
    result = extract(["[1] https://example.com/a\n[1] https://example.com/b"])
    assert len(result["sources"]) == 2
    assert result["issuer_match"] == "UNKNOWN"


def test_conflicting_reference_mappings_mark_representation_ambiguity():
    result = extract(["[1] https://example.com/a\n[2] https://example.com/b"],
                     {"response": "[2] https://example.com/a\n[1] https://example.com/b"})
    assert result["representation_ambiguity"] is True
    assert result["sources"] == [{"reference": "1", "url": "https://example.com/a"},
                                  {"reference": "2", "url": "https://example.com/b"}]
    assert result["issuer_match"] == "UNKNOWN"


def test_json_response_and_untrusted_nested_shapes():
    result = extract([json.dumps({"response": "not evidence\n[7] https://example.com/a"})])
    assert result["sources"] == [{"reference": "7", "url": "https://example.com/a"}]
    nested = json.dumps({"response": json.dumps({"citations": ["https://example.com/a"]})})
    assert extract([nested]) is None


def test_duplicate_urls_and_explicit_metadata_omitted():
    result = extract([], {"search_results": [{"url": "https://example.com/a", "title": "HIDDEN", "date": "2026"}],
                          "citations": ["https://example.com/a", None, {}, "http://example.com/a",
                                        "https://user:pass@example.com/a", "https://example.local/a"]})
    assert len(result["sources"]) == 1
    assert result["filtered_count"] == 5
    assert "HIDDEN" not in json.dumps(result)


def test_recognizable_provider_errors_fail_closed_and_total_line_limit():
    for flag in ({"success": False}, {"isError": True}, {"error": "failure"}):
        value = {**flag, "citations": ["https://example.com/a"]}
        assert extract([], value) is None
        assert extract([json.dumps(value)]) is None
    assert extract(["\n" * 2000 + "[1] https://example.com/a"]) is None
    assert extract(["first\n" * 600, "second\n" * 500 + "[1] https://example.com/a"]) is None
