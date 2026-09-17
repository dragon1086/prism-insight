import ast
from pathlib import Path

import pytest

from prism_core.market_report_context import market_report_context


def test_off_is_empty():
    assert market_report_context(None) == ""
    assert market_report_context({"market_regime": "sideways"}) == ""


@pytest.mark.parametrize("language", ["ko", "en"])
def test_same_facts_period_missing_denominator_and_id_reach_pdf(tmp_path, language):
    from reportlab.pdfgen import canvas
    from pdf_converter import pdf_to_markdown_text
    context = {"market_regime": "sideways", "market_intelligence": {
        "price_asof": "2026-09-16", "source": "yfinance_adjusted_daily", "input_sha256": "abcdef1234567890",
        "rows": [{"symbol": "IWF", "returns_pct": {"20": 4.2}, "relative_spy_pp": {"20": 1.3}}],
        "participation": {"advance": 3, "decline": 1, "unchanged": 0, "valid_count": 4,
                          "universe_count": 7, "missing_count": 3, "asof": "20260917"}}}
    text = market_report_context(context, language)
    assert "?/4.2/?" in text and "?/1.3/?" in text
    assert "valid=4/7" in text and "missing=3" in text
    assert "MI-abcdef1234567890" in text
    path = tmp_path / "market.pdf"
    pdf = canvas.Canvas(str(path))
    item = pdf.beginText(30, 800)
    item.setFont("Helvetica", 8)
    for line in text.splitlines():
        item.textLine(line)
    pdf.drawText(item)
    pdf.save()
    extracted = pdf_to_markdown_text(str(path))
    assert "MI-abcdef1234567890" in extracted
    assert "?/4.2/?" in extracted


@pytest.mark.parametrize("relative", ["cores/analysis.py", "prism-us/cores/us_analysis.py"])
def test_shared_market_evidence_precedes_strategy_and_summary(relative):
    path = Path(__file__).resolve().parents[1] / relative
    calls = [node for node in ast.walk(ast.parse(path.read_text()))
             if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)]
    source = next(node.lineno for node in calls if node.func.id == "market_report_context")
    assert all(source < node.lineno for node in calls
               if node.func.id in {"generate_investment_strategy", "generate_summary"})
