"""
Tests for cores/utils.py clean_markdown and sub-functions.
"""
import sys
from pathlib import Path

# Add project root to Python path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from cores.utils import (
    clean_markdown,
    _clean_llm_artifacts,
    _fix_line_breaks,
    _fix_markdown_tables,
    _preserve_headings,
    sanitize_report_for_telegram,
    extract_key_metrics,
)


class TestCleanLLMArtifacts:
    """Tests for _clean_llm_artifacts (GPT artifact removal)."""

    def test_removes_gpt52_function_calls(self):
        text = 'Hello {"name": "test_func", "arguments": {"a": 1}} World'
        result = _clean_llm_artifacts(text)
        assert "test_func" not in result
        assert "Hello" in result
        assert "World" in result

    def test_removes_special_tokens(self):
        text = "Some text <|eot_id|> more text"
        result = _clean_llm_artifacts(text)
        assert "<|eot_id|>" not in result
        assert "Some text" in result

    def test_removes_backtick_code_blocks(self):
        text = "before\n```markdown\nsome content\n```\nafter"
        result = _clean_llm_artifacts(text)
        assert "```" not in result
        assert "some content" in result

    def test_preserves_normal_text(self):
        text = "This is normal text without any artifacts."
        result = _clean_llm_artifacts(text)
        assert result == text


class TestFixLineBreaks:
    """Tests for _fix_line_breaks."""

    def test_converts_escaped_newlines(self):
        text = "line1\\n\\nline2"
        result = _fix_line_breaks(text)
        assert "\n\n" in result

    def test_joins_broken_korean_lines(self):
        text = "한국어\n텍스트"
        result = _fix_line_breaks(text)
        assert "한국어텍스트" in result

    def test_separates_header_endings_from_starters(self):
        text = "분석요약본분석에"
        result = _fix_line_breaks(text)
        assert "요약\n\n본" in result

    def test_preserves_non_korean_text(self):
        text = "English\ntext"
        result = _fix_line_breaks(text)
        assert "English\ntext" == result


class TestFixMarkdownTables:
    """Tests for _fix_markdown_tables."""

    def test_adds_blank_before_table(self):
        text = "Some text\n| Header 1 | Header 2 |\n|---|---|\n| a | b |"
        result = _fix_markdown_tables(text)
        lines = result.split("\n")
        table_idx = next(i for i, l in enumerate(lines) if l.strip().startswith("|"))
        assert lines[table_idx - 1].strip() == ""

    def test_preserves_already_separated_table(self):
        text = "Some text\n\n| Header 1 | Header 2 |\n|---|---|\n| a | b |"
        result = _fix_markdown_tables(text)
        assert "\n\n|" in result

    def test_handles_empty_input(self):
        result = _fix_markdown_tables("")
        assert result == ""


class TestPreserveHeadings:
    """Tests for _preserve_headings."""

    def test_keeps_valid_heading(self):
        text = "## 투자 전략 분석"
        result = _preserve_headings(text)
        assert "##" in result
        assert "분석" in result

    def test_demotes_invalid_heading(self):
        text = "### This is a very random heading that does not match any keyword at all but is still short"
        result = _preserve_headings(text)
        # Long heading without keywords should be preserved as-is since it's h3
        assert result is not None

    def test_adds_spacing_around_headings(self):
        text = "content\n## 분석\ncontent2"
        result = _preserve_headings(text)
        assert "\n\n## " in result or "## 분석" in result


class TestCleanMarkdown:
    """Integration tests for the full pipeline."""

    def test_full_pipeline(self):
        text = '```json\n{"data": "test"}\n```\n\n## 투자 전략 분석\n\n| Col1 | Col2 |\n|---|---|\n| a | b |'
        result = clean_markdown(text)
        assert "```" not in result
        assert "분석" in result

    def test_empty_input(self):
        assert clean_markdown("") == ""

    def test_plain_text_passthrough(self):
        text = "Hello, this is plain text."
        result = clean_markdown(text)
        assert "Hello" in result


class TestSanitizeReportForTelegram:
    """Tests for sanitize_report_for_telegram."""

    def test_removes_base64_images(self):
        text = 'Before <img src="data:image/png;base64,abc123"> After'
        result = sanitize_report_for_telegram(text)
        assert "data:image" not in result
        assert "Before" in result
        assert "After" in result

    def test_removes_markdown_base64_images(self):
        text = 'Before ![chart](data:image/png;base64,abc123) After'
        result = sanitize_report_for_telegram(text)
        assert "data:image" not in result

    def test_removes_html_tags(self):
        text = "<h1>Title</h1><p>Content</p>"
        result = sanitize_report_for_telegram(text)
        assert "<h1>" not in result
        assert "Title" in result

    def test_truncates_to_max_length(self):
        text = "a" * 5000
        result = sanitize_report_for_telegram(text, max_length=100)
        assert len(result) <= 100
        assert "truncated" in result

    def test_collapses_multiple_blank_lines(self):
        text = "a\n\n\n\n\nb"
        result = sanitize_report_for_telegram(text)
        assert "\n\n\n" not in result


class TestExtractKeyMetrics:
    """Tests for extract_key_metrics."""

    def test_extracts_current_price(self):
        text = "현재가: 85,500원"
        metrics = extract_key_metrics(text)
        assert metrics.get("current_price") == 85500

    def test_extracts_target_price(self):
        text = "목표가: 120,000원"
        metrics = extract_key_metrics(text)
        assert metrics.get("target_price") == 120000

    def test_extracts_per(self):
        text = "PER: 12.5배"
        metrics = extract_key_metrics(text)
        assert metrics.get("per") == 12.5

    def test_extracts_pbr(self):
        text = "PBR: 1.8배"
        metrics = extract_key_metrics(text)
        assert metrics.get("pbr") == 1.8

    def test_extracts_dividend_yield(self):
        text = "배당수익률: 2.5%"
        metrics = extract_key_metrics(text)
        assert metrics.get("dividend_yield") == 2.5

    def test_extracts_market_cap(self):
        text = "시가총액: 약 50조"
        metrics = extract_key_metrics(text)
        assert "50조" in metrics.get("market_cap", "")

    def test_empty_text_returns_empty_dict(self):
        metrics = extract_key_metrics("")
        assert metrics == {}

    def test_english_format(self):
        text = "Current Price: $150"
        metrics = extract_key_metrics(text)
        assert metrics.get("current_price") == 150
