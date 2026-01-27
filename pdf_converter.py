#!/usr/bin/env python3
"""
Module for converting Markdown files to PDF

Provides various conversion methods:
1. Playwright-based HTML conversion (Recommended) - Uses Chromium browser engine
2. pdfkit-based HTML conversion (Legacy) - Requires wkhtmltopdf, archived in 2023
3. reportlab direct rendering - Theme not supported
4. mdpdf simple conversion - Theme not supported

Recommended order: Playwright > pdfkit > reportlab > mdpdf
"""
import os
import logging
import markdown
import tempfile
import PyPDF2
import html2text
import base64
from datetime import datetime

# Logger configuration
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Logo path (relative to project root)
LOGO_PATH = os.path.join(os.path.dirname(__file__), "docs", "images", "prism-insight-logo.jpeg")

# Prism Light Theme - Original Design inspired by light refraction
# Unique design with spectrum colors representing data analysis through different perspectives
DEFAULT_CSS = """
/* ===== Prism Light Theme - CSS Variables ===== */
:root {
    --prism-dark: #0f172a;         /* Deep Slate */
    --prism-mid: #334155;          /* Slate */
    --prism-light: #64748b;        /* Light Slate */
    --spectrum-violet: #8b5cf6;    /* Violet */
    --spectrum-indigo: #6366f1;    /* Indigo */
    --spectrum-blue: #3b82f6;      /* Blue */
    --spectrum-cyan: #06b6d4;      /* Cyan */
    --spectrum-emerald: #10b981;   /* Emerald */
    --spectrum-amber: #f59e0b;     /* Amber */
    --spectrum-rose: #f43f5e;      /* Rose */
    --positive-color: #10b981;     /* Emerald (positive) */
    --negative-color: #ef4444;     /* Red (negative) */
    --bg-cream: #fafaf9;           /* Warm White */
    --bg-white: #ffffff;
    --border-soft: #e2e8f0;
    --text-dark: #0f172a;
    --text-muted: #64748b;
}

@page {
    size: A4;
    margin: 12mm 15mm;
}

body {
    font-family: "Pretendard", -apple-system, BlinkMacSystemFont, "Noto Sans KR", sans-serif;
    font-size: 10pt;
    line-height: 1.7;
    color: var(--text-dark);
    background-color: var(--bg-white);
    margin: 0;
    padding: 0;
}

/* ===== Prism Header - Geometric Light Refraction ===== */
.report-header {
    position: relative;
    background: var(--prism-dark);
    color: white;
    padding: 20px 32px 28px 32px;
    overflow: hidden;
}

/* Header logo section */
.header-logo-section {
    margin-bottom: 8px;
}

.header-logo {
    height: 16px;
    width: auto;
    margin: 0;
    border: none;
    border-radius: 0;
    box-shadow: none;
    max-height: 16px;
    opacity: 0.85;
}

.header-logo-text {
    font-size: 8pt;
    font-weight: 600;
    letter-spacing: 1px;
    opacity: 0.85;
}

/* Prism spectrum bar - rainbow gradient accent */
.report-header::before {
    content: "";
    position: absolute;
    bottom: 0;
    left: 0;
    right: 0;
    height: 4px;
    background: linear-gradient(90deg,
        var(--spectrum-violet) 0%,
        var(--spectrum-indigo) 16%,
        var(--spectrum-blue) 33%,
        var(--spectrum-cyan) 50%,
        var(--spectrum-emerald) 66%,
        var(--spectrum-amber) 83%,
        var(--spectrum-rose) 100%
    );
}

/* Geometric prism shape */
.report-header::after {
    content: "";
    position: absolute;
    right: 32px;
    top: 50%;
    transform: translateY(-50%);
    width: 60px;
    height: 60px;
    background: linear-gradient(135deg,
        rgba(139, 92, 246, 0.3) 0%,
        rgba(6, 182, 212, 0.3) 50%,
        rgba(16, 185, 129, 0.3) 100%
    );
    clip-path: polygon(50% 0%, 100% 100%, 0% 100%);
}

.report-header .title-section {
    position: relative;
    z-index: 1;
}

.report-header .report-title {
    font-size: 20pt;
    font-weight: 700;
    margin: 0;
    letter-spacing: -0.3px;
}

.report-header .report-subtitle {
    font-size: 9pt;
    color: rgba(255, 255, 255, 0.7);
    margin-top: 6px;
    font-weight: 400;
}

/* ===== Main Content Area ===== */
.report-content {
    padding: 32px 28px 40px 28px;
}

.report-content > h1:first-child {
    margin-top: 0;
}

/* ===== Heading Styles - Spectrum Accents ===== */
h1 {
    color: var(--prism-dark);
    font-size: 16pt;
    font-weight: 700;
    margin-top: 32px;
    margin-bottom: 16px;
    padding-bottom: 10px;
    border-bottom: 2px solid var(--border-soft);
    position: relative;
}

/* Spectrum underline for h1 */
h1::after {
    content: "";
    position: absolute;
    bottom: -2px;
    left: 0;
    width: 80px;
    height: 2px;
    background: linear-gradient(90deg, var(--spectrum-violet), var(--spectrum-cyan));
}

h2 {
    color: var(--prism-dark);
    font-size: 13pt;
    font-weight: 600;
    margin-top: 28px;
    margin-bottom: 14px;
    padding-left: 14px;
    border-left: 3px solid var(--spectrum-indigo);
    background: linear-gradient(90deg, rgba(99, 102, 241, 0.06) 0%, transparent 100%);
    padding-top: 6px;
    padding-bottom: 6px;
}

h3 {
    color: var(--prism-mid);
    font-size: 11pt;
    font-weight: 600;
    margin-top: 22px;
    margin-bottom: 10px;
    padding-left: 10px;
    border-left: 2px solid var(--spectrum-cyan);
}

h4 {
    color: var(--prism-light);
    font-size: 10pt;
    font-weight: 600;
    margin-top: 16px;
    margin-bottom: 8px;
}

/* ===== Table Styles - Clean & Modern ===== */
table {
    border-collapse: collapse;
    width: 100%;
    max-width: 100%;
    margin: 16px 0;
    font-size: 8.5pt;
    border: 1px solid var(--border-soft);
    table-layout: fixed;
}

thead {
    background: linear-gradient(180deg, var(--prism-dark) 0%, var(--prism-mid) 100%);
}

th {
    color: white;
    font-weight: 600;
    padding: 10px 8px;
    text-align: left;
    font-size: 8.5pt;
    border: none;
    word-wrap: break-word;
}

td {
    padding: 8px;
    border-bottom: 1px solid var(--border-soft);
    vertical-align: top;
    text-align: left;
    font-variant-numeric: tabular-nums;
    word-wrap: break-word;
    overflow-wrap: break-word;
}

tr:nth-child(even) {
    background-color: var(--bg-cream);
}

/* ===== Blockquote - Soft Highlight ===== */
blockquote {
    background: linear-gradient(90deg, rgba(99, 102, 241, 0.08) 0%, transparent 100%);
    border-left: 3px solid var(--spectrum-indigo);
    padding: 12px 16px;
    margin: 18px 0;
    color: var(--prism-mid);
    font-size: 9pt;
    border-radius: 0 4px 4px 0;
}

/* ===== Code Blocks ===== */
code {
    background-color: var(--bg-cream);
    padding: 2px 5px;
    border-radius: 3px;
    font-family: "JetBrains Mono", "SF Mono", Consolas, monospace;
    font-size: 8.5pt;
    color: var(--spectrum-indigo);
}

pre {
    background: var(--prism-dark);
    color: #e2e8f0;
    padding: 14px;
    border-radius: 6px;
    overflow-x: auto;
    font-size: 8.5pt;
}

/* ===== Images (Charts) - Contained within report ===== */
img {
    max-width: 100% !important;
    width: auto !important;
    height: auto !important;
    max-height: 380px;
    margin: 16px auto;
    display: block;
    border-radius: 6px;
    border: 1px solid var(--border-soft);
    object-fit: contain;
    box-sizing: border-box;
}

/* Chart container for proper sizing */
.chart-container {
    width: 100%;
    max-width: 100%;
    overflow: hidden;
    margin: 20px 0;
    text-align: center;
    page-break-inside: avoid;
}

.chart-container img {
    max-width: 100% !important;
    max-height: 360px !important;
    width: auto !important;
    height: auto !important;
}

/* ===== Horizontal Rule - Spectrum Fade ===== */
hr {
    border: none;
    height: 1px;
    background: linear-gradient(90deg,
        transparent 0%,
        var(--spectrum-violet) 20%,
        var(--spectrum-cyan) 50%,
        var(--spectrum-emerald) 80%,
        transparent 100%
    );
    margin: 28px 0;
    opacity: 0.5;
}

/* ===== List Styles ===== */
ul, ol {
    margin: 10px 0;
    padding-left: 22px;
}

li {
    margin-bottom: 5px;
    line-height: 1.6;
}

li::marker {
    color: var(--spectrum-indigo);
}

/* ===== Strong/Bold Text ===== */
strong {
    color: var(--prism-dark);
    font-weight: 600;
}

/* ===== Footer - Minimal & Clean ===== */
.report-footer {
    margin: 32px 28px 24px 28px;
    padding-top: 16px;
    border-top: 1px solid var(--border-soft);
    display: flex;
    justify-content: space-between;
    align-items: center;
    font-size: 7.5pt;
    color: var(--text-muted);
}

.report-footer .powered-by {
    display: flex;
    align-items: center;
    gap: 8px;
}

.report-footer .powered-by img {
    height: 20px;
    width: auto;
    margin: 0;
    border: none;
    border-radius: 0;
    box-shadow: none;
    max-height: 20px;
}

.report-footer .disclaimer {
    text-align: right;
    max-width: 55%;
    line-height: 1.4;
    font-size: 7pt;
}

/* ===== Link Styles ===== */
a {
    color: var(--spectrum-indigo);
    text-decoration: none;
}

a:hover {
    text-decoration: underline;
}

/* ===== Page Break ===== */
.page-break {
    page-break-after: always;
}

/* ===== Print Styles ===== */
@media print {
    body {
        font-size: 10pt;
        -webkit-print-color-adjust: exact;
        print-color-adjust: exact;
    }

    .report-header, .report-header::before, .report-header::after {
        -webkit-print-color-adjust: exact;
        print-color-adjust: exact;
    }

    thead {
        -webkit-print-color-adjust: exact;
        print-color-adjust: exact;
    }

    tr:nth-child(even) {
        -webkit-print-color-adjust: exact;
        print-color-adjust: exact;
    }

    img {
        max-width: 100% !important;
        max-height: 380px !important;
        page-break-inside: avoid;
    }

    h1, h2, h3, h4 {
        page-break-after: avoid;
    }

    table {
        page-break-inside: auto;
    }

    tr {
        page-break-inside: avoid;
    }
}
"""

