# S2c-3 설계: 원문을 바꾸지 않는 공식 주석 부모 문맥

## 실제 문제와 범위

S2c-2의 두 실제 자식 문서는 742,428/9,130바이트로 2 MiB 안에 들어옵니다. 부모 원문의 해당 구간과 전체 canonical 텍스트·표 구조가 같지만, 부모 제목이 없어서 기존 파서의 75개/6개 record 모두 scope가 unknown입니다. 내용이나 위치를 바꾸는 대신 검증된 공식 목차의 부모 관계를 별도 provenance로 보존해야 합니다.

이 단계는 **공통 보고서 어댑터의 명시적인 주석 fragment 진입점**과 같은 원문 검증입니다. 실제 collector의 fallback/자식 선택·예산 배분은 다음 설계입니다. 일반 `section_blocks`/`parse_filing_html` 기본 동작, 운영 2 MiB·최종 6,000바이트, 매매 정책은 바꾸지 않습니다.

## API와 검증 경계

`prism_core/dart_report_evidence.py`에 `note_fragment_blocks(row, section, *, main_html, corp_code, parent_key)`를 추가합니다. 함수를 호출한 것 자체를 신뢰 근거로 취급하지 않고 매번 다음을 검증합니다.

1. S2c-2 `parse_viewer_tree(main_html, row.receipt_id, corp_code)`를 호출합니다. 반환 main_sha256은 row의 기존 main_sha256과 같아야 합니다. 원문이 없거나 hash·발행사·문법이 확인되지 않으면 scope를 부여하지 않습니다.
2. section의 기존 URL/tuple/원문 hash/UTF-8바이트/2 MiB/receipt/row의 검증된 연결·별도 범위 admission을 그대로 적용합니다. 구현 시 이 admission을 private 공통 함수로 추출해 두 public 진입점이 재사용하도록 합니다. 먼저 기존 회귀를 잠그고 정상/실패 반환값이 달라지지 않게 합니다.
3. 자식 key는 section tuple의 dcmNo:eleId입니다. graph에서 정확히 한 부모 parent_key의 직접 자식이어야 하며 section tuple 전 필드와 graph child tuple이 일치해야 합니다. parent와 child의 dcmNo가 같아야 합니다. 다른 parent·문서·offset/length를 허용하지 않습니다.
   추가로 row.sections의 cover와 financial_statements provenance/tuple이 모두 있어야 합니다. 두 tuple은 graph의 해당 key와 전 필드가 같고, receipt 및 dcmNo가 parent/child와 같아야 합니다. cover의 graph 제목은 row.kind와 맞는 기존 정기보고서 종류, financial_statements 제목은 row.scope와 맞는 기존 `숫자. 연결재무제표` 또는 `숫자. 재무제표`여야 합니다. 이미 검증한 회계기간/범위를 다른 dcmNo에 옮기지 않습니다.
