import ast
import re
from pathlib import Path

import pytest

from prism_core.competitive_evidence import attach_competitive_evidence


NEWS = """### News
Ordinary news.
#### Competitive Evidence
type: business_competitive_position
entity: EXAMPLE
peers: Peer A; Peer B
metric: operating_margin
value: 12%
period: FY2025
geography: global
source: https://example.com/annual-report
status: SEARCH_ONLY
#### Risks
Other risks.
"""


@pytest.mark.parametrize("market,symbol", [("KR", "012630"), ("US", "EXAMPLE")])
@pytest.mark.parametrize("language", ["ko", "en"])
def test_reuse_preserves_record_without_claiming_verification(market, symbol, language):
    original = {"news_analysis": NEWS, "company_overview": "Original overview"}
    reports, receipt = attach_competitive_evidence(original, market, symbol, "20260911", language)
    assert original["news_analysis"] == NEWS
    assert original["company_overview"] == "Original overview"
    assert receipt["status"] == "COPIED_NOT_VALIDATED"
    assert receipt["evidence_id"].startswith("CE-")
    for key in ("news_analysis", "company_overview"):
        assert receipt["evidence_id"] in reports[key]
        assert "status: SEARCH_ONLY" in reports[key]
        assert "source: https://example.com/annual-report" in reports[key]
    assert "Other risks." not in reports["company_overview"]
    assert attach_competitive_evidence(original, market, symbol, "20260911", language) == (reports, receipt)


@pytest.mark.parametrize("news,status", [("no record", "RECORD_ABSENT"), ("#### Competitive Evidence\n", "RECORD_EMPTY"), (NEWS + NEWS, "RECORD_AMBIGUOUS")])
def test_missing_or_ambiguous_never_becomes_positive(news, status):
    reports, receipt = attach_competitive_evidence({"news_analysis": news}, "US", "EXAMPLE", "20260911")
    assert receipt["status"] == status
    assert receipt["evidence_id"] is None
    assert status in reports["company_overview"]
    assert reports["news_analysis"] == news


def test_oversize_is_not_silently_truncated_or_copied():
    news = "#### Competitive Evidence\n" + "x" * 12001
    reports, receipt = attach_competitive_evidence({"news_analysis": news}, "KR", "012630", "20260911")
    assert receipt["status"] == "RECORD_OVERSIZE"
    assert reports["news_analysis"] == news
    assert len(reports["company_overview"]) < 500


@pytest.mark.parametrize("fence", ["```", "~~~"])
def test_heading_inside_fenced_example_is_not_a_record(fence):
    _, receipt = attach_competitive_evidence({"news_analysis": f"{fence}markdown\n{NEWS}{fence}\n"}, "US", "EXAMPLE", "20260911")
    assert receipt["status"] == "RECORD_ABSENT"


def test_unclosed_fence_in_record_cannot_corrupt_report():
    _, receipt = attach_competitive_evidence({"news_analysis": "#### Competitive Evidence\n```\nnot closed"}, "US", "EXAMPLE", "20260911")
    assert receipt["status"] == "RECORD_MALFORMED"


def test_real_record_after_fenced_example_gets_id_at_correct_offset():
    prefix = "```markdown\n#### Competitive Evidence\nfake\n```\n"
    reports, receipt = attach_competitive_evidence({"news_analysis": prefix + NEWS}, "US", "EXAMPLE", "20260911")
    assert reports["news_analysis"].startswith(prefix)
    assert receipt["status"] == "COPIED_NOT_VALIDATED"
    assert reports["news_analysis"].count(receipt["evidence_id"]) == 1


def test_generic_report_h3_variant_keeps_nested_h4_records():
    news = "### Competitive Evidence\n#### First claim\nstatus: SEARCH_ONLY\n### News Analysis\nnot evidence"
    reports, receipt = attach_competitive_evidence({"news_analysis": news}, "US", "EXAMPLE", "20260911")
    assert receipt["status"] == "COPIED_NOT_VALIDATED"
    assert "#### First claim" in reports["company_overview"]
    assert "not evidence" not in reports["company_overview"]


@pytest.mark.parametrize("heading", ["### competitive evidence", "#### COMPETITIVE EVIDENCE", "### **Competitive Evidence**"])
def test_exact_title_format_variants_not_arbitrary_fuzzy_matches(heading):
    from cores.utils import clean_markdown
    reports, receipt = attach_competitive_evidence({"news_analysis": heading + "\nstatus: SEARCH_ONLY"}, "US", "EXAMPLE", "20260911")
    assert receipt["status"] == "COPIED_NOT_VALIDATED"
    assert "SEARCH_ONLY" in reports["company_overview"]
    assert heading in clean_markdown(reports["news_analysis"])