# Theme-related CSS (additional styles when add_theme=True)
THEME_CSS = """
/* Prism Light Theme - Additional styles */

/* Logo text fallback with spectrum gradient */
.logo-text {
    font-size: 14pt;
    font-weight: 700;
    letter-spacing: 0.5px;
    background: linear-gradient(90deg, var(--spectrum-violet), var(--spectrum-cyan));
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    background-clip: text;
}

.footer-logo {
    height: 20px;
    width: auto;
    margin: 0;
    box-shadow: none;
    border: none;
    border-radius: 0;
}

/* Ensure all images stay within bounds */
.report-content img {
    max-width: 100% !important;
    height: auto !important;
    box-sizing: border-box;
}
"""


def _extract_report_info(md_content: str) -> dict:
    """
    Extract report information from markdown content

    Args:
        md_content: Markdown content

    Returns:
        {"company_name": "Samsung Electronics", "report_date": "2025-12-27", ...}
    """
    import re

    info = {
        "company_name": "",
        "company_code": "",
        "report_date": datetime.now().strftime("%Y-%m-%d"),
        "report_type": "Analysis Report"
    }

    # Skip titles to ignore (disclaimers, etc.)
    skip_titles = ["투자 유의사항", "Investment Disclaimer", "핵심 요약", "Executive Summary"]

    # Method 1: Extract from H1 title like "# 삼성전자 (005930) 분석 보고서"
    h1_matches = re.findall(r'^#\s+(.+?)$', md_content, re.MULTILINE)
    for h1_text in h1_matches:
        h1_clean = h1_text.strip()
        # Skip disclaimer/summary titles
        if any(skip in h1_clean for skip in skip_titles):
            continue
        # Try to extract company name and code from title
        title_match = re.match(r'(.+?)[\s]*[\(\（]([A-Z0-9]{4,6}|\d{6})[\)\）]', h1_clean)
        if title_match:
            info["company_name"] = title_match.group(1).strip()
            info["company_code"] = title_match.group(2)
            break
        # If no code in parentheses, use the whole title
        if not any(skip in h1_clean for skip in skip_titles):
            info["company_name"] = h1_clean.replace("분석 보고서", "").replace("Analysis Report", "").strip()
            break

    # Method 2: Extract from content pattern like "기준 삼성전자는" or "삼성전자(005930)"
    if not info["company_name"]:
        # Korean pattern: "YYYY-MM-DD 기준 회사명은" or "회사명(종목코드)"
        kr_pattern = re.search(r'기준\s+([가-힣A-Za-z\s]+?)(?:는|은|이|가|\()', md_content)
        if kr_pattern:
            info["company_name"] = kr_pattern.group(1).strip()

    # Method 3: Extract stock code from content like "삼성전자(005930)" or "(AAPL)"
    if not info["company_code"]:
        # Korean stock code (6 digits)
        code_match = re.search(r'[\(\（](\d{6})[\)\）]', md_content)
        if code_match:
            info["company_code"] = code_match.group(1)
        else:
            # US ticker (1-5 uppercase letters)
            us_match = re.search(r'[\(\（]([A-Z]{1,5})[\)\）]', md_content)
            if us_match:
                info["company_code"] = us_match.group(1)

    # Extract date from content like "2025-01-24 기준" or "발행일: 2025-01-24"
    date_patterns = [
        r'(\d{4}-\d{2}-\d{2})\s*기준',  # "2025-01-24 기준"
        r'(?:발행일|Publication Date|생성 일시)[:\s]*(\d{4}[-./]\d{2}[-./]\d{2})',
        r'(\d{4}[-./]\d{2}[-./]\d{2})\s*(?:기준|현재|as of)',
    ]
    for pattern in date_patterns:
        date_match = re.search(pattern, md_content, re.IGNORECASE)
        if date_match:
            info["report_date"] = date_match.group(1).replace('.', '-').replace('/', '-')
            break

    return info


