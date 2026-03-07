import json
import logging
import re
from typing import Any, Dict, Optional

# WiseReport URL template configuration
WISE_REPORT_BASE = "https://comp.wisereport.co.kr/company/"
URLS = {
    "기업현황": "c1010001.aspx?cmp_cd={}",  # Company status (Korean key for API)
    "기업개요": "c1020001.aspx?cmp_cd={}",  # Company overview (Korean key for API)
    "재무분석": "c1030001.aspx?cmp_cd={}",  # Financial analysis (Korean key for API)
    "투자지표": "c1040001.aspx?cmp_cd={}",  # Investment indicators (Korean key for API)
    "컨센서스": "c1050001.aspx?cmp_cd={}",  # Consensus (Korean key for API)
    "경쟁사분석": "c1060001.aspx?cmp_cd={}",  # Competitor analysis (Korean key for API)
    "지분현황": "c1070001.aspx?cmp_cd={}",  # Shareholding status (Korean key for API)
    "업종분석": "c1090001.aspx?cmp_cd={}",  # Industry analysis (Korean key for API)
    "최근리포트": "c1080001.aspx?cmp_cd={}"  # Recent reports (Korean key for API)
}


def _clean_llm_artifacts(text: str) -> str:
    # 0. Remove GPT-5.2 artifacts
    text = re.sub(r'\{"name":\s*"[^"]+",\s*"arguments":\s*\{[^}]*\}\}', '', text)
    text = re.sub(r'<\|[^|]+\|>', '', text)
    # 1. Remove backtick code blocks
    text = re.sub(r'```[^\n]*\n(.*?)\n```', r'\1', text, flags=re.DOTALL)
    return text

def _fix_line_breaks(text: str) -> str:
    text = text.replace('\\n\\n', '\n\n').replace('\\n', '\n')
    prev_text = None
    while prev_text != text:
        prev_text = text
        text = re.sub(r'([가-힣])\n([가-힣])', r'\1\2', text)
    
    header_endings = ['관점', '계획', '해석', '동향', '현황', '개요', '전략', '요약', '배경', '결론']
    sentence_starters = ['본', '다음', '이는', '이번', '해당', '실제', '현재', '그러', '따라', '특히', '또한', '다만', '한편']

    # Optimized: single compiled regex instead of O(n×m) nested loop
    _endings_pattern = '|'.join(re.escape(e) for e in header_endings)
    _starters_pattern = '|'.join(re.escape(s) for s in sentence_starters)
    _combined_re = re.compile(rf'({_endings_pattern})({_starters_pattern})')
    text = _combined_re.sub(r'\1\n\n\2', text)

    for starter in sentence_starters:
        text = re.sub(r'(\d+\)\s*[가-힣]+\s*(?:계획|현황|분석|동향|개요|배경))(' + starter + ')', r'\1\n\n\2', text)
    return text

def _fix_markdown_tables(text: str) -> str:
    lines = text.split('\n')
    cleaned_lines = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.strip().startswith('|') and not line.strip().endswith('|'):
            merged = line
            while i + 1 < len(lines) and not merged.strip().endswith('|'):
                i += 1
                merged += lines[i]
            cleaned_lines.append(merged)
        else:
            cleaned_lines.append(line)
        i += 1
    
    text = '\n'.join(cleaned_lines)
    lines = text.split('\n')
    result_lines = []
    for i, line in enumerate(lines):
        stripped = line.strip()
        is_table_line = stripped.startswith('|')
        prev_line = lines[i - 1].strip() if i > 0 else ''
        prev_is_table = prev_line.startswith('|')
        prev_is_empty = prev_line == ''
        if is_table_line and not prev_is_table and not prev_is_empty:
            result_lines.append('')
        result_lines.append(line)
        
    final_lines = []
    for i, line in enumerate(result_lines):
        final_lines.append(line)
        stripped = line.strip()
        is_table_line = stripped.startswith('|')
        if is_table_line and i + 1 < len(result_lines):
            next_line = result_lines[i + 1].strip()
            next_is_table = next_line.startswith('|')
            next_is_empty = next_line == ''
            if not next_is_table and not next_is_empty:
                final_lines.append('')
    return '\n'.join(final_lines)