def test_clean_markdown_preserves_exact_evidence_headings():
    from cores.utils import clean_markdown
    reports, receipt = attach_competitive_evidence({"news_analysis": NEWS}, "US", "EXAMPLE", "20260911", "en")
    cleaned = clean_markdown(reports["company_overview"])
    assert "#### Competitive Evidence Handoff" in cleaned
    assert "#### Competitive Evidence\n" in cleaned
    assert receipt["evidence_id"] in cleaned


def test_context_and_content_are_bound_to_id():
    def ident(market="US", symbol="EXAMPLE", date="20260911", news=NEWS):
        return attach_competitive_evidence({"news_analysis": news}, market, symbol, date)[1]["evidence_id"]
    assert len({ident(), ident(market="KR"), ident(symbol="OTHER"), ident(date="20260910"), ident(news=NEWS.replace("12%", "13%"))}) == 5


@pytest.mark.parametrize("file", ["cores/analysis.py", "prism-us/cores/us_analysis.py"])
def test_both_pipelines_handoff_before_strategy_and_summary(file):
    source = (Path(__file__).resolve().parents[1] / file).read_text()
    calls = [n for n in ast.walk(ast.parse(source)) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)]
    handoff = [n.lineno for n in calls if n.func.id == "attach_competitive_evidence"]
    assert len(handoff) == 1
    downstream = [n.lineno for n in calls if n.func.id in {"generate_investment_strategy", "generate_summary"}]
    assert downstream and all(handoff[0] < line for line in downstream)
    assert re.search(r"section_reports(?:\.get\(|\[)['\"]company_overview['\"]", source)


@pytest.mark.asyncio
@pytest.mark.parametrize("market,symbol", [("KR", "012630"), ("US", "EXAMPLE")])
async def test_downstream_real_prompt_builders_receive_identical_evidence(monkeypatch, market, symbol):
    from unittest.mock import Mock
    import cores.report_generation as generation
    from cores.llm.ports import LLMResult

    seen = []

    class Backend:
        async def run(self, spec, user_input):
            seen.append(user_input)
            return LLMResult(text="test synthesis")

    monkeypatch.setattr(generation, "_report_backend", Backend())
    reports, receipt = attach_competitive_evidence({"news_analysis": NEWS}, market, symbol, "20260911", "en")
    await generation.generate_investment_strategy(reports, "\n".join(reports.values()), "Example", symbol, "20260911", Mock(), "en")
    await generation.generate_summary(reports, "Example", symbol, "20260911", Mock(), "en")
    assert len(seen) == 2
    for user_input in seen:
        assert receipt["evidence_id"] in user_input
        assert "status: SEARCH_ONLY" in user_input
        assert "https://example.com/annual-report" in user_input


def test_id_and_source_survive_actual_pdf_text_input(tmp_path):
    from reportlab.pdfgen import canvas
    from pdf_converter import pdf_to_markdown_text

    reports, receipt = attach_competitive_evidence({"news_analysis": NEWS}, "US", "EXAMPLE", "20260911", "en")
    path = tmp_path / "evidence.pdf"
    pdf = canvas.Canvas(str(path))
    text = pdf.beginText(36, 800)
    text.setFont("Helvetica", 8)
    for line in reports["company_overview"].splitlines():
        text.textLine(line)
    pdf.drawText(text)
    pdf.save()
    extracted = pdf_to_markdown_text(str(path))
    assert receipt["evidence_id"] in extracted
    assert "https://example.com/annual-report" in extracted
    assert "SEARCH_ONLY" in extracted


@pytest.mark.parametrize("file", ["cores/analysis.py", "prism-us/cores/us_analysis.py"])
def test_handoff_logger_is_compatible_with_mcp_logger_signature(file):
    import inspect
    from mcp_agent.logging.logger import Logger

    source = (Path(__file__).resolve().parents[1] / file).read_text()
    calls = [n for n in ast.walk(ast.parse(source)) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "info"]
    calls = [n for n in calls if "[COMPETITIVE_EVIDENCE]" in ast.get_source_segment(source, n)]
    assert len(calls) == 1
    assert len(calls[0].args) == 1
    inspect.signature(Logger.info).bind(None, "safe metadata")
