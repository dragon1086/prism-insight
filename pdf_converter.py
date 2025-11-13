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

# CSS styles
DEFAULT_CSS = """
body {
    font-family: Arial, sans-serif;
    line-height: 1.6;
    margin: 40px;
    color: #333;
    max-width: 900px;
    margin: 0 auto;
    padding: 20px;
}
h1 {
    color: #0056b3;
    border-bottom: 2px solid #ddd;
    padding-bottom: 5px;
}
h2 {
    color: #0056b3;
    border-bottom: 1px solid #eee;
    padding-bottom: 3px;
}
h3 {
    color: #0056b3;
}
table {
    border-collapse: collapse;
    width: 100%;
    margin: 20px 0;
}
th, td {
    border: 1px solid #ddd;
    padding: 8px;
    text-align: left;
}
th {
    background-color: #f2f2f2;
}
tr:nth-child(even) {
    background-color: #f9f9f9;
}
code {
    background-color: #f5f5f5;
    padding: 2px 4px;
    border-radius: 4px;
    font-family: Consolas, monospace;
}
blockquote {
    border-left: 4px solid #ddd;
    padding-left: 10px;
    margin-left: 10px;
    color: #555;
}
pre {
    background-color: #f5f5f5;
    padding: 10px;
    border-radius: 4px;
    overflow-x: auto;
}
"""

# Theme-related CSS
THEME_CSS = """
/* Prism Insight Theme */
body {
    background-color: #f0f8ff;  /* Light blue background */
    position: relative;
}

/* Background watermark style (dynamically added by create_watermark function) */

/* Header style */
.report-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    border-bottom: 3px solid #0056b3;
    margin-bottom: 30px;
    padding-bottom: 10px;
}

.report-header .title-area {
    flex: 1;
}

.report-header .logo {
    width: 120px;
    height: auto;
}

.report-date {
    color: #666;
    font-size: 0.9em;
    margin-top: 5px;
}

/* Container style */
.report-container {
    border-left: 5px solid #0056b3;  /* Blue similar to Prism logo */
    padding-left: 20px;
    background: linear-gradient(to right, rgba(240, 248, 255, 0.7), rgba(255, 255, 255, 1));
    padding: 20px;
    box-shadow: 0 2px 10px rgba(0, 0, 0, 0.1);
}

/* Footer style */
.report-footer {
    margin-top: 40px;
    padding-top: 10px;
    border-top: 1px solid #ddd;
    color: #666;
    font-size: 0.9em;
    text-align: center;
}
"""

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
    Convert Markdown file to HTML

    Args:
        md_file_path (str): Markdown file path
        add_css (bool): Whether to add CSS styles
        add_theme (bool): Whether to add theme and logo
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

        # Convert image paths to absolute paths (using regex)
        import re

        # Find HTML image tags
        img_tags_pattern = r'<img\s+src="data:image/png;base64,[^"]+"\s+[^>]+>'
        img_tags = re.findall(img_tags_pattern, md_content)

        # Replace with temporary placeholders
        for i, tag in enumerate(img_tags):
            placeholder = f"___HTML_IMG_TAG_{i}___"
            md_content = md_content.replace(tag, placeholder)

        # Process remaining images (existing code)
        def replace_image_path(match):
            img_path = match.group(2)
            if not os.path.isabs(img_path):
                img_path = os.path.abspath(os.path.join(base_dir, img_path))
            return f'![{match.group(1)}]({img_path})'

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
            placeholder = f"___HTML_IMG_TAG_{i}___"
            html = html.replace(placeholder, tag)

        # Attempt to extract markdown title
        title = "Report"
        for line in md_content.split('\n'):
            if line.startswith('# '):
                title = line[2:].strip()
                break

        # Format date
        today = datetime.now().strftime("%Y-%m-%d")

        # Set logo path
        if logo_path is None and (add_theme or enable_watermark):
            # Default logo path (assumed to be in project assets folder)
            script_dir = os.path.dirname(os.path.abspath(__file__))
            logo_path = os.path.join(script_dir, "assets", "prism_insight_logo.png")

        # HTML header and footer templates
        if add_theme:
            header_template = f"""
            <div class="report-container">
                <div class="report-header">
                    <div class="title-area">
                        <h1>{title}</h1>
                        <div class="report-date">Publication date: {today}</div>
                    </div>
                    <img src="{logo_path}" alt="Prism Insight Logo" class="logo">
                </div>
            """

            footer_template = """
                <div class="report-footer">
                    <p>© 2025 Prism Insight. All rights reserved.</p>
                </div>
            </div>
            """
        else:
            header_template = ""
            footer_template = ""

        # Create complete HTML document
        if add_css:
            css_content = DEFAULT_CSS
            if add_theme:
                css_content += THEME_CSS

            full_html = f"""
            <!DOCTYPE html>
            <html>
            <head>
                <meta charset="UTF-8">
                <style>
                {css_content}
                </style>
            </head>
            <body>
                {header_template}
                {html}
                {footer_template}
            </body>
            </html>
            """
        else:
            full_html = f"""
            <!DOCTYPE html>
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