def _get_logo_base64() -> str:
    """Encode logo image to Base64"""
    logo_path = os.path.abspath(LOGO_PATH)

    if os.path.exists(logo_path):
        try:
            with open(logo_path, 'rb') as f:
                logo_data = f.read()
            return base64.b64encode(logo_data).decode('utf-8')
        except Exception as e:
            logger.warning(f"Failed to load logo: {e}")

    return ""

def create_watermark(html_content, logo_path, opacity=0.02):
    """
    Apply logo as watermark to HTML background

    Args:
        html_content (str): HTML content
        logo_path (str): Logo image path
        opacity (float): Watermark opacity (0.0-1.0)

    Returns:
        str: HTML with watermark applied
    """
    try:
        # Encode logo as Base64
        with open(logo_path, "rb") as image_file:
            encoded_logo = base64.b64encode(image_file.read()).decode('utf-8')

        # Watermark CSS style - browser compatibility and !important added
        watermark_style = f"""
        <style>
        body::before {{
            content: "";
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            z-index: -1;
            background-image: url('data:image/png;base64,{encoded_logo}');
            background-repeat: no-repeat;
            background-position: center;
            background-size: 40%;
            opacity: {opacity} !important;
            -webkit-opacity: {opacity} !important;
            -moz-opacity: {opacity} !important;
            filter: alpha(opacity={int(opacity * 100)}) !important;
            pointer-events: none;
        }}
        </style>
        """

        # Insert style just before </head> tag
        return html_content.replace('</head>', f'{watermark_style}</head>')
    except Exception as e:
        logger.error(f"Error applying watermark: {str(e)}")
        return html_content  # Return original on error

