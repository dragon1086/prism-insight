"""Regression tests for the retired PDF backend compatibility path."""

from unittest.mock import patch

import pdf_converter


def test_pdfkit_method_delegates_to_playwright():
    with patch.object(pdf_converter, "markdown_to_pdf_playwright") as render:
        pdf_converter.markdown_to_pdf(
            "input.md",
            "output.pdf",
            method="pdfkit",
            add_theme=True,
            logo_path="logo.png",
            enable_watermark=True,
            watermark_opacity=0.1,
        )

    render.assert_called_once_with(
        "input.md",
        "output.pdf",
        True,
        "logo.png",
        True,
        0.1,
    )