4. 부모 제목은 row.scope와 일치하는 좁은 기존 DART 주석 제목입니다: 연결은 `숫자. 연결재무제표 주석`, 별도는 `숫자. 재무제표 주석`. 공백 차이만 정규화합니다. 자식 제목은 기존 번호/하위번호 형식이며 끝의 `(연결)` 또는 `(별도)`가 부모와 명시적으로 일치해야 합니다. suffix가 없으면 추정하지 않고 거절합니다.
5. 자식 HTML은 기존 bounded parser로 그대로 읽습니다. records가 비어 있거나, 어떤 record라도 scope가 unknown이 아니거나 section_path의 첫 항목이 graph의 자식 제목과 다르면 전체 fragment를 거절합니다. 선행 무소속 내용·다른 주석/범위 전환·제목 누락을 조용히 버리고 나머지만 성공시키지 않습니다. 내부 제목 때문에 이 조건을 만족하지 못하는 문서는 이번 supported profile 밖으로 명시합니다.
6. 첫 설계 리뷰에서 내부 `(1) 위험관리 (별도)`를 놓치는 반례가 확인됐습니다. 모든 record의 전체 heading path와 **모든 제목 이벤트**를 함께 검사합니다. 마지막 제목 뒤에 record가 없어도 범위 전환을 거절합니다. 각 이벤트의 section_path도 첫 항목이 동일한 child 제목이어야 하며, 어떤 경로/제목의 `(연결)`·`(별도)`라도 반대 범위이면 실패합니다. 내부 금융 범위 제목에 `연결/별도/개별/재무제표/재무상태표/손익계산서/현금흐름표`가 있고 끝에 부모와 일치하는 명시적 qualifier가 없으면 확인 불가로 거절합니다. 이는 supported profile의 보수적 한계이며 단어만으로 새로운 scope를 추정하지 않습니다.
   접두어와 접미어의 충돌도 무조건 거절합니다. 이번 좁은 profile은 경로/제목에 부모의 반대 범위 단어가 있거나 `개별`이 있으면 suffix가 일치하더라도 거절합니다. 예를 들어 연결 부모 아래 `별도 재무제표 (연결)`과 별도 부모 아래 `연결 재무제표 (별도)`는 모두 실패해야 합니다. 개별을 별도로 자동 매핑하지 않습니다. 반대 범위를 참조한 합법적인 설명 제목도 미지원일 수 있으며 이를 범위 확인 성공으로 바꾸지 않습니다.

이를 위해 `parse_filing_html`에 기본 False인 private keyword `_capture_headings`만 추가합니다. True일 때만 `heading_events`를 반환하며 각 항목은 원래 text, source_path/source_paths, level, 갱신 후 section_path/scope입니다. 이벤트는 기존 streaming reducer가 제목을 인식한 시점에 저장하고 최대2,000개를 append 전에 제한합니다. 한도 초과는 HTML_HEADING_LIMIT로 문서 전체를 거절합니다. 별도 full DOM, HTML 재작성, 제목 삽입은 사용하지 않습니다. 기본 False의 반환 구조·records·resource 한도는 그대로입니다.

검증한 records를 복사하여 scope만 row.scope로 설정합니다. 원문의 text/kind/source_path/source_paths/section_path/표 값·grid·단위·각주는 변경하지 않습니다. 부모 제목을 HTML이나 section_path 앞에 합성해서 넣지 않습니다.

## provenance와 결과

새 record에 `scope_context`를 추가합니다: basis=`DART_EXPLICIT_VIEWER_TREE`, main_sha256, main_url, parent_key, child_key, parent_title, child_title. 이 필드는 외부 부모 문맥이지 해당 child HTML 안에 부모 제목이 있었다는 주장이 아닙니다. scope 자체는 명시적 메타데이터 관계이며 금융 사실 검증은 아닙니다.
main_url은 검증한 receipt에서 공식 main URL로 정규화해 만들며 임의 row 문자열을 그대로 사용하지 않습니다. 첫 제목 이벤트의 원래 source_paths도 child_heading_paths로 보존하여 자식의 명시적 제목 위치를 복원할 수 있게 합니다.

`filing_report_evidence._record_blocks`의 provenance allowlist에 이 필드를 추가합니다. 기본 record에는 새 필드를 넣지 않아 기존 경로가 같아야 합니다. 기존 model envelope 압축/복원은 필드를 손실 없이 유지해야 합니다. 6,000바이트에 들어가지 않는 근거는 기존대로 제외하며 cap을 올리지 않습니다.

정상 fragment도 `DART_NOTE_FRAGMENT_PARTIAL_COVERAGE`를 반환합니다. 한 자식을 읽었다는 사실은 parent 주석54개나 문서 전체를 읽었다는 뜻이 아닙니다. 기존 SOURCE_TEXT_NOT_FACT_VALIDATED 상태를 유지합니다. 승인되지 않은 context는 정적 DART_NOTE_CONTEXT_UNVERIFIED, 본문 불일치는 DART_NOTE_FRAGMENT_SCOPE_UNRESOLVED 등으로 반환하며 raw 예외나 본문을 로그에 넣지 않습니다.

## 변경 파일과 회귀

