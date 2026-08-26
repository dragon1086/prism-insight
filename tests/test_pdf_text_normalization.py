from pdf_converter import normalize_pdf_text


def test_normalize_pdf_text_preserves_content_and_markdown_symbols():
    source = "# 삼성전자  \r\n목표가: 100,000원  \r\nhttps://example.com?a=1&b=2\r\n"
    assert normalize_pdf_text(source) == (
        "# 삼성전자\n목표가: 100,000원\nhttps://example.com?a=1&b=2\n"
    )


def test_normalize_pdf_text_caps_blank_lines_and_handles_empty_input():
    assert normalize_pdf_text("첫 문단\n\n\n\n둘째 문단") == "첫 문단\n\n둘째 문단\n"
    assert normalize_pdf_text("") == ""
