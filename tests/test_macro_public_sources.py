import copy

import pytest

from cores.utils import clean_markdown
from prism_core.market_report_context import market_report_context, public_macro_prose


def test_off_preserves_legacy_prose():
    assert public_macro_prose({"report_prose": "Unlinked claim [1]"}) == "Unlinked claim [1]"


@pytest.mark.parametrize("prose", [
    "Fed claim [1] [4] [10]",
    "Fed claim [1]. [Source](https://www.federalreserve.gov/newsevents.htm)",
    "Claim [1,2]. https://www.federalreserve.gov/newsevents.htm",
    "Claim https://127.0.0.1/private", "Claim https://example.local/private", "",
])
def test_unresolved_or_private_source_suppressed_without_inventing_references(prose):
    context = {"market_intelligence": {}, "report_prose": prose, "market_regime": "sideways"}
    original = copy.deepcopy(context)
    result = public_macro_prose(context)
    assert "제외했습니다" in result
    assert "https://" not in result and "[1]" not in result
    assert context == original


@pytest.mark.parametrize("prose", [
    "Claim [Fed](https://www.federalreserve.gov/newsevents.htm), 2026-09-17.",
    "Claim [1](https://www.federalreserve.gov/newsevents.htm), 2026-09-17.",
    "Claim [1].\n\n[1]: https://www.federalreserve.gov/newsevents.htm",
])
def test_linked_prose_preserved_without_claiming_truth(prose):
    assert public_macro_prose({"market_intelligence": {}, "report_prose": prose}) == prose


def test_english_omission():
    assert "was omitted" in public_macro_prose({"market_intelligence": {}, "report_prose": "claim"}, "en")


def test_korean_heading_survives_actual_markdown_cleaner():
    result = clean_markdown(market_report_context({"market_intelligence": {}, "market_regime": "sideways"}))
    assert "### 공통 시장 근거\n\n확정된 매매 국면:" in result
    assert "분석가의 분위기 설명이나 점수" in result
