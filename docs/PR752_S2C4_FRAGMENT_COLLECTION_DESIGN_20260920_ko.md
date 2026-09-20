# S2c-4 설계: 기존 예산 안의 부분 주석 수집 연결

## 목표와 불변 조건

검증된 C2 graph와 C3 fragment API를 실제 `collect_dart_periodic_filings → collect_latest → packet` 경로에 연결합니다. 특정 회사·receipt·eleId 분기는 두지 않습니다. 개별2 MiB, collector 합계8 MiB·28회·55초, identity 별도 기존 한도, 담당자별6,000바이트, 기존 기본 OFF 및 매매 규칙을 유지합니다.

목표는 큰 parent를 읽지 못했을 때 **그 주석의 일부를 안전하게 보완**하는 것입니다. parent 전체·54개 자식 전체·최종 내용 품질을 완료했다고 표시하지 않습니다. 제목 label의 존재를 중요도·악재·투자 신호로 해석하지 않습니다.

## 발동 조건

`dart_public_filings`의 내부 `_SectionBodyLimitExceeded(_SourceError)`를 추가하되 외부 오류 문자열은 기존 RESPONSE_BYTES_EXCEEDED를 유지합니다. request_once에서 aggregate 초과를 먼저 검사하고, 그 후 개별 response body만 초과했을 때 이 내부 타입을 사용합니다. 입력검증·HTTP·압축·UTF-8·전송·timeout·aggregate 초과에는 child fallback이 없습니다. child가 다시 커도 grandchildren로 재귀하지 않습니다.

`include_section_bodies=True`이며 기존 선정기가 tentative selected primary 또는 annual로 고른 row의 notes parent에서 위 **개별 초과**가 발생해야 합니다. core의 cover/financial 범위·기간·문서 검증은 기존 그대로 선행하며 core 실패를 fragment로 구제하지 않습니다.

## 요청 순서와 예산

기존 catalog/core 선정 단계 이후:

1. selected primary와 annual의 whole parent notes를 기존 우선순위대로 먼저 시도합니다.
2. 개별 한도에 걸린 selected row에만 strict graph를 적용해 직접 child 후보를 만듭니다. main 재조회는 없습니다.
3. 실패한 selected row들을 primary→annual 순서로 round-robin하여 **합쳐 최대2개의 고유 child**를 계획합니다. 기존 request()의 허용된 GET 재시도가 있으면 같은 max_calls counter를 소비합니다. 2개는 고유 문서 수이며 추가 HTTP 재시도를 예산 밖으로 세지 않습니다.
4. 남은 기존 call/byte/deadline 안에서 계획 child를 순차 취득합니다. 부재한 예산을 위해 상한을 올리지 않습니다. 이후 기존 비선택 notes를 잔여 예산에서 처리합니다.

whole parent가 정상인 경우 호출 순서는 현재와 같아야 합니다. parent 실패가 HTTP/전송 계열이면 이 새 경로로 우회하지 않습니다. child의 HTTP/encoding/transport/timeout/aggregate 실패가 발생하면 새로운 supplementary 요청을 중단하고 기존 core·이미 확보한 child를 보존합니다. 개별 child 초과나 body 의미/범위 거절은 다른 문서를 손상시키지 않으며, 실제 요청은 모두 기존 예산 안에 남습니다.

첫 설계 리뷰의 중단 범위 지적을 반영합니다. **선택된 whole parent 단계**에서 HTTP/encoding/transport/UTF-8/timeout/aggregate 오류가 하나라도 발생하면 그 row만의 실패가 아니라 수집 전체의 supplementary-stop을 설정합니다. 그 뒤 어떤 selected row의 child도, 비선택 notes도 요청하지 않습니다. primary parent가 개별 초과한 뒤 annual parent가403이면 child0회가 정답입니다. 이미 취득한 core/whole body는 보존하고, 계획 또는 미실행 fragment 상태에 정적 중단 사유를 남깁니다. 개별 크기 초과만 이 전역 중단 조건에서 제외합니다. 기존 core 취득 단계까지 새로 바꾸는 설계는 아닙니다.