- `prism_core/dart_report_evidence.py`: 공통 admission/record 변환 재사용과 새 명시적 fragment API.
- `prism_core/filing_report_evidence.py`: provenance에 scope_context 보존.
- `prism_core/filing_html.py`: 명시적으로 요청한 경우에만 bounded heading event 수집. 기본 경로는 변경하지 않습니다.
- `tests/test_dart_note_fragment_context.py`: 신규 synthetic graph+child 회귀. CI 등록.
- 기존 collector와 section_blocks signature는 유지합니다. HTML parser는 위의 private opt-in keyword 외 기본 동작/반환을 유지합니다. collector가 아직 새 API를 호출하지 않으므로 운영 cache revision은 이번 단계에서 바꾸지 않습니다. 실제 운영 연결 시 별도 revision/수집 정책 검증이 필요합니다.

RED 대상은 검증 graph의 일치하는 연결/별도 fragment가 scope를 획득하는 두 정상 사례입니다. 다음 반례도 함께 고정합니다: main hash/issuer/receipt mismatch, parent/child/문서/offset/length 변조, cover/financial document 불일치·누락·잘못된 제목, suffix 부재/반대 범위, parent가 일반 재무제표인 경우, graph 미지원/누락, 제목 없는 본문·다른 제목·선행 무소속 내용·후속 별도 전환(마지막 record 없는 전환 포함), 내부 subheading의 양방향 반대 범위/미확인 금융 제목, heading 수 제한과 기본 수집 OFF, 원문 hash/URL/크기 변조, 원본 text/path/grid 불변, 기존 일반 section_blocks의 unknown 유지.

단위·조건을 포함한 합성 표/각주가 그대로 남는지 검증하고, 새 helper→기존 packet의 6,000바이트 계약과 scope_context 복원/출처 매핑을 확인합니다. 동일 두 실제 private 원문에서 scope 외 기존 record 불변과 DOM11,201개 위치를 다시 검증합니다. 표 수/후보 증가를 최종 내용 품질 승인으로 부르지 않습니다.

## 검증·피드백·원복

설계 리뷰 → RED → 최소 구현 → 기존/new DART·provenance·packet 회귀 → 독립 리뷰 → same-source 검증 순서입니다. 이 단계에서는 HTTP·모델·주문 호출을 하지 않습니다. 문제가 있으면 새 API와 provenance 필드 추가만 원복하고 기존 부모 단위 수집은 유지합니다. 지원 profile을 넓히거나 사용자 예산을 바꾸는 결정은 별도 단계로 남깁니다.

## 첫 코드 리뷰 피드백

1. 새 graph section URL 검증에서 parse_qsl이 빈 값을 버려 빈 중복/미허용 query를 승인하는 반례가 나왔습니다. 새 API의 검증에서 빈 값을 보존해 한 번 파싱한 뒤 정확한 개수·키·값을 대조합니다. child/cover/financial 각각의 빈 중복 키·빈 미허용 키를 RED 회귀로 고정합니다. 기존 일반 section_blocks admission은 변경하지 않습니다.
2. 기존 파서는 숫자나 h 태그가 없는 `<p>별도 재무제표</p>`를 prose로 봅니다. fragment API는 추가로 **정확한 금융 범위 제목처럼 생긴 prose**도 거절합니다. 공백을 제거한 text가 `(연결|별도|개별)?(재무제표(주석)?|재무상태표|(포괄)?손익계산서|현금흐름표)(동일 세 범위의 괄호 qualifier)?` 전체와 일치하면, 맞는 부모 범위여도 이번 profile에서는 확인되지 않은 제목으로 거절합니다. 문장 중 단어를 발견했다는 이유로 scope를 추정하거나 일반 HTML parser를 바꾸지 않습니다. 이 guard는 알려진 plain-title 우회만 보수적으로 막으며 모든 언어·표 안·비표준 제목을 알아낸다고 주장하지 않습니다. 양방향·matching suffix/반대prefix·무접두어·후행 제목, 일반 설명 문장이 부당하게 새 scope가 되지 않는 회귀를 포함합니다.
