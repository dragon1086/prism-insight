# S3a 설계: 전체 HTML 표의 무손실 tuple 표현

상태: 독립 architect 설계 입력을 반영한 critic 검토 대기 문서입니다. 기준은 `3e290fd1`입니다. frozen G1~G3는 변경하지 않습니다. 이번 단계는 표현 변경이며 projection/ranking/cap 변경은 제외합니다.

## 배경과 범위

C5 이후 삼성생명 news 후보는 57개이며 필수 표 781/1018은 index 31/46입니다. 앞 24개 제한으로 둘 다 빠지고, 전자의 excerpt는 7,957바이트입니다. 전체 셀을 같은 다섯 필드 tuple로 표현한 메모리 측정은 각각 4,810/772바이트입니다. 최종 packet에는 추가 비용이 있으므로 이 수치를 6,000바이트 통과로 부르지 않습니다.

충당부채 표의 다른 열에도 불확실성·추정·진행 중 사건이 있습니다. 특정 두 열만 선택하도록 gold에 맞추는 대신 전체 셀의 반복 필드명부터 줄입니다. 의미 있는 부분 선택은 별도 S3b 설계에서 정당화해야 합니다.

## 고정 schema와 API

새 `prism_core/filing_html_codec.py`에 다음 순수 함수만 둡니다.

- `compact_html_table_excerpt(record) -> str`: 지원되는 COMPLETE 원본 table의 기존 text를 더 작은 codec로 반환하거나, 지원 밖/이득 없는 record에는 원래 text를 반환합니다. 입력을 수정하지 않습니다.
- `expand_html_table_excerpt(text) -> str`: 정확한 codec만 받아 canonical legacy excerpt로 복원합니다. malformed/unknown schema는 code-only ValueError를 내며 legacy처럼 해석하지 않습니다. 자동 내용 탐지로 prose를 codec로 취급하지 않습니다.

codec JSON 키는 정확히 `schema`, `cell_fields`, `shape`, `cells`입니다. schema는 `html_cell_tuples_v1`, cell_fields는 순서대로 `["row","col","rowspan","colspan","text"]`, shape는 원본 `[row_count,column_count]`입니다. 각 cell은 정확히 다섯 값의 tuple입니다. 원본 origin cell 전체와 순서를 유지하며 숫자 문자열/빈칸/dash/괄호/조건을 변환하지 않습니다. tag나 새 금융 해석 필드를 추가하지 않습니다.

legacy 복원은 `{"cells":[{"row":...,"col":...,"rowspan":...,"colspan":...,"text":...},...]}`를 기존 ensure_ascii=False/separators=(',', ':')로 직렬화한 결과입니다. encode 전에 이 canonical legacy와 record.text가 byte-exact 동일한지 확인합니다. COMPLETE 표의 모든 셀·좌표·span·shape를 검증하고 입력record에 layout_role/context_incomplete가 있으면 인코딩하지 않습니다. 원래 record/provenance는 변경하지 않습니다.

비용 비교는 excerpt 문자열 자체가 아니라 packet에서 쓰는 JSON-string 직렬화의 UTF-8 비용으로 합니다. encoder는 이 비용이 줄지 않으면 원문을 반환합니다. 최종 adapter는 encoding metadata를 포함한 완성된 원래 block과 새 block 전체의 동일 canonical JSON 비용을 비교하고 순감소할 때만 새 block을 채택합니다. 기존 provenance가 있을 때의 comma 비용도 포함됩니다. excerpt만 29바이트 줄고 metadata 때문에 block은 11바이트 늘어나는 3셀 반례는 기존 표현을 유지해야 합니다. 한 표에서 후보를 늘리거나 subset을 탐색하지 않습니다.

## 엄격한 decoder와 자원 계약

- encoded/expanded 문자열 각각 8 MiB, text 합계 2 MiB UTF-8. 이는 로컬 직렬화 방어 한도이며 운영 응답 2 MiB·총 취득 예산·최종 6,000바이트는 바꾸지 않습니다.
- rows/columns 각각 1~300, 적어도 한 축 80 이하, 면적과 origin cell 수 최대 12,000, cell 최소 1개.
- duplicate/extra/missing key, NaN/Infinity, 잘못된 Unicode, 잘못된 schema/field 순서를 거절합니다.
- json.loads 전에 문자열 인용/escape를 구별하는 단일 선형 preflight를 수행합니다. 문자열 밖의 최대 container depth는 3, 전체 여는 container 수는 12,010, comma/colon 합은 75,000으로 제한합니다. 정상 12,000-cell schema를 수용하면서 millions-container/scalar 또는 깊은 중첩 입력을 Python 객체 할당 전에 거절합니다. 인용 문자열 안의 괄호·comma는 구조로 세지 않습니다. 원문 byte 상한을 먼저 확인하고 malformed quote/escape는 정적 코드로 실패합니다.
- 좌표/span은 type is int만 허용하며 bool/float를 거절합니다. 음수 좌표·0 span·shape 밖 span·비정렬 cell·중복·겹침을 거절합니다.
- tuple arity/text 타입을 검사합니다. 문자열처럼 보이는 숫자를 숫자로 바꾸지 않습니다. 빈 grid slot은 parser와 같이 허용하지만 0으로 채우지 않습니다.
- cells/text와 최대 12,000 grid slots에 선형으로 동작합니다. 깊은 JSON/escape-heavy 입력도 코드만 담은 bounded failure로 처리합니다. decoded legacy의 직렬화 전후 크기도 검사합니다.
- expanded size는 고정 field/key/구분자 비용과 각 정수·문자열의 JSON-string 길이를 누적해 전체 출력 직렬화 전에 상한을 적용합니다. 실제 최종 직렬화 후에도 재검사합니다. 거대한 복원 결과를 먼저 만든 뒤 거절하지 않습니다.
- encoder의 지원 조건 불일치는 원래 text를 유지합니다. 인식된 decoder 형식이 잘못됐을 때는 fallback하지 않습니다. 호출자가 decoder를 쓸지 정하는 명시 metadata가 필요합니다.

