# S2b 설계: 방향 중립적인 bounded 표 기하 구조

## 목표와 실제 근거

S2a 다음의 단일 수정입니다. POSCO private fixture의 PF 보증표는 기본 15×99(원본 셀 1,124개), 연간 15×85(966개)입니다. 가장 큰 colspan은 98/84이고 기존 80열 제한 때문에 전체 표가 탈락합니다. 행 제한 문제가 아닙니다. SHA와 위치는 실행 기록의 실제 원문 기준이며, 원문을 재조회하거나 종목별 분기를 넣지 않습니다.

목표는 **wide table geometry 보존**입니다. PF 근거의 최종 전달·금융 사실·단위/각주 연결 품질까지 합격으로 간주하지 않습니다. 특히 표 앞 단위가 별도 layout table에 있고 조건 각주도 다른 table에 있는 경우 현재 문맥 결합은 별도 실패로 유지합니다.

- 기본 원문: `response-0022.bin`, `/html/body/table[447]`, SHA-256 `51deac90c0c3fc9eb7679588d6c2a04febc5463b8d12499b51d581504a241ca3`.
- 연간 원문: `response-0023.bin`, `/html/body/table[782]`, SHA-256 `7cdc55381b3ad5c883c79c076f3f3e3dfc97b2a3c6a107ae8435ab202ef159fe`.
- 독립 critic 검토: OKAY. 위 SHA/위치는 검토의 기록 보완 권고를 반영했습니다.

## 대안과 선택

- A 선택: 기존 허용 형태와 전치 형태의 합집합을 허용하되 실제 행·열을 전치하지 않습니다. 방향에 따른 임의 거절을 없애고 원본 좌표와 원문 순서를 유지합니다.
- B 보류: sparse 저장/열 projection은 별개 문제입니다. 열 선택은 계층형 표제·조건을 잃을 위험이 있고 S3의 내용 검증이 필요합니다.
- C 원복: 자원·범위·구조 회귀가 실패하면 기존 80열 정책으로 돌아갑니다.

열·colspan 허용 범위가 80→300으로 확장된다는 점을 명시합니다. 총 자원 한도가 전혀 바뀌지 않는다는 주장은 하지 않습니다. 원문 2 MiB, 표 12,000칸, 문서 전체 120,000칸/60,000원본 셀, 최종 담당자별 6,000바이트 제한은 유지합니다.

## API와 입출력 계약

`parse_html_table(..., max_rows=300, max_columns=300, max_cells=12000)`로 기본 열 상한을 변경합니다. 세 인자 모두 여전히 개별 축/면적 상한이며 낮춰 지정한 값을 우회하지 않습니다. 양 축의 개별 hard ceiling은 300입니다. 추가 형태 조건은 `min(행수, 현재까지 누적 너비) <= 80`이며, 두 축 모두 81 이상이면 GRID_LIMIT/LIMIT_EXCEEDED입니다. 면적도 grid 확장 전에 검사합니다.

- colspan/rowspan은 각 축의 지정 상한을 먼저 검사합니다.
- 끝 열은 병합 점유 칸을 건너뛴 실제 위치와 colspan의 합으로 계산합니다. end-column 및 면적·형태 조건을 grid extend 전에 검사합니다.
- 입력의 행·열·cell 순서·rowspan/colspan·원문 text·XPath는 그대로 반환합니다. 자동 전치, 합산, 빈칸/0 추정, 잘라낸 성공 반환을 하지 않습니다.
- span 문법, 겹침, row overrun, hidden/active/nested table 검사는 유지합니다.
- `filing_html` 문서 전체 한도는 유지하며 초과하면 기존처럼 문서 실패 처리합니다. 새 표를 읽었으므로 이전보다 aggregate 한도에 일찍 닿을 수 있음을 실제 fixture에서 점검합니다.
- parse result schema와 parser_version은 바꾸지 않고 cache revision만 `bounded-html-v5-wide-geometry`로 올립니다. 실제 출력 좌표 모델은 동일합니다.

## 변경 범위

1. `prism_core/filing_html_tables.py`: API 기본값/상한 및 shape 조건만 변경.
2. `prism_core/report_insight_prefetch.py`: cache revision.
3. `tests/test_filing_html_tables.py`: 먼저 실패하는 wide 사례와 경계/원문 좌표 테스트 추가.
4. `tests/test_filing_html_streaming_adversarial.py`: 기존 aggregate 제한 회귀를 실행하며 wide 추가 시 전체 제한 동작을 명시적으로 검증.
5. `tools/capture_dart_fixture.py`: code hash 목록이 table parser와 material selection 등 실제 후보→packet 의존 모듈도 포함하도록 보완. 기존 manifest의 hash 목록은 수정하지 않고 새 replay 해시가 더 넓어졌음을 표시합니다.

## 구현 전 실패 테스트와 검증

- 합성 15×99/15×85와 99×15/85×15 쌍. wide 두 사례는 기존 코드에서 실패해야 합니다. expected grid/cell 좌표·병합은 직접 구성하고 production parser를 정답 생성기로 쓰지 않습니다.
- 80×81/81×80 허용, 81×81 거절. 1×300/300×1 허용, 301축 거절. 40×300 면적 12,000 허용, 41×300 거절. 누적 너비 301은 cell별 colspan이 작아도 거절.
- colspan98/84와 rowspan을 섞은 고정 grid, overlapping span, row beyond end, lowered max_columns/max_rows/max_cells 유지.
- wide 표 누적으로 120,000 grid slots 초과와 60,000 원본 셀 초과가 기존 문서 fail-closed를 유지하는지 확인.
- 기존 표/streaming/원문 위치/보고서 wiring 회귀와 CI 연구 입력 전체 묶음, Ruff/compile/diff 검사.
- POSCO 두 원문을 같은 해시에서 전체 parse→adapter→packet replay. 새 표의 모든 cell XPath를 원문 DOM에서 직접 조회하고 원문 tag/text/span과 비교. 전체 grid의 분할 점유가 원본 cell 좌표와 일치함을 확인하되 기존 oracle을 독립 geometry 증명이라고 부르지 않습니다.
- 기존 3개 대형 HTML과 LG/카카오/HMM fixture 전체 회귀. 기하 구조 추가 외 기존 records의 text/path/cell정보가 유지되는지 확인하며 새 문맥 결합 변화가 있으면 별도 검토합니다.
- 최종 6,000바이트 초과 0, 새 wide 표가 최종에서 제외되면 미전달로 명시합니다. 단위/조건 연결·중요 근거 회수·실제 모델은 다음 게이트입니다.

## 검토와 원복

설계 critic OKAY → RED 고정 → 구현 → 회귀/동일원문 비교 → 독립 코드 리뷰 → 피드백 수정 순서입니다. 자원 또는 내용 변화가 설명되지 않으면 다음 품질 승인으로 넘어가지 않습니다. 매매 규칙·네트워크 요청량·원문/모델 예산·기본 OFF 설정은 변경하지 않습니다.
