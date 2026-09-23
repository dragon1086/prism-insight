from prism_core.filing_structure import parse_filing
from prism_core.filing_table_projection import project_table


def fixture(extra="", header=True, unit=True):
    return (("(단위: 천원)\n" if unit else "")
            + ("| 구분 | 당기 | 전기 |\n| --- | --- | --- |\n" if header else "")
            + extra + "| 영업현금흐름 | 100 | 90 |\n"
            + "".join(f"| 기타항목{i} | {i+1} | {i+2} |\n" for i in range(30))
            + "| 합계 | 400 | 300 |\n\n(주1) 금액은 담보 제공분을 포함합니다.\n")


def block(text):
    return next(b for b in parse_filing(text) if b["kind"] == "table")


def test_projection_retains_header_unit_notes_and_exact_spans():
    text = fixture()
    original = block(text)
    result = project_table(original, text, 330)
    assert result and result["projected"]
    assert len(result["text"].encode()) <= 330
    for expected in ("(단위: 천원)", "| 구분 | 당기 | 전기 |", "(주1)", "영업현금흐름", "합계"):
        assert expected in result["text"]
    assert result["text"] == "\n".join(text[a:b] for a, b in result["source_spans"])
    for key in ("block_id", "scope", "section_path", "unit_context"):
        assert result[key] == original[key]
    assert result["selected_data_rows"] < result["original_data_rows"]


def test_label_only_ancestor_is_kept():
    text = fixture("| 유동자산 |\n| 재고 | 30 | 20 |\n")
    result = project_table(block(text), text, 360)
    assert result and "| 유동자산 |" in result["text"]


def test_unsafe_blank_labels_and_columns_rejected():
    for row in ("| | 10 | 20 |\n", "| 채권 | | 20 |\n", "| 채권 | 10 |\n"):
        text = fixture(row)
        assert project_table(block(text), text, 330) is None


def test_missing_unit_or_headers_rejected():
    for text in (fixture(unit=False), fixture(header=False)):
        assert project_table(block(text), text, 330) is None


def test_large_footnote_cannot_be_removed_to_meet_budget():
    text = fixture() + "긴 조건 " * 100 + "\n"
    assert project_table(block(text), text, 330) is None


def test_do_not_reproject_or_change_already_small_table():
    text = fixture()
    original = block(text)
    assert project_table(original, text, 10000) is None
    assert project_table({**original, "projected": True}, text, 330) is None
    assert project_table(original, text, 10) is None


def test_date_caption_and_all_multiline_headers_kept():
    text = fixture().replace("(단위: 천원)", "2025년 12월 31일 현재\n(단위: 천원)").replace(
        "| --- | --- | --- |", "| 총액 | 충당금 | 장부금액 |\n| --- | --- | --- |", 1)
    result = project_table(block(text), text, 400)
    assert result
    assert "2025년 12월 31일 현재" in result["text"]
    assert "| 총액 | 충당금 | 장부금액 |" in result["text"]


def test_source_mismatch_and_escaped_cell_delimiters_decline():
    text = fixture()
    original = block(text)
    assert project_table({**original, "text": "modified"}, text, 330) is None
    text = fixture("| 채권\\|기타 | 10 | 20 |\n")
    assert project_table(block(text), text, 330) is None


def test_negative_values_and_zeroes_not_recomputed():
    text = fixture("| 당기순이익 | (10) | 0 |\n")
    result = project_table(block(text), text, 400)
    assert result and "| 당기순이익 | (10) | 0 |" in result["text"]
    assert all(a < b for a, b in result["source_spans"])


def test_second_table_or_unit_context_is_not_projected_together():
    for extra in ("| 구분 | 당기 | 전기 |\n", "| 구분 | 2024 | 2023 |\n", "| (단위: 원) |\n"):
        text = fixture().replace("| 합계 |", extra + "| 합계 |")
        assert project_table(block(text), text, 400) is None


def test_numeric_year_header_cannot_be_selected_away():
    text = fixture("| 연도 | 2025 | 2024 |\n")
    for budget in (100, 330, 500):
        result = project_table(block(text), text, budget)
        assert result is None or "| 연도 | 2025 | 2024 |" in result['text']
