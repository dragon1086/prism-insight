# S2c-5 설계: 원문 layout 표의 문맥 연결

## 목적과 범위

S3 독립 gold의 단위·기간과 POSCO PF 조건이 별도 작은 HTML table에 있어 기존 prose 문맥 연결에서 빠집니다. 회사·표 번호별 분기 없이 구조가 명확한 caption/unit 및 각주 table만 인식합니다. 수치 표를 prose로 바꾸거나 원문 record를 삭제하지 않습니다. 기존 기하 파서·원본 text/table/좌표·scope는 유지하고 metadata만 추가합니다. 중첩 표, 금융 수치 투영·우선순위, 모델 검증은 이번 단계 밖입니다.

## 인식 계약

정상 COMPLETE, non-nested table만 대상으로 합니다. class token에 `nb`가 있고 border가 없거나 0이어야 하며 thead/tfoot/th/rowspan이 없어야 합니다. hidden/active content는 기존 파서가 거절하며 script/style이 있는 layout도 거절합니다.

native `<caption>`과 셀 바깥의 공백 아닌 text/tail이 있으면 layout으로 재분류하지 않습니다. 구조 요소는 table/tbody/tr/td와 td 내부의 안전한 기존 inline text에 한정하며 셀 밖의 설명을 버리지 않습니다. 지원 밖 표는 이전 layout 문맥을 끊는 기존 data/unsupported 경로에 남깁니다.

실제 원문 검증 피드백: 지정한 삼성생명 caption에는 비어 있는 `<colgroup><col width="…"/>…</colgroup>`가 있습니다. 초기 문법은 이것도 거절해 실측 연결에 실패했습니다. table의 직접 자식인 colgroup 최대 1개, 그 안의 직접 col만 허용하며 col 개수는 표의 column_count와 같아야 합니다. colgroup은 속성·text가 없고, col은 선택적 숫자 width 외 속성·자식·text가 없어야 합니다. 모든 tail은 공백만 허용합니다. span·조건문·숨김·다른 child를 넣은 colgroup은 거절합니다. 이는 geometry나 source 값을 바꾸지 않는 관측 문법 확대이며 별도 RED 회귀·설계 재검토 후 반영합니다.

1. caption: 정확히 2행·3 origin cells. 첫 행 td colspan=2 한 개, 다음 행 span 없는 td 두 개. 제목은 공백 정규화 후 1~240자이며 문자(한글/영문)를 포함해야 합니다. 기간은 `(?:당|전)(?:기|반기|분기)(?:말)?` 전체 일치 또는 빈 문자열입니다. 단위는 괄호를 포함한 `(단위 : 원|천원|백만원|억원|USD|천USD|백만USD|달러|천달러|백만달러)` 전체 일치, 공백은 허용하되 값은 변환하지 않습니다. 지원 밖 형태는 원래 table로 남습니다.
2. period_unit: 정확히 1행·2개의 span 없는 td이며 위 기간/단위 계약을 만족합니다. 앞의 제목을 자동 계승하지 않고 이전 문맥을 대체합니다.
3. footnote: 정확히 1행·1셀, span 없는 td이며 기존 `_FOOT` marker로 시작합니다. 원문 전체를 보존합니다. 최대 4,096 UTF-8바이트; 이를 넘으면 연결하지 않고 `LAYOUT_CONTEXT_LIMIT` gap을 기록하며 target을 끊습니다. 여러 각주의 합도 같은 한도로 제한하고 일부만 잘라 붙이지 않습니다.
   한도 초과 시 관련 data record에 `context_incomplete=True`를 표시하며 `material_html_records`는 그 record를 최종 후보에서 제외합니다. 이미 붙인 앞 각주나 수치만 정상 근거로 전달하지 않습니다. 해당 각주 table에도 layout_role을 유지해 독립 후보가 되지 않게 합니다. 이후 target을 끊어 다음 표에 영향을 이월하지 않습니다.
4. spacer: nb/non-data border, 정상 1행·1셀·span 없음, visible text가 완전히 빈 경우만 인접성에 투명합니다. 기존에 빈 table을 무시한 경로를 점검해 빈 일반 table과 지원 밖 구조는 연결을 끊도록 합니다. spacer 자체에 가짜 금융 record를 만들지는 않습니다.

독립 코드 리뷰 피드백: origin cell shape뿐 아니라 실제 row_count도 정확히 검사합니다. 빈 추가 tr이 caption/spacer로 승인되면 실패입니다. 각주의 4,096바이트 누적 한도는 같은 target에 붙는 prose 각주에도 적용합니다. 한도 초과로 끊긴 각주 묶음의 후속 `주2)` prose를 독립 금융 사실로 전달하지 않고 context_incomplete로 제외합니다. 이 불완전 묶음 상태는 일반 prose·새 data/caption·heading 같은 명시적 경계에서만 끝내며 strict spacer로 지우지 않습니다. 기존 prose-only 각주도 동일 누적 한도에 도달하면 불완전 표를 제외하고 부분 전달하지 않습니다.

위 조건은 새로운 금융 의미 추정이 아니라 좁은 문법 허용 목록입니다. title에 명시적인 연결/별도/개별 충돌이 있으면 문맥 연결을 거절합니다. 연결인지 별도인지 알 수 없는 scope를 caption에서 추정하지 않습니다.