def _preserve_headings(text: str) -> str:
    valid_section_keywords = [
        '분석', '현황', '개요', '전략', '요약', '지표', '동향', '차트', '투자',
        '기술적', '펀더멘털', '뉴스', '시장', '핵심', '포인트', '의견',
        'Analysis', 'Overview', 'Status', 'Strategy', 'Summary', 'Chart',
        'Technical', 'Fundamental', 'News', 'Market', 'Investment', 'Key', 'Point', 'Opinion',
        '1.', '2.', '3.', '4.', '5.', '1-1', '1-2', '2-1', '2-2', '3-1', '4-1', '5-1',
        'Executive'
    ]
    def is_valid_section_header(header_text):
        header_text = header_text.strip()
        if len(header_text) <= 50:
            for keyword in valid_section_keywords:
                if keyword in header_text:
                    return True
        if len(header_text) <= 50 and header_text and header_text[0].isdigit():
            return True
        return False
        
    lines = text.split('\n')
    processed_lines = []
    for i, line in enumerate(lines):
        stripped = line.strip()
        heading_match = re.match(r'^(#{1,4})\s+(.+)$', stripped)
        if heading_match:
            heading_level = heading_match.group(1)
            header_content = heading_match.group(2)
            if is_valid_section_header(header_content):
                processed_lines.append(stripped)
            else:
                if len(heading_level) >= 2:
                    processed_lines.append(header_content)
                else:
                    processed_lines.append(stripped)
        else:
            processed_lines.append(line)
    text = '\n'.join(processed_lines)
    text = re.sub(r'([^\n])\n(#{1,4}\s)', r'\1\n\n\2', text)
    text = re.sub(r'(#{1,4}\s[^\n]+)\n([^\n#])', r'\1\n\n\2', text)
    return text

def clean_markdown(text: str) -> str:
    """Clean markdown text"""
    text = _clean_llm_artifacts(text)
    text = _fix_line_breaks(text)
    text = _fix_markdown_tables(text)
    text = _preserve_headings(text)
    return text


def get_wise_report_url(report_type: str, company_code: str) -> str:
    """Generate WiseReport URL"""
    return WISE_REPORT_BASE + URLS[report_type].format(company_code)


# --- LLM JSON Response Parsing ---
# Consolidates duplicated regex + json_repair fallback chains.
# TODO: Replace with generate_structured() + Pydantic models to eliminate JSON parsing entirely.

_json_logger = logging.getLogger(__name__)


def fix_json_syntax(json_str: str) -> str:
    """Fix common JSON syntax errors from LLM output."""
    # 1. Remove trailing commas before } or ]
    json_str = re.sub(r',(\s*[}\]])', r'\1', json_str)

    # 2. Add comma after ] before property
    json_str = re.sub(r'(\])\s*(\n\s*")', r'\1,\2', json_str)

    # 3. Add comma after } before property
    json_str = re.sub(r'(})\s*(\n\s*")', r'\1,\2', json_str)

    # 4. Add comma after number or string before property
    json_str = re.sub(r'([0-9]|")\s*(\n\s*")', r'\1,\2', json_str)

    # 5. Remove duplicate commas
    json_str = re.sub(r',\s*,', ',', json_str)

    return json_str


def _extract_json_string(response: str) -> Optional[str]:
    """Extract JSON object string from LLM response text."""
    # Strategy 1: Markdown code block
    markdown_match = re.search(r'```(?:json)?\s*({[\s\S]*?})\s*```', response, re.DOTALL)
    if markdown_match:
        return markdown_match.group(1)

    # Strategy 2: JSON object with nested braces support
    json_match = re.search(r'(\{(?:[^{}]|\{(?:[^{}]|\{[^{}]*\})*\})*\})', response, re.DOTALL)
    if json_match:
        return json_match.group(1)

    # Strategy 3: Full response is JSON
    clean = response.strip()
    if clean.startswith('{') and clean.endswith('}'):
        return clean

    return None


def _parse_strategy_1(json_str: str) -> Optional[Dict[str, Any]]:
    # Stage 2: Fix syntax + parse
    fixed = fix_json_syntax(json_str)
    return json.loads(fixed)

def _parse_strategy_2(json_str: str) -> Optional[Dict[str, Any]]:
    # Stage 3: Strip control characters + retry
    cleaned = re.sub(r'[\x00-\x1f\x7f-\x9f]', '', json_str)
    cleaned = fix_json_syntax(cleaned)
    return json.loads(cleaned)

def _parse_strategy_3(response: str) -> Optional[Dict[str, Any]]:
    # Stage 4: Aggressive cleanup
    aggressive = re.sub(r'```(?:json)?|```', '', response).strip()
    aggressive = re.sub(r'(\]|\})\s*(\n\s*"[^"]+"\s*:)', r'\1,\2', aggressive)
    aggressive = re.sub(r'(["\d\]\}])\s*\n\s*("[^"]+"\s*:)', r'\1,\n    \2', aggressive)
    aggressive = re.sub(r',(\s*[}\]])', r'\1', aggressive)
    aggressive = re.sub(r',\s*,+', ',', aggressive)
    return json.loads(aggressive)

def _parse_strategy_4(response: str) -> Optional[Dict[str, Any]]:
    # Stage 5: json_repair library
    import json_repair
    repaired = json_repair.repair_json(response)
    return json.loads(repaired)

