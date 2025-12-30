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

    # 5. 본문 중간의 ## 헤더를 볼드체로 변환 (GPT-5.2가 강조용으로 사용한 경우)
    # 정상적인 섹션 제목은 짧고 (30자 이하), 특정 키워드 포함
    valid_section_keywords = [
        '분석', '현황', '개요', '전략', '요약', '지표', '동향', '차트',
        'Analysis', 'Overview', 'Status', 'Strategy', 'Summary', 'Chart'
    ]

    def is_valid_section_header(header_text):
        """정상적인 섹션 헤더인지 확인"""
        header_text = header_text.strip()
        # 30자 이하이고, 키워드 포함시 정상 헤더로 간주
        if len(header_text) <= 30:
            for keyword in valid_section_keywords:
                if keyword in header_text:
                    return True
        return False

    lines = text.split('\n')
    processed_lines = []
    for line in lines:
        stripped = line.strip()
        # ## 로 시작하는 라인 처리
        if stripped.startswith('## '):
            header_content = stripped[3:]  # "## " 이후 텍스트
            if is_valid_section_header(header_content):
                # 정상 섹션 헤더는 유지
                processed_lines.append(line)
            else:
                # 강조용으로 사용된 ##는 볼드체로 변환
                indent = line[:len(line) - len(line.lstrip())]
                processed_lines.append(f"{indent}**{header_content}**")
        else:
            processed_lines.append(line)
    text = '\n'.join(processed_lines)

    return text


def get_wise_report_url(report_type: str, company_code: str) -> str:
    """WiseReport URL 생성"""
    return WISE_REPORT_BASE + URLS[report_type].format(company_code)