## 후보와 선택의 좁은 계약

공식 parent의 직접 child만 사용합니다. parent/child의 receipt/dcm·주석 parent 제목·child의 일치하는 명시적 `(연결)`/`(별도)` 표기를 먼저 확인합니다. 일반 재무제표나 다른 scope의 노드는 후보가 아닙니다.

각 parent 안에서는 public `material_topics('', (child_title,))` 결과가 있는 항목을 먼저, 그 안에서는 원래 child 순서로 둡니다. label이 없는 child는 원래 순서의 후순위입니다. 새로운 키워드·sector/ticker 조건·임의 중요도 점수는 추가하지 않습니다. 정책명은 `title-label-presence-v1`이며 제목 기반 retrieval 순서일 뿐 S3 내용 품질을 인증하지 않습니다. 이번 probe의47/78을 선택 규칙에 넣지 않습니다.

## 원문과 상태 전달

`include_section_bodies=True`일 때만 기존 응답 main을 내부 `main_by_receipt`에 보관합니다. 이미8 MiB 예산으로 취득한 bytes이며 새로운 네트워크나 원문 한도 확대가 없습니다.

- `row.note_fragments`: EOF까지 받은 child 원문/기존 provenance/parent_key/child_key 목록.
- `row.note_main_html`: fragment가 실제로 있는 selected row에만 붙이는 명시적 raw context. adapter의 C3 교차 검증용입니다.
- `row.note_fragment_selection`: 원문 없는 상태. version, basis, parent_key, main_sha256, child_total, eligible_total, planned_keys, requested_keys, acquired_keys, budget_omitted_keys, unselected_count, failures, full_notes_acquired=False.

계획과 요청, 완성 body, C3 승인, 후보와 최종 전달을 구분합니다. 한 child도 못 받았으면 raw main을 반환할 필요가 없습니다. 전체 parent `financial_notes.html`은 만들거나 합성하지 않으며, missing_sections의 financial_notes와 PARTIAL을 그대로 유지합니다. 이전 parent 실패 코드를 삭제하지 않습니다.

`collect_latest`는 기존 정상 섹션을 처리한 뒤 각 fragment에 C3 `note_fragment_blocks`를 적용합니다. child마다 `D-{receipt}-financial_notes_fragment-{dcm}-{ele}`의 고유 source ID와 section=`financial_notes_fragment`를 사용합니다. source에는 기존 row의 role/kind/period/scope/entity를 보존하며 child 원본 URL/hash와 scope_context를 유지합니다.

progress/receipt에는 row의 raw context를 복사하지 않습니다. `selected_note_fragments`에 위 안전한 선택 상태와 C3 context_verified/candidate_count/정적 gaps만 명시적으로 옮깁니다. scope 확인 성공인데 후보0개인 경우와 scope 검증 실패를 구분합니다. 어떤 경우에도 parent 전체 AVAILABLE이나 full_notes_acquired=True로 승격하지 않습니다.

context_verified는 blocks 개수가 아니라 C3 성공 표시 `DART_NOTE_FRAGMENT_PARTIAL_COVERAGE`의 존재로 판단합니다. 이 표시는 scope/provenance admission 성공이지 후보 존재·최종 전달·금융 사실 승인이 아닙니다. graph 검증 실패처럼 child 총수를 모르는 경우 child_total/eligible_total을0으로 꾸미지 않고 None으로 표시합니다.

tentative 선택과 최종 선정 결과가 다를 수 있습니다. 최종 primary/annual에 포함되지 않은 row의 raw fragment/main context는 반환에서 제거하고, 실제 요청·취득 이력만 sanitized selection 상태에 `discarded_due_to_final_selection=True`로 남깁니다. adapter도 최종 selected row만 처리합니다. 선정 취소 또는 다른 annual로 바뀐 경우의 fragment가 sources/packet에 들어가지 않는 회귀를 추가합니다.

## 변경 범위

