import hashlib
import json

import pytest

from tools.evaluate_filing_extraction import (
    benchmark,
    load_source,
    normalize,
    pareto_frontier,
    score_records,
    source_bound_records,
)


def fact():
    return [{"id": "test", "weight": 3, "evidence_groups": [{
        "needles": ["재고 | 100", "평가손실 | 20"], "scope": "consolidated",
        "required_context": ["단위 : 천원", "2025년"],
    }]}]


def record(text, scope="consolidated", block_id="b1"):
    return {"text": text, "scope": scope, "block_id": block_id}


def test_full_group_has_values_context_and_scope():
    result = score_records(fact(), [record("2025년 단위 : 천원 재고 | 100 평가손실 | 20")])
    assert result["weighted_full_recall"] == 1


@pytest.mark.parametrize("text,scope", [
    ("2025년 단위 : 원 재고 | 100 평가손실 | 20", "consolidated"),
    ("2025년 단위 : 천원 재고 | 100 평가손실 | 20", "standalone"),
    ("2025년 재고 | 100 평가손실 | 20", "consolidated"),
    ("2025년 단위 : 천원 재고 | 100", "consolidated"),
])
def test_wrong_unit_scope_or_incomplete_cannot_get_full_credit(text, scope):
    assert score_records(fact(), [record(text, scope)])["full_items"] == 0


def test_scattered_matches_get_partial_only():
    result = score_records(fact(), [record("2025년 단위 : 천원 재고 | 100"),
        record("2025년 단위 : 천원 평가손실 | 20", block_id="b2")])
    assert result["full_items"] == 0
    assert result["partial_literal_recall"] == 1


def test_scope_free_content_metric_does_not_grant_full_credit():
    result = score_records(fact(), [record("2025년 단위 : 천원 재고 | 100 평가손실 | 20", "unknown")])
    assert result["full_items"] == 0
    assert result["context_complete_items_any_scope"] == 1


@pytest.mark.parametrize("number", ["1000", "100,000", "100.5"])
def test_numeric_suffix_does_not_match(number):
    facts = [{"id": "n", "evidence_groups": [{"needles": ["매출 " + "100"]}]}]
    assert score_records(facts, [record("매출 " + number)])["full_items"] == 0


def test_all_groups_required():
    facts = fact()
    facts[0]["evidence_groups"].append({"needles": ["전환 주체 자회사"], "scope": "consolidated"})
    assert score_records(facts, [record("2025년 단위 : 천원 재고 | 100 평가손실 | 20")])["full_items"] == 0


def test_presentation_normalization_keeps_semantics():
    assert normalize("**２０２５년**\n  단위 : 천원") == "2025년 단위 : 천원"
    assert normalize("(100)") != normalize("100")
    assert normalize("천원") != normalize("원")


def test_pareto_independent_by_document():
    rows = [{"document_id": doc, "method": "structured", "budget_bytes": size,
             "actual_payload_bytes": size, "weighted_full_recall": recall}
            for doc, size, recall in [("a", 100, .5), ("a", 200, .5), ("a", 300, 1), ("b", 500, .1)]]
    assert [(r["document_id"], r["actual_payload_bytes"]) for r in pareto_frontier(rows)] == [
        ("a", 100), ("a", 300), ("b", 500)]


def test_empty_evidence_does_not_get_full_credit():
    assert score_records([{"id": "empty", "evidence_groups": []}], [record("x")])["full_items"] == 0
    assert score_records([{"id": "empty", "evidence_groups": [{"needles": []}]}],
                         [record("x")])["full_items"] == 0


