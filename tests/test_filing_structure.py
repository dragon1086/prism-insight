from prism_core.filing_structure import parse_filing


def test_table_captions_notes_and_exact_offsets():
    text = '## III. 재무에 관한 사항\n\n### 3\\. 연결재무제표 주석\n\n5\\. 매출채권\n\n(1) 신용위험\n\n(단위: 천원)\n\n제 19 기 2025년 12월 31일 현재\n\n| 구분 | 당기 |\n| --- | --- |\n| 채권 | 123 |\n\n주1) 담보는 제공하지 않았습니다.\n\n다음 문단입니다.\n'
    blocks = parse_filing(text)
    table = next(b for b in blocks if b['kind'] == 'table')
    assert table['scope'] == 'consolidated'
    assert table['section_path'][-2:] == ['5. 매출채권', '(1) 신용위험']
    assert '천원' in table['unit_context'] and '2025' in table['period_context']
    assert '주1)' in table['text'] and '다음 문단' not in table['text']
    for b in blocks:
        assert text[b['start']:b['end']] == b['text']
        assert b['source_spans'] == [[b['start'], b['end']]]
    assert blocks == parse_filing(text)


def test_scopes_and_repeated_notes_do_not_merge():
    text = '## III. 재무에 관한 사항\n\n### 3. 연결재무제표 주석\n\n5. 매출채권\n\n| a | b |\n| 1 | 2 |\n\n### 5. 재무제표 주석\n\n5. 매출채권\n\n| a | b |\n| 1 | 2 |\n\n## IV. 이사의 경영진단\n\n일반 내용'
    blocks = parse_filing(text)
    tables = [b for b in blocks if b['kind'] == 'table']
    assert [b['scope'] for b in tables] == ['consolidated', 'standalone']
    assert tables[0]['block_id'] != tables[1]['block_id']
    assert blocks[-1]['scope'] == 'unknown'


def test_large_table_is_atomic_even_without_delimiter():
    text = '(단위: 백만원)\n\n| 구분 | 금액 |\n' + ''.join(f'| 항목{i} | {i} |\n' for i in range(2000)) + '\n※ 금액은 반올림됨\n'
    blocks = parse_filing(text)
    assert len(blocks) == 1 and blocks[0]['text'] == text
    assert blocks[0]['kind'] == 'table'


def test_numeric_prose_unknown_input_and_toc():
    text = '## 문서 목차\n\n[III. 재무에 관한 사항](#x)\n\n### 3. 연결재무제표 주석\n\n5. 매출채권\n\n1. 증가율은 20%입니다.\n\n1.23은 측정값이다.\n\n(2) 당기말 잔액은 다음과 같습니다.\n'
    blocks = parse_filing(text)
    assert not any('[III.' in b['text'] for b in blocks)
    for b in blocks[-3:]:
        assert b['section_path'][-1] == '5. 매출채권'
    assert parse_filing('plain prose. More prose.')[0]['section_path'] == []
    assert parse_filing('') == []


def test_units_in_pipe_wrapper_and_unicode_are_preserved():
    text = '| (단위：천원, ％) |\n| --- |\n| 제 20 기 2026년 06월 30일 현재 |\n| 금액 | 300 |\n\n(주1) 담보 제공\n후속 설명\n'
    block = parse_filing(text)[0]
    assert block['text'] == text
    assert '천원, ％' in block['unit_context']
    assert '2026년 06월 30일' in block['period_context']


def test_heading_after_table_note_starts_new_block():
    text = '### 3. 연결재무제표 주석\n\n5. 매출채권\n\n| 항목 | 값 |\n| 채권 | 1 |\n\n주) 담보 있음\n6. 재고자산\n\n재고 설명\n'
    blocks = parse_filing(text)
    table = next(b for b in blocks if b['kind'] == 'table')
    assert '6. 재고자산' not in table['text']
    assert blocks[-1]['section_path'][-1] == '6. 재고자산'


def test_context_does_not_leak_to_other_table():
    blocks = parse_filing('(단위: 원)\n\n| 구분 | 금액 |\n| a | 1 |\n\n설명\n\n| 구분 | 금액 |\n| b | 2 |\n')
    tables = [b for b in blocks if b['kind'] == 'table']
    assert '원' in tables[0]['unit_context']
    assert tables[1]['unit_context'] == ''