## 통합 경계

`filing_report_evidence._record_blocks()`의 기존 source_text 구성·classification·routing·Firecrawl Markdown corroboration을 모두 수행한 **뒤** material_notes=True이면서 DART_VIEWER_HTML/FIRECRAWL_CLEANED_HTML table일 때만 적용합니다. encoded 값이 원문과 다를 때 provenance에 `excerpt_encoding=html_cell_tuples_v1`을 붙입니다. 그 외 provenance/context/footnote/source hash/원문 record는 동일해야 합니다. codec 문자열이 topic 분류 입력이 되어서는 안 됩니다.

material_notes=False, prose, Markdown-only, SEC inline은 byte-preserved입니다. layout-only/context-incomplete 제외, owner/topic/candidate 순서, source/topic 24개 제한과 SECTION_BYTES=6000은 유지합니다. report_insight_prefetch의 parser revision만 갱신합니다. 기본 OFF·네트워크·모델·주문 동작은 바꾸지 않습니다.

decoder는 audit/검증에서만 사용합니다. `tools/evaluate_large_filing_html.py`는 전달 provenance의 encoding이 있을 때 decoder로 원문 셀 JSON을 복원하고 해당 원래 parsed table의 canonical text와 비교하는 검증을 추가합니다. 기존 block→packet provenance/문맥 비교도 유지합니다. 모델용 packet을 미리 복원하여 압축 효과를 없애지 않습니다. `expand_filing_record()`의 기존 filing-ref 의미는 이번에 바꾸지 않습니다.

audit의 block→packet 비교 키에 excerpt_encoding의 존재·값을 포함합니다. marker가 삭제되거나 unknown으로 바뀌어도 이 비교에서 실패해야 합니다. decoded text는 provenance의 representation_sha256가 현재 parsed.source_sha256와 같고, source_path가 유일한 원래 table record를 가리킬 때만 그 record.text와 대조합니다. marker 삭제/unknown, source hash/위치 변조, 셀 값 변조를 모두 실패 회귀로 고정합니다. compact 공통 provenance reference는 원래 검증 helper로 펼친 뒤 비교합니다.

## 파일 소유 범위와 순서

1. 새 codec 회귀 `tests/test_filing_html_codec.py`를 RED로 고정합니다.
2. 새 codec 모듈과 `filing_report_evidence.py`의 material HTML 경계, `report_insight_prefetch.py` revision을 구현합니다.
3. DOM 감사 도구·테스트와 CI 연구 입력 목록에 반영합니다. capture code fingerprint에 새 모듈을 추가하고 누락 회귀를 갱신합니다.
4. 독립 코드 리뷰 뒤 동일 private 원문/packet에서 원문 전체 셀 roundtrip과 변경된 실제 전달을 측정합니다. 원문은 공개하거나 첨부하지 않습니다.

## 회귀·합격 기준

- 1×1/직사각형/multirow header/rowspan/colspan/ragged grid, 한글·영문·통화·괄호·dash·빈칸, 마지막 긴 조건, quote/backslash/HTML처럼 보이는 문자열. decode(encode(text))가 byte-exact이고 input mutation이 없어야 합니다. 커지는 작은 표에는 적용하지 않습니다.
- 잘못된 모든 키/schema/필드순서/tuple/타입/좌표/span/정렬/겹침/면적/cell 수, text/encoded/expanded byte 경계와 초과, 깊은 JSON/escape-heavy 입력을 거절합니다. 오류에 원문이 없어야 합니다.
- 실제 adapter에서 owner/topic/candidate 순서·개수가 같고 decoded excerpt와 provenance(새 encoding 제외)가 같아야 합니다. Markdown corroboration 실패를 우회하면 안 됩니다. structured/prose/Markdown/SEC/OFF의 기존 출력은 그대로여야 합니다.
- 실제 최종 packet UTF-8≤6,000, filing/provenance ref·단위·조건·XPath 유지, 전달된 모든 codec 셀의 원문 대조를 확인합니다. 코드 변경으로 앞 후보가 더 들어가 뒤 후보가 밀릴 수 있으므로 최종 전달 집합이 같다고 가정하지 않습니다.
- 원문 6개 이상의 다양한 업종과 기존 대형 fixture를 사용하되 개발 회귀를 새 holdout 성공으로 세지 않습니다. 신규 의존성은 없습니다. Ruff·구문·관련 연구 입력 회귀·독립 리뷰·정확한 HEAD CI를 확인합니다.

G1/G2와 G3는 여전히 cap 뒤에 있으므로 gold 미통과가 예상되며 S3a 성공과 분리합니다. S3b의 관련성 기준·생략 허용 범위·전체 예산 충돌을 방어 가능하게 정하지 못하면 사용자에게 실제 선택지를 설명하고 지원을 요청합니다. gold나 예산을 몰래 바꾸지 않습니다. 머지·배포는 전체 검증 이후에만 가능합니다.