def markdown_to_html(md_file_path, add_css=True, add_theme=False, logo_path=None, enable_watermark=False, watermark_opacity=0.02):
    """
    Convert Markdown file to HTML with modern professional design

    Args:
        md_file_path (str): Markdown file path
        add_css (bool): Whether to add CSS styles
        add_theme (bool): Whether to add theme with header and footer
        logo_path (str): Logo image path (uses default logo if None)
        enable_watermark (bool): Whether to apply logo watermark to background
        watermark_opacity (float): Watermark opacity (0.0-1.0)

    Returns:
        str: Converted HTML string
    """
    try:
        # Get directory path of markdown file
        base_dir = os.path.dirname(os.path.abspath(md_file_path))

        # Read markdown file
        with open(md_file_path, 'r', encoding='utf-8') as f:
            md_content = f.read()

        # Extract report information for header
        report_info = _extract_report_info(md_content)

        # Convert image paths to absolute paths (using regex)
        import re

        # Find HTML image tags (base64 encoded)
        img_tags_pattern = r'<img\s+src="data:image/[^"]+"\s*[^>]*>'
        img_tags = re.findall(img_tags_pattern, md_content, re.IGNORECASE)

        # Replace with temporary placeholders (using HTML comments to survive markdown parsing)
        for i, tag in enumerate(img_tags):
            placeholder = f"<!--PRISM_IMG_PLACEHOLDER_{i}-->"
            md_content = md_content.replace(tag, placeholder)

        # Process remaining images (existing code)
        def replace_image_path(match):
            img_alt = match.group(1)
            img_path = match.group(2)

            # Keep absolute paths and URLs as is
            if os.path.isabs(img_path) or img_path.startswith(('http://', 'https://')):
                return f'![{img_alt}]({img_path})'

            # Convert relative path to absolute
            abs_path = os.path.abspath(os.path.join(base_dir, img_path))
            return f'![{img_alt}]({abs_path})'

        # Find markdown image link patterns and convert to absolute paths
        md_content = re.sub(r'!\[(.*?)]\((.*?)\)', replace_image_path, md_content)

        # Convert markdown to HTML (with extensions enabled)
        html = markdown.markdown(
            md_content,
            extensions=[
                'markdown.extensions.tables',
                'markdown.extensions.fenced_code',
                'markdown.extensions.nl2br',
                'markdown.extensions.sane_lists',
                'markdown.extensions.toc',
                'markdown.extensions.attr_list',  # Attribute support
                'markdown.extensions.extra'       # Additional features (including HTML)
            ]
        )

        # Restore HTML image tags
        for i, tag in enumerate(img_tags):
            placeholder = f"<!--PRISM_IMG_PLACEHOLDER_{i}-->"
            # Wrap in div for proper sizing
            wrapped_tag = f'<div class="chart-container">{tag}</div>'
            html = html.replace(placeholder, wrapped_tag)

        # Set logo path
        if logo_path is None:
            logo_path = LOGO_PATH

        # Get logo as Base64 for embedding
        logo_base64 = _get_logo_base64()
        logo_img = f'<img src="data:image/jpeg;base64,{logo_base64}" alt="Prism-Insight">' if logo_base64 else '<span class="logo-text">PRISM-INSIGHT</span>'

        # Company display name
        company_display = report_info["company_name"]
        if report_info["company_code"]:
            company_display += f" ({report_info['company_code']})"
        if not company_display:
            company_display = "Stock Analysis Report"

        # Determine report type based on language
        is_korean = any(ord(c) >= 0xAC00 and ord(c) <= 0xD7A3 for c in report_info["company_name"])
        report_type = "기업 분석 보고서" if is_korean else "Analysis Report"
        footer_disclaimer_line1 = "본 보고서는 AI 기반 자동 분석 시스템에 의해 생성되었습니다." if is_korean else "This report was generated by an AI-based automated analysis system."
        footer_disclaimer_line2 = "투자 결정은 반드시 추가적인 검토와 전문가 상담 후 진행하시기 바랍니다." if is_korean else "Please consult with experts before making investment decisions."

        # HTML header and footer templates (modern design)
        header_html = ""
        footer_html = ""

        if add_theme:
            # Create header logo (small, top-left)
            header_logo = f'<img src="data:image/jpeg;base64,{logo_base64}" class="header-logo" alt="Prism-Insight">' if logo_base64 else '<span class="header-logo-text">PRISM</span>'

            header_html = f"""
    <div class="report-header">
        <div class="header-logo-section">
            {header_logo}
        </div>
        <div class="title-section">
            <div class="report-title">{company_display}</div>
            <div class="report-subtitle">{report_type} · {report_info['report_date']}</div>
        </div>
    </div>
            """

            footer_html = f"""
    <div class="report-footer">
        <div class="powered-by">
            <span>Powered by <strong>Prism-Insight</strong></span>
        </div>
        <div class="disclaimer">
            {footer_disclaimer_line1}<br>
            {footer_disclaimer_line2}
        </div>
    </div>
            """

        # Create complete HTML document
        if add_css:
            css_content = DEFAULT_CSS
            if add_theme:
                css_content += THEME_CSS

            full_html = f"""<!DOCTYPE html>
<html lang="{'ko' if is_korean else 'en'}">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{company_display} {report_type}</title>
    <style>
    {css_content}
    </style>
</head>
<body>
    {header_html}
    <main class="report-content">
    {html}
    </main>
    {footer_html}
</body>
</html>
            """
        else:
            full_html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
