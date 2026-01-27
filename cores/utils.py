import re
import subprocess

# WiseReport URL 템플릿 설정
WISE_REPORT_BASE = "https://comp.wisereport.co.kr/company/"
URLS = {
    "기업현황": "c1010001.aspx?cmp_cd={}",
    "기업개요": "c1020001.aspx?cmp_cd={}",
    "재무분석": "c1030001.aspx?cmp_cd={}",
    "투자지표": "c1040001.aspx?cmp_cd={}",
    "컨센서스": "c1050001.aspx?cmp_cd={}",
    "경쟁사분석": "c1060001.aspx?cmp_cd={}",
    "지분현황": "c1070001.aspx?cmp_cd={}",
    "업종분석": "c1090001.aspx?cmp_cd={}",
    "최근리포트": "c1080001.aspx?cmp_cd={}"
}


def clean_markdown(text: str) -> str:
    """마크다운 텍스트 정리"""

    # 0. GPT-5.2 artifact 제거
    # Tool call JSON 패턴 제거 (예: {"name":"kospi_kosdaq-get_stock_ohlcv","arguments":{...}})
    text = re.sub(r'\{"name":\s*"[^"]+",\s*"arguments":\s*\{[^}]*\}\}', '', text)
    # 내부 토큰 제거 (예: <|ipynb_marker|>, <|endoftext|> 등)
    text = re.sub(r'<\|[^|]+\|>', '', text)

    # 1. 백틱 블록 제거
    text = re.sub(r'```[^\n]*\n(.*?)\n```', r'\1', text, flags=re.DOTALL)

    # 2. 개행문자 리터럴을 실제 개행으로 변환 (GPT-5.2 호환)
    # 먼저 이중 개행 처리
    text = text.replace('\\n\\n', '\n\n')
    # 단일 개행 처리
    text = text.replace('\\n', '\n')

    # 3. 한글 사이에 끼어든 불필요한 개행 제거 (GPT-5.2 출력 정리)
    # 예: "코\n리\n아" -> "코리아" (반복 적용)
    prev_text = None
    while prev_text != text:
        prev_text = text
        text = re.sub(r'([가-힣])\n([가-힣])', r'\1\2', text)

    # 4. 테이블 행 내부의 개행 제거 (마크다운 테이블 수정)
    # 테이블 행은 | 로 시작하고 | 로 끝나야 함
    lines = text.split('\n')
    cleaned_lines = []
    i = 0
    while i < len(lines):
        line = lines[i]
        # 테이블 행이 | 로 시작하지만 | 로 끝나지 않으면 다음 줄과 병합
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

    # 5. 마크다운 헤딩 보존 및 정리
    # 정상적인 섹션 제목 키워드 (길이 제한 완화: 50자까지 허용)
    valid_section_keywords = [
        # Korean keywords
        '분석', '현황', '개요', '전략', '요약', '지표', '동향', '차트', '투자',
        '기술적', '펀더멘털', '뉴스', '시장', '핵심', '포인트', '의견',
        # English keywords
        'Analysis', 'Overview', 'Status', 'Strategy', 'Summary', 'Chart',
        'Technical', 'Fundamental', 'News', 'Market', 'Investment', 'Key', 'Point', 'Opinion',
        # Numbered section patterns
        '1.', '2.', '3.', '4.', '5.', '1-1', '1-2', '2-1', '2-2', '3-1', '4-1', '5-1',
        'Executive'
    ]

    def is_valid_section_header(header_text):
        """정상적인 섹션 헤더인지 확인"""
        header_text = header_text.strip()
        # 50자 이하이고, 키워드 포함시 정상 헤더로 간주
        if len(header_text) <= 50:
            for keyword in valid_section_keywords:
                if keyword in header_text:
                    return True
        # 숫자로 시작하는 짧은 제목 (예: "1. 기술적 분석")
        if len(header_text) <= 50 and header_text and header_text[0].isdigit():
            return True
        return False

    lines = text.split('\n')
    processed_lines = []
    for i, line in enumerate(lines):
        stripped = line.strip()
        # # ~ #### 헤딩 처리 (모든 유효한 마크다운 헤딩 레벨 보존)
        heading_match = re.match(r'^(#{1,4})\s+(.+)$', stripped)
        if heading_match:
            heading_level = heading_match.group(1)  # #, ##, ###, or ####
            header_content = heading_match.group(2)
            if is_valid_section_header(header_content):
                # 유효한 섹션 헤더는 그대로 유지
                processed_lines.append(stripped)
            else:
                # 50자 초과 또는 키워드 없는 경우 ## 이상은 텍스트로 변환
                if len(heading_level) >= 2:
                    # 강조용으로 사용된 헤딩은 제거
                    processed_lines.append(header_content)
                else:
                    # # (h1)은 그대로 유지 (보고서 제목)
                    processed_lines.append(stripped)
        else:
            processed_lines.append(line)
    text = '\n'.join(processed_lines)

    # 5-1. 헤딩 전후에 빈 줄 보장
    # 헤딩 앞에 빈 줄이 없으면 추가
    text = re.sub(r'([^\n])\n(#{1,4}\s)', r'\1\n\n\2', text)
    # 헤딩 뒤에 빈 줄이 없으면 추가
    text = re.sub(r'(#{1,4}\s[^\n]+)\n([^\n#])', r'\1\n\n\2', text)

    # 5-2. 테이블 전후에 빈 줄 보장 (마크다운 테이블 파싱을 위해 필수)
    lines = text.split('\n')
    result_lines = []
    for i, line in enumerate(lines):
        stripped = line.strip()
        is_table_line = stripped.startswith('|')
        prev_line = lines[i - 1].strip() if i > 0 else ''
        prev_is_table = prev_line.startswith('|')
        prev_is_empty = prev_line == ''

        # 테이블 시작 전에 빈 줄 추가 (이전 줄이 테이블이 아니고 빈 줄도 아닌 경우)
        if is_table_line and not prev_is_table and not prev_is_empty:
            result_lines.append('')

        result_lines.append(line)

        # 테이블 끝 후에 빈 줄 추가 (다음 줄이 테이블이 아니고 빈 줄도 아닌 경우)
        # 이 부분은 다음 iteration에서 처리됨

    # 테이블 끝 후 빈 줄 추가
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

    text = '\n'.join(final_lines)

    # 6. 헤더/소제목 뒤에 누락된 개행 추가 (GPT-5.2가 개행 없이 붙여쓴 경우)
    # 패턴: "관점본" -> "관점\n\n본", "계획다음" -> "계획\n\n다음"
    header_endings = ['관점', '계획', '해석', '동향', '현황', '개요', '전략', '요약', '배경', '결론']
    sentence_starters = ['본', '다음', '이는', '이번', '해당', '실제', '현재', '그러', '따라', '특히', '또한', '다만', '한편']

    for ending in header_endings:
        for starter in sentence_starters:
            # "관점본" -> "관점\n\n본" (개행 없이 붙어있는 경우)
            text = text.replace(f'{ending}{starter}', f'{ending}\n\n{starter}')

    # 7. 번호 매긴 소제목 뒤 누락된 개행 추가
    # 패턴: "4) 미래 계획다음은" -> "4) 미래 계획\n\n다음은"
    for starter in sentence_starters:
        # "계획다음" 같은 패턴 처리 (위에서 이미 처리됨)
        # 추가로 "n) 제목단어" 패턴도 처리
        text = re.sub(rf'(\d+\)\s*[가-힣]+\s*(?:계획|현황|분석|동향|개요|배경))({starter})', rf'\1\n\n\2', text)

    return text


def get_wise_report_url(report_type: str, company_code: str) -> str:
    """WiseReport URL 생성"""
    return WISE_REPORT_BASE + URLS[report_type].format(company_code)