def test_benchmark_serializes_full_packet_and_repeats(tmp_path):
    text = "### 3. 연결재무제표 주석\n\n2025년 단위 : 천원 재고 | 100 평가손실 | 20"
    source = tmp_path / "source.json"
    source.write_text(json.dumps({"response": {"markdown": text}}))
    document = {"document_id": "fixture", "source_file": str(source), "facts": fact(),
                "markdown_sha256": hashlib.sha256(text.encode()).hexdigest()}
    calls = []

    def selector(text, **kwargs):
        calls.append(kwargs)
        return {"records": [{**record(text), "source_spans": [[0, len(text)]]}],
                "metadata": "counts toward payload budget"}

    report = benchmark([document], selector, [6000], 2)
    assert len(calls) == 6
    assert len(report["rows"]) == 3
    expected_bytes = len(json.dumps(selector(text), ensure_ascii=False, separators=(",", ":")).encode())
    for row in report["rows"]:
        assert row["actual_payload_bytes"] == expected_bytes
        assert row["weighted_full_recall"] == 1
        assert row["deterministic_repeats"]
        assert row["provider_calls"] == row["model_calls"] == 0


def test_source_hash_mismatch_stops_evaluation(tmp_path):
    source = tmp_path / "source.json"
    source.write_text(json.dumps({"response": {"markdown": "content"}}))
    with pytest.raises(ValueError, match="hash mismatch"):
        load_source({"source_file": str(source), "markdown_sha256": "incorrect"})


def test_source_bound_mixed_tables_cannot_borrow_other_period_or_unit(tmp_path):
    text = ("### 3. 연결재무제표 주석\n\n2025년 12월 31일 현재\n(단위: 천원)\n"
            "| 구분 | 금액 |\n| 재고 | 200 |\n\n2024년 12월 31일 현재\n(단위: 원)\n"
            "| 구분 | 금액 |\n| 재고 | 100 |\n")
    bundle = {**record(text), "source_spans": [[0, len(text)]]}
    facts = [{"id": "inventory", "evidence_groups": [{"needles": ["| 재고 | 100 |"],
              "required_context": ["2025년", "단위: 천원"], "scope": "consolidated"}]}]
    assert score_records(facts, [bundle])["full_items"] == 1  # old tagged-record metric
    assert score_records(facts, source_bound_records([bundle], text))["full_items"] == 0
    path = tmp_path / "mixed.json"
    path.write_text(json.dumps({"response": {"markdown": text}}))
    result = benchmark([{"document_id": "mixed", "source_file": str(path), "facts": facts}],
                       lambda text, **kwargs: {"records": [bundle]}, [6000], 1,
                       methods=("structured",))
    assert result["rows"][0]["full_items"] == 0
    assert result["metric_version"] == "source-unit-bound-v2"


def test_scope_must_match_actual_source_not_record_tag():
    text = "### 5. 재무제표 주석\n\n원문 100"
    forged = {**record(text), "source_spans": [[0, len(text)]]}
    with pytest.raises(ValueError, match="scope"):
        source_bound_records([forged], text)


@pytest.mark.parametrize("spans", [[[2, 5], [0, 2]], [[0, 4], [3, 5]], []])
def test_source_spans_must_be_ordered_nonoverlapping_and_present(spans):
    text = "plain original text"
    selected = "\n".join(text[a:b] for a, b in spans)
    with pytest.raises(ValueError, match="spans"):
        source_bound_records([{**record(selected, "unknown"), "source_spans": spans}], text)


def test_adjacent_prose_only_joins_same_path_scope_not_across_table():
    text = "### 3. 연결재무제표 주석\n\n첫 조건이다.\n\n둘째 조건이다.\n\n| 표 | 값 |\n| 수치 | 1 |\n\n셋째 조건이다."
    bundle = {**record(text), "source_spans": [[0, len(text)]]}
    units = source_bound_records([bundle], text)
    assert any("첫 조건" in r["text"] and "둘째 조건" in r["text"] for r in units)
    assert not any("둘째 조건" in r["text"] and "셋째 조건" in r["text"] for r in units)


def test_missing_selected_context_never_restored_from_source():
    text = "(단위: 천원)\n| 구분 | 값 |\n| 재고 | 100 |\n"
    start = text.index("| 재고")
    selection = {**record(text[start:], "unknown"), "source_spans": [[start, len(text)]]}
    units = source_bound_records([selection], text)
    assert units and all("단위" not in r["text"] for r in units)