- `prism_core/dart_public_filings.py`: 내부 개별 limit 타입, opt-in main 재사용, selected-parent 우선 단계와 최대2 child 계획/수집/상태. graph/material_topics는 필요한 경로에서 import하여 report adapter와 순환 의존하지 않습니다.
- `prism_core/dart_report_evidence.py`: C3 호출과 독립 source 구성, 상태 allowlist. 기존 source 생성과 filing metadata 공통부를 재사용하며 raw main/body의 progress 유출을 회귀로 막습니다.
- `prism_core/report_insight_prefetch.py`: 운영 연결에 맞춰 parser revision 변경.
- `tools/evaluate_general_filing_reports.py`: fragment 상태가 있을 때만 원문 없는 결과를 조건부로 포함합니다. 정상 기존 결과의 shape는 유지합니다.
- `tools/capture_dart_fixture.py`: 운영 경로의 새 graph 의존 파일도 fingerprint에 포함합니다. recorder 자체는 바꾸지 않습니다.
- 관련 단위·실제 adapter→packet 통합 회귀와 CI 등록, 결과/인수인계.

## recorder 제약과 검증 분리

기존 recorder는2 MiB를 넘기기 전에 FixtureError를 던지므로, producer의 개별 limit 타입을 똑같이 재현하지 못합니다. 이를 production에서 잡거나 tools를 import하지 않습니다. incomplete를 complete로 바꾸거나 가짜 oversized bytes를 실제 원문 replay라고 부르지 않습니다.

1. fallback·request order·공유 예산은 명시적으로 합성인 streamed MockTransport로 검증합니다.
2. 기존 정상 parent의 실제 저장 fixture replay는 그대로 확인합니다.
3. 새 실제 통합은 recorder가 개입하지 않는 기존 HTTPX 경로에서 selected1회사만 read-only로 실행합니다. `collect_latest`까지의 실제 결과를 원문 없는 요약으로 기록합니다. 원문 전체 재생 증거가 아니므로 full replay 성공이라고 표시하지 않습니다.
4. 이미 확보한 두 실제 child의 parser/adapter same-source 증거는 그대로 유지합니다. 새로 선택된 child의 본문까지 보존할 경우 명시적인 opt-in raw 반환값을 Git 밖의 새0700/0600 진단 경로에 저장하고 source hash를 확인합니다. partial parent의 prefix는 저장·성공으로 재사용하지 않습니다.

## RED 및 합격 기준

- 정상 parent의 호출 순서/기존 결과/hash 불변, body opt-in False에서 raw main/fragment/추가 요청 없음.
- 정확히2 MiB 정상, +1바이트 개별 초과만 fallback. aggregate 초과와 동시 초과 시 child0회.
- HTTP/encoding/UTF-8/timeout/transport 실패에는 child0회. 새 child 단계의 접근/전송 실패에서 supplementary 중단.
- primary child가 annual whole parent를 밀어내지 않음. 최대2 unique child/기존 retry 포함 총 call·bytes·walltime 한도 유지.
- primary 개별 초과→annual403 순서에서 child0회/비선택notes0회/중단 사유 유지, 이미 완성된 core 보존.
- graph 실패, 다른 dcm/receipt/parent, scope 충돌, 예산 부재, partially acquired/rejected child 상태.
- parent status PARTIAL/missing 유지, 취득/검증/후보/최종 전달 분리, source ID 충돌0, raw main/body가 progress/receipt/model envelope에 없음.
- C3검증 성공/후보0개 상태와 C3실패를 구분하고, tentative/final 선정 변경·취소 시 최종비선택 fragment 유입0건.
- 실제 collect_latest→packet 통합에서 source hash·기간·범위·외부 문맥·누락·6,000바이트 계약 확인.
- 모든 새 branching을 synth 실패부터 고정한 뒤 기존 DART/HTML/provenance/cohort/consumer 회귀, Ruff·구문·diff·독립 리뷰를 통과합니다. actual1case는 코드 리뷰 후 실행합니다.

이번 단계가 끝나도 S3의 필수 근거·단위·조건·연간 보완 품질, 새 검증군, 미국·실제 모델·BUY/SELL은 남습니다. 머지·배포 승인과 구분합니다.