unknown scope에서도 범위를 새로 부여하지 않은 채 같은 unknown record끼리 구조적 문맥을 연결할 수 있습니다. 이후 C3가 별도 검증합니다. 다만 unknown 문맥의 caption에 연결/별도/개별 명시가 있으면 이번 단계에서는 연결하지 않습니다. 상충/미검증 caption 때문에 바로 다음 data를 문맥 없이 정상화하지 않도록 그 data에 context_incomplete를 표시하고 후보에서 제외합니다.

## 연결과 상태

- caption/period_unit은 같은 section_path와 scope의 바로 다음 실제 data table에만 적용합니다. strict spacer만 사이에 허용합니다. 새 caption은 교체하며 일반 prose, heading, unsupported table, 다른 data table에서 대기 문맥을 끊습니다.
- data table의 기존 prose/caption 문맥과 새 layout 문맥을 원문 순서대로 붙입니다. 새 `context_paths`에 caption/title/period/unit의 원래 td XPath를 보존합니다. 빈 기간 셀은 원문 locator로 남겨도 값은 만들지 않습니다.
- 각주는 바로 전 data table에만 연결합니다. strict spacer 외 일반 prose·새 caption·heading·다른 표·scope 경계에서 target을 끊습니다. 각주 table record에 `layout_role=footnote`를 붙이되 그 record가 target을 덮지 않습니다. `footnotes`와 `footnote_paths`에 전체 text/원래 td XPath를 추가합니다.
- caption/period_unit record에는 해당 `layout_role`만 추가합니다. 기존 kind/text/table/source_path 등의 원문 필드는 그대로입니다. 별도 layout record는 `material_html_records`에서 독립 금융 사실 후보로 선택하지 않습니다. metadata가 없는 기존 HTML과 Markdown에는 영향이 없어야 합니다.
- 최종 공통 `_record_blocks`에도 `layout_role` 및 `context_incomplete` 제외를 적용합니다. `filing_blocks(material_notes=False)`는 material_html_records를 거치지 않으므로 material_notes False/True 양쪽에서 불완전 조건·layout-only가 우회 전달되지 않는 회귀가 필요합니다. metadata가 없는 기존 경로는 그대로입니다.
- `context_paths`는 report provenance allowlist, compact/expand roundtrip, 원문 위치 검증에 전달합니다. 기존 compact v1 계약을 깨지 않도록 추가 필드로 보존할 수 있습니다. 새 provenance가 최종 JSON 직렬화/확장 뒤에도 정확해야 합니다.
- parser revision을 갱신합니다. source byte/table/document budget과 최종 6,000바이트는 변경하지 않습니다. 새 문자열 복사도 위 한도로 제한합니다.

## 파일과 순서

1. `tests/test_filing_html_layout_context.py` 신설: 아래 회귀를 RED로 고정합니다.
2. `prism_core/filing_html.py`: 좁은 layout 분류와 reducer 상태 연결. 기존 `filing_html_tables.py` 기하 동작은 바꾸지 않습니다.
3. `prism_core/material_filing_selection.py`, `prism_core/filing_report_evidence.py`: layout-only 제외, context_paths 보존. `prism_core/report_insight_prefetch.py` revision 갱신.
4. `.github/workflows/ci.yml`에 새 테스트 포함. root가 실제 private DOM audit·결과 문서를 별도로 수행합니다.
5. `tools/evaluate_large_filing_html.py` 및 `tests/test_large_filing_evaluation.py`: root가 context_paths를 원문에서 resolve하고 연결 text가 context_before에 원문 순서대로 존재하는지 검증합니다. 기존 prose 문맥에는 path가 없을 수 있으므로 전체 context_before와 강제 동등 비교하지 않습니다. 잘못된 locator·순서·값을 변조한 회귀로 검사합니다. 최종 packet provenance 대응 키에도 context_paths를 포함합니다.

## 실패 회귀와 합격 기준

- caption→data, period_unit 교체, data→빈 spacer→각주 연결. 이전 각주가 새 caption을 넘지 않고 다음 표로 이월되지 않아야 합니다.
- class nb인 실제 수치 표, 숫자-only 제목, unit 뒤 숫자 추가, extra row/cell, rowspan, nested/hidden/script content, 지원 밖 단위는 재분류하지 않습니다.
- 연결/별도 충돌, unknown scope, 일반 prose/heading/unsupported/빈 일반 표 경계. scope 값 자체는 전후 동일해야 합니다.
- unknown fragment→반대 범위 caption→C3 통합 반례에서 상충 data가 후보로 승격되지 않아야 합니다.
- byte 초과 각주는 부분 성공 금지. 복수 각주 누적으로 초과해도 명시 gap이 남아야 합니다.
- native caption이나 셀 밖 조건문을 붙인 위장 layout을 재분류하지 않습니다. 한도 초과로 context_incomplete가 된 수치 표와 layout-only 각주는 둘 다 후보 0개여야 합니다.
- 원래 record의 text/table/좌표가 동일하고 layout-only 독립 후보 0개. context_paths/footnote_paths는 원문 DOM 실제 text와 일치하며 JSON compact/expand까지 유지됩니다.
- 저장된 삼성생명 P의 780→781, 1017→1018 및 A의 3→4, 10→11; POSCO 446→447, 448 spacer→449 footnote를 오프라인 검증합니다. 445나 451을 잘못 연결하면 실패입니다. 원문은 첨부/커밋하지 않습니다.
- LG/카카오/HMM/POSCO의 반복 문법을 확인하되 후보 개수를 범용성 통과로 부르지 않습니다. 연구 입력 회귀, Ruff, 구문, diff, 독립 리뷰를 수행합니다. S3 gold 최종 전달은 별도 검증이며 본 단계 성공으로 대체하지 않습니다.
