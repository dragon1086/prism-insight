"""
Export Formats Module

Provides alternative export formats for analysis reports:
- HTML export with embedded charts and professional styling
- JSON summary export for machine-readable API consumption
"""

import re
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def export_to_html(markdown_content: str, title: str = "Stock Analysis Report",
                   css_theme: str = "default") -> str:
    """
    Convert markdown analysis report to styled HTML with embedded base64 charts.

    Args:
        markdown_content: Raw markdown report text
        title: HTML page title
        css_theme: CSS theme name (currently: 'default')

    Returns:
        Complete HTML document string
    """
    # Convert basic markdown to HTML
    html_body = _markdown_to_html(markdown_content)

    css = _get_css_theme(css_theme)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title}</title>
    <style>{css}</style>
</head>
<body>
    <div class="report-container">
        {html_body}
    </div>
</body>
</html>"""
    return html


def _markdown_to_html(text: str) -> str:
    """Convert markdown text to basic HTML."""
    lines = text.split('\n')
    html_lines = []
    in_list = False
    in_table = False

    for line in lines:
        stripped = line.strip()

        # Headings
        heading_match = re.match(r'^(#{1,6})\s+(.+)$', stripped)
        if heading_match:
            level = len(heading_match.group(1))
            content = heading_match.group(2)
            if in_list:
                html_lines.append('</ul>')
                in_list = False
            if in_table:
                html_lines.append('</table>')
                in_table = False
            html_lines.append(f'<h{level}>{_inline_formatting(content)}</h{level}>')
            continue

        # Table rows
        if stripped.startswith('|') and stripped.endswith('|'):
            if not in_table:
                html_lines.append('<table>')
                in_table = True
            # Skip separator rows
            if re.match(r'^\|[\s\-:|]+\|$', stripped):
                continue
            cells = [c.strip() for c in stripped.strip('|').split('|')]
            row = ''.join(f'<td>{_inline_formatting(c)}</td>' for c in cells)
            html_lines.append(f'<tr>{row}</tr>')
            continue
        elif in_table:
            html_lines.append('</table>')
            in_table = False

        # List items
        if re.match(r'^[-*]\s+', stripped):
            if not in_list:
                html_lines.append('<ul>')
                in_list = True
            content = re.sub(r'^[-*]\s+', '', stripped)
            html_lines.append(f'<li>{_inline_formatting(content)}</li>')
            continue
        elif in_list and stripped == '':
            html_lines.append('</ul>')
            in_list = False

        # Empty line
        if stripped == '':
            html_lines.append('')
            continue

        # Base64 images (preserve as-is)
        if '<img' in stripped or '![' in stripped:
            html_lines.append(stripped)
            continue

        # Paragraph
        html_lines.append(f'<p>{_inline_formatting(stripped)}</p>')

    if in_list:
        html_lines.append('</ul>')
    if in_table:
        html_lines.append('</table>')

    return '\n'.join(html_lines)


def _inline_formatting(text: str) -> str:
    """Apply inline markdown formatting."""
    # Bold
    text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)
    # Italic
    text = re.sub(r'\*(.+?)\*', r'<em>\1</em>', text)
    # Code
    text = re.sub(r'`(.+?)`', r'<code>\1</code>', text)
    return text


def _get_css_theme(theme: str) -> str:
    """Return CSS theme string."""
    return """
        :root {
            --bg: #0f1117;
            --card: #1a1d2e;
            --text: #e1e4ea;
            --accent: #6366f1;
            --accent-dim: #4f46e5;
            --border: #2a2d3e;
            --muted: #8b8fa3;
        }
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
            background: var(--bg);
            color: var(--text);
            line-height: 1.7;
        }
        .report-container {
            max-width: 900px;
            margin: 40px auto;
            padding: 40px;
            background: var(--card);
            border-radius: 16px;
            border: 1px solid var(--border);
            box-shadow: 0 4px 24px rgba(0,0,0,0.3);
        }
        h1 { font-size: 2em; color: #fff; margin: 24px 0 16px; border-bottom: 2px solid var(--accent); padding-bottom: 8px; }
        h2 { font-size: 1.5em; color: #c7c9d4; margin: 20px 0 12px; }
        h3 { font-size: 1.2em; color: var(--accent); margin: 16px 0 10px; }
        h4 { font-size: 1.05em; color: var(--muted); margin: 12px 0 8px; }
        p { margin: 8px 0; }
        ul { padding-left: 24px; margin: 8px 0; }
        li { margin: 4px 0; }
        table {
            width: 100%;
            border-collapse: collapse;
            margin: 16px 0;
        }
        td, th {
            padding: 8px 12px;
            border: 1px solid var(--border);
            text-align: left;
        }
        tr:nth-child(even) { background: rgba(99,102,241,0.05); }
        strong { color: #fff; }
        code { background: #2a2d3e; padding: 2px 6px; border-radius: 4px; font-size: 0.9em; }
        img { max-width: 100%; height: auto; border-radius: 8px; margin: 12px 0; }
    """


def export_to_json_summary(report_text: str, company_info: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Extract structured data from a markdown report into a JSON-serializable dict.

    Args:
        report_text: Completed markdown analysis report
        company_info: Optional dict with metadata (company_code, company_name, etc.)

    Returns:
        Dictionary with structured report data
    """
    from cores.utils import extract_key_metrics

    summary: Dict[str, Any] = {
        "metadata": company_info or {},
        "metrics": extract_key_metrics(report_text),
        "sections": _extract_sections(report_text),
        "report_length": len(report_text),
    }

    return summary


def _extract_sections(markdown_text: str) -> Dict[str, str]:
    """Extract section titles and their first paragraph from markdown."""
    sections = {}
    current_section = None
    current_content = []

    for line in markdown_text.split('\n'):
        heading_match = re.match(r'^#{1,3}\s+(.+)$', line.strip())
        if heading_match:
            # Save previous section
            if current_section:
                sections[current_section] = '\n'.join(current_content).strip()[:500]
            current_section = heading_match.group(1).strip()
            current_content = []
        elif current_section:
            current_content.append(line)

    # Save last section
    if current_section:
        sections[current_section] = '\n'.join(current_content).strip()[:500]

    return sections