</head>
<body>
    {html}
</body>
</html>
            """

        # Apply logo watermark to background
        if enable_watermark and logo_path:
            full_html = create_watermark(full_html, logo_path, watermark_opacity)

        return full_html

    except Exception as e:
        logger.error(f"Error during HTML conversion: {str(e)}")
        raise

def _ensure_playwright_browser():
    """
    Check if Playwright browser is installed, and auto-install if not

    Uses subprocess only for safe operation regardless of asyncio event loop

    Returns:
        bool: Browser installation success status
    """
    import subprocess
    import sys

    try:
        # Check browser info using subprocess (no sync API usage)
        # Check installed browsers with playwright show command
        check_result = subprocess.run(
            [sys.executable, "-m", "playwright", "show"],
            capture_output=True,
            text=True,
            timeout=10
        )

        # Check if chromium is installed
        if "chromium" in check_result.stdout.lower() and "executable" in check_result.stdout.lower():
            logger.debug("Playwright Chromium browser is already installed.")
            return True

        # Attempt auto-installation if not installed
        logger.warning("Playwright browser is not installed. Attempting auto-installation...")

        try:
            # Execute playwright install chromium
            result = subprocess.run(
                [sys.executable, "-m", "playwright", "install", "chromium"],
                capture_output=True,
                text=True,
                timeout=300  # 5 minute timeout
            )

            if result.returncode == 0:
                logger.info("Playwright browser installation complete")
                return True
            else:
                logger.error(f"Playwright browser installation failed: {result.stderr}")
                return False

        except subprocess.TimeoutExpired:
            logger.error("Playwright browser installation timeout")
            return False
        except Exception as install_error:
            logger.error(f"Error during Playwright browser auto-installation: {str(install_error)}")
            return False

    except subprocess.TimeoutExpired:
        logger.warning("Playwright browser check timeout - attempting installation...")
        # Attempt installation even on timeout
        try:
            result = subprocess.run(
                [sys.executable, "-m", "playwright", "install", "chromium"],
                capture_output=True,
                text=True,
                timeout=300
            )
            return result.returncode == 0
        except Exception:
            return False
    except Exception as e:
        logger.error(f"Error checking Playwright browser: {str(e)}")
        return False

def markdown_to_pdf_playwright(md_file_path, pdf_file_path, add_theme=False, logo_path=None, enable_watermark=False, watermark_opacity=0.02):
    """
    Convert Markdown to PDF using Playwright

    Modern alternative to wkhtmltopdf, uses Chromium-based rendering
    Works safely in asyncio environments
    Auto-installs browser if not present

    Installation:
    pip install playwright
    playwright install chromium

    Args:
        md_file_path (str): Markdown file path
        pdf_file_path (str): Output PDF file path
        add_theme (bool): Whether to add theme and logo
        logo_path (str): Logo image path (uses default logo if None)
        enable_watermark (bool): Whether to apply logo watermark to background
        watermark_opacity (float): Watermark opacity (0.0-1.0)
    """
    try:
        # Check and install Playwright browser
        if not _ensure_playwright_browser():
            raise RuntimeError("Cannot install Playwright browser. Please manually run 'playwright install chromium'.")

        # Check asyncio event loop
        import asyncio
        import concurrent.futures

        try:
            # Check if event loop is currently running
            loop = asyncio.get_running_loop()
            # If event loop is running, execute sync function in separate thread
            with concurrent.futures.ThreadPoolExecutor() as executor:
                future = executor.submit(
                    _markdown_to_pdf_playwright_sync,
                    md_file_path, pdf_file_path, add_theme, logo_path, enable_watermark, watermark_opacity
                )
                return future.result()
        except RuntimeError:
            # If no event loop, execute sync version directly
            _markdown_to_pdf_playwright_sync(
                md_file_path, pdf_file_path, add_theme, logo_path, enable_watermark, watermark_opacity
            )

    except ImportError:
        logger.error("Playwright library is not installed. Install with 'pip install playwright && playwright install chromium'.")
        raise
    except Exception as e:
        logger.error(f"Error during Playwright conversion: {str(e)}")
        raise

async def _markdown_to_pdf_playwright_async(md_file_path, pdf_file_path, add_theme, logo_path, enable_watermark, watermark_opacity):
    """PDF conversion using Playwright Async API (for asyncio environments)"""
    from playwright.async_api import async_playwright

    # Convert markdown to HTML
    html_content = markdown_to_html(
        md_file_path,
        add_theme=add_theme,
        logo_path=logo_path,
        enable_watermark=enable_watermark,
        watermark_opacity=watermark_opacity
    )

    # Save HTML to temporary file
    with tempfile.NamedTemporaryFile(suffix='.html', delete=False, mode='w', encoding='utf-8') as f:
        f.write(html_content)
        temp_html = f.name

    # Generate PDF with Playwright Async
    async with async_playwright() as p:
        # Chromium browser launch options
        # Additional args for headless mode and stability
        browser = await p.chromium.launch(
            headless=True,
            args=[
                '--disable-dev-shm-usage',  # Prevent shared memory issues
                '--no-sandbox',  # Disable sandbox (for container environments)
                '--disable-setuid-sandbox',
                '--disable-gpu',  # Disable GPU acceleration (unnecessary in headless)
            ]
        )
        page = await browser.new_page()

        # Load as file URL (allow local file access)
        await page.goto(f'file://{os.path.abspath(temp_html)}', wait_until='networkidle')

        # PDF generation options
        await page.pdf(
            path=pdf_file_path,
            format='A4',
            margin={
                'top': '20mm',
                'right': '20mm',
                'bottom': '20mm',
                'left': '20mm'
            },
            print_background=True  # Include background colors/images
        )

        await browser.close()

    # Delete temporary file
    os.unlink(temp_html)

    logger.info(f"PDF conversion complete with Playwright: {pdf_file_path}")

def _markdown_to_pdf_playwright_sync(md_file_path, pdf_file_path, add_theme, logo_path, enable_watermark, watermark_opacity):
    """PDF conversion using Playwright Sync API (for regular environments)"""
    from playwright.sync_api import sync_playwright

    # Convert markdown to HTML
    html_content = markdown_to_html(
        md_file_path,
        add_theme=add_theme,
        logo_path=logo_path,
        enable_watermark=enable_watermark,
        watermark_opacity=watermark_opacity
    )

    # Save HTML to temporary file
    with tempfile.NamedTemporaryFile(suffix='.html', delete=False, mode='w', encoding='utf-8') as f:
        f.write(html_content)
        temp_html = f.name

    # Generate PDF with Playwright Sync
    with sync_playwright() as p:
        # Chromium browser launch options
        # Additional args for headless mode and stability
        browser = p.chromium.launch(
            headless=True,
            args=[
                '--disable-dev-shm-usage',  # Prevent shared memory issues
                '--no-sandbox',  # Disable sandbox (for container environments)
                '--disable-setuid-sandbox',
                '--disable-gpu',  # Disable GPU acceleration (unnecessary in headless)
            ]
        )
        page = browser.new_page()

        # Load as file URL (allow local file access)
        page.goto(f'file://{os.path.abspath(temp_html)}', wait_until='networkidle')

        # PDF generation options
        page.pdf(
            path=pdf_file_path,
            format='A4',
            margin={
                'top': '20mm',
                'right': '20mm',
                'bottom': '20mm',
                'left': '20mm'
            },
            print_background=True  # Include background colors/images
        )

        browser.close()

    # Delete temporary file
    os.unlink(temp_html)

    logger.info(f"PDF conversion complete with Playwright: {pdf_file_path}")

def markdown_to_pdf_pdfkit(md_file_path, pdf_file_path, add_theme=False, logo_path=None, enable_watermark=False, watermark_opacity=0.02):
    """
    Convert Markdown to PDF using pdfkit (wkhtmltopdf)

    ⚠️ Warning: wkhtmltopdf was archived in 2023 and is no longer maintained.
    Using Playwright is recommended.

    Linux installation: dnf install wkhtmltopdf

    Args:
        md_file_path (str): Markdown file path
        pdf_file_path (str): Output PDF file path
        add_theme (bool): Whether to add theme and logo
        logo_path (str): Logo image path (uses default logo if None)
        enable_watermark (bool): Whether to apply logo watermark to background
        watermark_opacity (float): Watermark opacity (0.0-1.0)
    """
    try:
        # Import pdfkit (requires installation: pip install pdfkit + wkhtmltopdf binary)
        import pdfkit

        # Convert markdown to HTML
        html_content = markdown_to_html(
            md_file_path,
            add_theme=add_theme,
            logo_path=logo_path,
            enable_watermark=enable_watermark,
            watermark_opacity=watermark_opacity
        )

        # Save HTML to temporary file
        with tempfile.NamedTemporaryFile(suffix='.html', delete=False) as f:
            f.write(html_content.encode('utf-8'))
            temp_html = f.name

        # Option settings
        options = {
            'encoding': 'UTF-8',
            'page-size': 'A4',
            'margin-top': '20mm',
            'margin-right': '20mm',
            'margin-bottom': '20mm',
            'margin-left': '20mm',
            'enable-local-file-access': None,
            'quiet': '',
            # Additional options for wkhtmltopdf
            'enable-javascript': None,
            'javascript-delay': '1000',  # Wait time for JavaScript execution (milliseconds)
            'no-stop-slow-scripts': None,
            'debug-javascript': None
        }

        # Convert HTML to PDF
        pdfkit.from_file(temp_html, pdf_file_path, options=options)

        # Delete temporary file
        os.unlink(temp_html)

        logger.info(f"PDF conversion complete with pdfkit: {pdf_file_path}")

    except ImportError:
        logger.error("pdfkit library is not installed. Install with pip install pdfkit.")
        raise
    except Exception as e:
        logger.error(f"Error during pdfkit conversion: {str(e)}")
        raise

def markdown_to_pdf_reportlab(md_file_path, pdf_file_path):
    """
    Convert Markdown to PDF directly using ReportLab

    Args:
        md_file_path (str): Markdown file path
        pdf_file_path (str): Output PDF file path
    """
    try:
        # Import ReportLab (requires installation: pip install reportlab)
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import inch
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
        from reportlab.lib import colors

        # Read markdown file
        with open(md_file_path, 'r', encoding='utf-8') as f:
            md_content = f.read()

        # Extract title (first line starting with #)
        title = "Report"
        for line in md_content.split('\n'):
            if line.startswith('# '):
                title = line[2:].strip()
                break

        # Create PDF document
        doc = SimpleDocTemplate(
            pdf_file_path,
            pagesize=A4,
            rightMargin=72,
            leftMargin=72,
            topMargin=72,
            bottomMargin=72
        )

        # Style settings
        styles = getSampleStyleSheet()
        styles.add(ParagraphStyle(
            name='Heading1',
            parent=styles['Heading1'],
            fontSize=16,
            spaceAfter=12
        ))
        styles.add(ParagraphStyle(
            name='Heading2',
            parent=styles['Heading2'],
            fontSize=14,
            spaceBefore=12,
            spaceAfter=6
        ))
        styles.add(ParagraphStyle(
            name='Heading3',
            parent=styles['Heading3'],
            fontSize=12,
            spaceBefore=10,
            spaceAfter=4
        ))

        # Parse and convert content (simple implementation)
        elements = []

        # Add title
        elements.append(Paragraph(title, styles['Heading1']))
        elements.append(Spacer(1, 0.25*inch))

        # Add content (simple parsing)
        current_section = []
        in_code_block = False

        for line in md_content.split('\n'):
            # Handle code blocks
            if line.startswith('```'):
                in_code_block = not in_code_block
                continue

            if in_code_block:
                current_section.append(line)
                continue

            # Handle headings
            if line.startswith('# '):
                if current_section:
                    elements.append(Paragraph('\n'.join(current_section), styles['Normal']))
                    current_section = []
                elements.append(Paragraph(line[2:], styles['Heading1']))
            elif line.startswith('## '):
                if current_section:
                    elements.append(Paragraph('\n'.join(current_section), styles['Normal']))
                    current_section = []
                elements.append(Paragraph(line[3:], styles['Heading2']))
            elif line.startswith('### '):
                if current_section:
                    elements.append(Paragraph('\n'.join(current_section), styles['Normal']))
                    current_section = []
                elements.append(Paragraph(line[4:], styles['Heading3']))
            # Regular text
            elif line.strip():
                current_section.append(line)
            # Empty lines separate paragraphs
            elif current_section:
                elements.append(Paragraph('\n'.join(current_section), styles['Normal']))
                current_section = []
                elements.append(Spacer(1, 0.1*inch))

        # Add last section
        if current_section:
            elements.append(Paragraph('\n'.join(current_section), styles['Normal']))

        # Build PDF
        doc.build(elements)

        logger.info(f"PDF conversion complete with ReportLab: {pdf_file_path}")

    except ImportError:
        logger.error("ReportLab library is not installed. Install with pip install reportlab.")
        raise
    except Exception as e:
        logger.error(f"Error during ReportLab conversion: {str(e)}")
        raise

def markdown_to_pdf_mdpdf(md_file_path, pdf_file_path):
    """
    Convert Markdown to PDF using mdpdf library

    Installation: pip install mdpdf

    Args:
        md_file_path (str): Markdown file path
        pdf_file_path (str): Output PDF file path
    """
    try:
        # Import mdpdf (requires installation: pip install mdpdf)
        from mdpdf import MarkdownPdf

        # Convert markdown to PDF
        md = MarkdownPdf()
        md.convert(md_file_path, pdf_file_path)

        logger.info(f"PDF conversion complete with mdpdf: {pdf_file_path}")

    except ImportError:
        logger.error("mdpdf library is not installed. Install with pip install mdpdf.")
        raise
    except Exception as e:
        logger.error(f"Error during mdpdf conversion: {str(e)}")
        raise

def markdown_to_pdf(md_file_path, pdf_file_path, method='playwright', add_theme=False, logo_path=None, enable_watermark=False, watermark_opacity=0.02):
    """
    Convert Markdown file to PDF (default method selection)

    Args:
        md_file_path (str): Markdown file path
        pdf_file_path (str): Output PDF file path
        method (str): Conversion method ('playwright', 'pdfkit', 'reportlab', 'mdpdf')
                     Default: 'playwright' (recommended)
        add_theme (bool): Whether to add theme and logo
        logo_path (str): Logo image path (uses default logo if None)
        enable_watermark (bool): Whether to apply logo watermark to background
        watermark_opacity (float): Watermark opacity (0.0-1.0)
    """
    logger.info(f"Starting Markdown to PDF conversion: {md_file_path} -> {pdf_file_path}")

    try:
        if method == 'playwright':
            markdown_to_pdf_playwright(md_file_path, pdf_file_path, add_theme, logo_path, enable_watermark, watermark_opacity)
        elif method == 'pdfkit':
            markdown_to_pdf_pdfkit(md_file_path, pdf_file_path, add_theme, logo_path, enable_watermark, watermark_opacity)
        elif method == 'reportlab':
            # Note: reportlab method currently does not support themes
            markdown_to_pdf_reportlab(md_file_path, pdf_file_path)
        elif method == 'mdpdf':
            # Note: mdpdf method currently does not support themes
            markdown_to_pdf_mdpdf(md_file_path, pdf_file_path)
        else:
            # Default tries playwright first, then pdfkit, reportlab, and finally mdpdf
            try:
                markdown_to_pdf_playwright(md_file_path, pdf_file_path, add_theme, logo_path, enable_watermark, watermark_opacity)
            except Exception as e1:
                logger.warning(f"Playwright failed, trying pdfkit: {str(e1)}")
                try:
                    markdown_to_pdf_pdfkit(md_file_path, pdf_file_path, add_theme, logo_path, enable_watermark, watermark_opacity)
                except Exception as e2:
                    logger.warning(f"pdfkit failed, trying ReportLab: {str(e2)}")
                    try:
                        markdown_to_pdf_reportlab(md_file_path, pdf_file_path)
                    except Exception as e3:
                        logger.warning(f"ReportLab failed, trying mdpdf: {str(e3)}")
                        markdown_to_pdf_mdpdf(md_file_path, pdf_file_path)

    except Exception as e:
        logger.error(f"PDF conversion failed: {str(e)}")
        raise


# Extract text from PDF
def extract_text_from_pdf(pdf_path):
    with open(pdf_path, 'rb') as file:
        reader = PyPDF2.PdfReader(file)
        text = ""
        for page in reader.pages:
            text += page.extract_text()
    return text

# Convert text to markdown
def convert_to_markdown(text):
    h = html2text.HTML2Text()
    h.ignore_links = False
    markdown_text = h.handle(text)
    return markdown_text

# PDF to markdown_text
def pdf_to_markdown_text(pdf_path):
    text = extract_text_from_pdf(pdf_path)
    return convert_to_markdown(text)


if __name__ == "__main__":
    # Test code
    import sys

    if len(sys.argv) < 3:
        print(f"Usage: {sys.argv[0]} <markdown_file> <pdf_file> [method] [add_theme(true/false)] [enable_watermark(true/false)] [watermark_opacity(0.0-1.0)]")
        sys.exit(1)

    md_file = sys.argv[1]
    pdf_file = sys.argv[2]
    method = sys.argv[3] if len(sys.argv) > 3 else 'auto'
    add_theme = sys.argv[4].lower() == 'true' if len(sys.argv) > 4 else False
    enable_watermark = sys.argv[5].lower() == 'true' if len(sys.argv) > 5 else False
    watermark_opacity = float(sys.argv[6]) if len(sys.argv) > 6 else 0.02

    markdown_to_pdf(md_file, pdf_file, method, add_theme=add_theme, enable_watermark=enable_watermark, watermark_opacity=watermark_opacity)

    markdown_text_content = pdf_to_markdown_text(pdf_file)
    print(f"markdown_text_content: {markdown_text_content}")