def parse_llm_json(
    response: str,
    context: str = 'LLM response',
) -> Optional[Dict[str, Any]]:
    """Parse JSON from an LLM text response with multi-stage recovery."""
    if not response or not response.strip():
        _json_logger.warning(f'[{context}] Empty response received')
        return None

    # Stage 1: Extract JSON string
    json_str = _extract_json_string(response)
    if json_str is None:
        _json_logger.warning(f'[{context}] No JSON object found in response (length: {len(response)})')
        json_str = response

    strategies = [
        (_parse_strategy_1, json_str, "parsed successfully"),
        (_parse_strategy_2, json_str, "parsed after control character cleanup"),
        (_parse_strategy_3, response, "parsed with aggressive cleanup"),
        (_parse_strategy_4, response, "parsed via json_repair library")
    ]

    for strategy_fn, arg, log_msg in strategies:
        try:
            result = strategy_fn(arg)
            if result is not None:
                _json_logger.info(f'[{context}] JSON {log_msg}')
                return result
        except json.JSONDecodeError:
            continue
        except ImportError:
            if strategy_fn == _parse_strategy_4:
                _json_logger.debug(f'[{context}] json_repair not installed, skipping')
            continue
        except Exception:
            continue

    _json_logger.error(
        f'[{context}] All JSON parsing attempts failed. '
        f'Response preview: {response[:300]}...'
    )
    return None


def sanitize_report_for_telegram(markdown_text: str, max_length: int = 4096) -> str:
    """
    Sanitize a markdown report for Telegram messaging.

    Strips base64-encoded images, HTML tags, and truncates to Telegram's
    message length limit while preserving readable content.

    Args:
        markdown_text: Raw markdown report text
        max_length: Maximum message length (default: Telegram's 4096)

    Returns:
        Cleaned text suitable for Telegram messaging
    """
    text = markdown_text

    # Remove base64 image embeds (e.g., <img src="data:image/..."> or ![](data:image/...))
    text = re.sub(r'<img[^>]*src="data:image/[^"]*"[^>]*>', '', text, flags=re.IGNORECASE)
    text = re.sub(r'!\[[^\]]*\]\(data:image/[^)]*\)', '', text)

    # Remove HTML tags
    text = re.sub(r'<[^>]+>', '', text)

    # Remove consecutive blank lines (keep max 2)
    text = re.sub(r'\n{3,}', '\n\n', text)

    # Strip leading/trailing whitespace
    text = text.strip()

    # Truncate to max_length with ellipsis
    if len(text) > max_length:
        text = text[:max_length - 20] + "\n\n…(truncated)"

    return text


def extract_key_metrics(report_text: str) -> Dict[str, Any]:
    """
    Extract structured financial metrics from a markdown analysis report.

    Uses regex patterns to pull key financial figures for downstream use
    (e.g., alerts, database storage, API responses).

    Args:
        report_text: Completed markdown analysis report

    Returns:
        Dictionary with extracted metrics (values may be None if not found)
    """
    metrics: Dict[str, Any] = {}

    # Current price (Korean: 현재가, English: Current Price)
    price_match = re.search(
        r'(?:현재가|Current\s*Price|종가|Close)[:\s]*[₩$]?\s*([\d,]+)', report_text, re.IGNORECASE
    )
    if price_match:
        metrics['current_price'] = int(price_match.group(1).replace(',', ''))

    # Target price
    target_match = re.search(
        r'(?:목표가|Target\s*Price|적정가|Fair\s*Value)[:\s]*[₩$]?\s*([\d,]+)', report_text, re.IGNORECASE
    )
    if target_match:
        metrics['target_price'] = int(target_match.group(1).replace(',', ''))

    # PER (Price-to-Earnings Ratio)
    per_match = re.search(
        r'(?:PER|P/E)[:\s]*([\d.]+)\s*(?:배|x|times)?', report_text, re.IGNORECASE
    )
    if per_match:
        metrics['per'] = float(per_match.group(1))

    # PBR (Price-to-Book Ratio)
    pbr_match = re.search(
        r'(?:PBR|P/B)[:\s]*([\d.]+)\s*(?:배|x|times)?', report_text, re.IGNORECASE
    )
    if pbr_match:
        metrics['pbr'] = float(pbr_match.group(1))

    # Dividend yield
    div_match = re.search(
        r'(?:배당수익률|배당률|Dividend\s*Yield)[:\s]*([\d.]+)\s*%', report_text, re.IGNORECASE
    )
    if div_match:
        metrics['dividend_yield'] = float(div_match.group(1))

    # Market cap
    mcap_match = re.search(
        r'(?:시가총액|Market\s*Cap)[:\s]*(?:약\s*)?([₩$]?\s*[\d,.]+\s*(?:조|억|billion|trillion|B|T))',
        report_text, re.IGNORECASE
    )
    if mcap_match:
        metrics['market_cap'] = mcap_match.group(1).strip()

    return metrics