def test_caption_table_and_data_table_are_one_atomic_block():
    text = ('### 3. 연결재무제표 주석\n\n9\\. 매출채권 및 기타채권\n\n'
            '(1) 당기말 및 전기말 현재 상세내역은 다음과 같습니다.\n\n'
            '|     |     |\n| --- | --- |\n| (당기말) | (단위 : 천원) |\n\n'
            '| 구분 | 당기말 | 전기말 |\n| --- | --- | --- |\n'
            '| 매출채권 | 16,433,687 | 10,653,322 |\n\n주) 손실충당금 차감 전\n')
    blocks = parse_filing(text)
    tables = [b for b in blocks if b['kind'] == 'table']
    assert len(tables) == 1
    assert '16,433,687' in tables[0]['text'] and '주)' in tables[0]['text']
    assert '천원' in tables[0]['unit_context']
    assert text[tables[0]['start']:tables[0]['end']] == tables[0]['text']
    assert blocks[0]['is_heading'] and not tables[0]['is_heading']


def test_caption_merging_does_not_merge_independent_tables_or_cross_scope():
    text = ('### 3. 연결재무제표 주석\n\n| (단위: 원) |\n\n'
            '| a | 1 |\n\n| b | 2 |\n\n| (단위: 원) |\n\n'
            '### 5. 재무제표 주석\n\n| c | 3 |\n')
    tables = [b for b in parse_filing(text) if b['kind'] == 'table']
    assert len(tables) == 4
    assert '| a | 1 |' in tables[0]['text'] and '| b |' not in tables[0]['text']
    assert tables[-1]['scope'] == 'standalone' and not tables[-1]['unit_context']


def test_parenthesized_reporting_period_caption_merges_without_numeric_rows():
    for period in ('당기', '전기', '당분기', '전분기', '당반기', '전반기'):
        text = f'| ({period}) | (단위 : 천원) |\n\n| 구분 | 금액 |\n| --- | --- |\n| 채권 | 123 |\n'
        blocks = parse_filing(text)
        assert len(blocks) == 1
        assert blocks[0]['text'] == text
        assert period in blocks[0]['period_context']
    assert len(parse_filing('| (전기) | 123 |\n\n| (당기) | 456 |\n')) == 2


def test_non_statement_section_does_not_inherit_standalone_scope():
    text = '### 5. 재무제표 주석\n\n9. 채권\n\n금액 설명\n\n### 8. 기타 재무에 관한 사항\n\n기타 설명\n'
    blocks = parse_filing(text)
    assert blocks[-1]['scope'] == 'unknown'


def test_hyphen_numbered_notes_do_not_inherit_previous_note():
    text = ('### 3. 연결재무제표 주석\n\n18. 현금흐름\n\n현금 설명\n\n'
            '19-1. 우발채무 및 약정사항 (연결)\n\n계약 설명\n\n'
            '19-2\\. 담보제공 (연결)\n\n담보 설명\n')
    blocks = parse_filing(text)
    assert next(b for b in blocks if b['text'].strip() == '계약 설명')['section_path'][-1] == '19-1. 우발채무 및 약정사항 (연결)'
    assert blocks[-1]['section_path'][-1] == '19-2. 담보제공 (연결)'


def test_title_plain_period_and_unit_caption_wrapper_is_preserved():
    for period in ('당분기', '전분기', '당분기말', '전분기말', '당반기', '전반기'):
        text = (f'| 재고자산 평가 및 폐기손실 |\n| {period} | (단위 : 백만원) |\n\n'
                '| 구분 | 금액 |\n| --- | --- |\n| 손실 | 100 |\n')
        blocks = parse_filing(text)
        assert len(blocks) == 1 and blocks[0]['text'] == text
        assert period in blocks[0]['period_context']


def test_caption_wrapper_does_not_merge_amount_table_or_conflicting_period():
    text = ('| 재고 평가 |\n| 당분기 | (단위: 백만원) |\n| 손실 | 100 |\n\n'
            '| 구분 | 금액 |\n| 전기 | 200 |\n')
    assert len(parse_filing(text)) == 2
    text = ('| 재고 평가 |\n| 당분기 | (단위: 백만원) |\n\n'
            '| 재고 평가 |\n| 전분기 | (단위: 백만원) |\n\n'
            '| 구분 | 금액 |\n| 손실 | 200 |\n')
    blocks = parse_filing(text)
    assert len(blocks) == 2
    assert '당분기' in blocks[0]['text'] and '전분기' not in blocks[0]['text']
