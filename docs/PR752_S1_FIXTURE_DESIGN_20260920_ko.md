# S1 설계: 공개 DART 원문의 제한된 보존과 동일 입력 재생

상위 요구사항: `PR752_COMPLETION_PRD_20260920_ko.md` R1/R2. PRD 독립 검토는 OKAY이며, 이 문서는 구현 전 설계 검토 대상입니다. 범위는 재현 기반과 LG 실제 원인 확인입니다. 파서 수정이나 전체 품질 승인은 여기 포함하지 않습니다.

## 선택한 구조

운영 수집기·인증 정보를 고치지 않고 기존 `client_factory` 주입점을 사용합니다. 보고서 어댑터 `collect_latest`에는 기본값 None인 진단용 `client_factory` keyword만 추가해 실제 identity/collector에 전달합니다. 기본 호출에서는 추가 keyword를 넘기지 않아 기존 동작을 유지합니다. HTTPX의 `AsyncBaseTransport`/`AsyncByteStream`을 감싼 진단 전용 transport가 응답 스트림을 그대로 소비자에게 전달하면서 완료된 원문을 보존합니다. 따라서 `_section_scope`, `parse_catalog_page`, `section_blocks` 등에서 거절하기 **전**의 응답을 확보할 수 있습니다.

대안인 ‘보고서 packet에서 원문을 역추출’은 원문이 이미 사라지므로 사용하지 않습니다. 운영 수집기에 무조건 raw logging을 추가하거나 원문 크기 제한을 늘리는 방법도 사용하지 않습니다. 설계 근거는 [HTTPX custom transport 문서](https://www.python-httpx.org/advanced/transports/)입니다.

## 변경 파일과 책임

- 신규 `tools/dart_fixture_transport.py`: private fixture 저장·검증, 기록/재생 transport. 운영 모듈에서 import하지 않습니다.
- 신규 `tools/capture_dart_fixture.py`: 명시적 live capture와 network-free replay 진입점, 안전한 결과 요약.
- 신규 `tests/test_dart_fixture_transport.py`, `tests/test_capture_dart_fixture.py`: 원문·실패·I/O·악성 manifest·같은 입력 재생 회귀.
- 수정 `prism_core/dart_report_evidence.py`: `collect_latest(..., *, client_factory=None)`를 실제 identity/collector 호출에 연결하는 한정된 주입점. 수집·선정 정책이나 기본 캐시 의미를 바꾸지 않습니다.
- 기존 `dart_identity.resolve_dart_identity`와 `dart_public_filings.collect_dart_periodic_filings`의 `client_factory`, 기존 `section_blocks`와 `packet`을 재사용합니다. 운영 함수의 기본 동작·순서·예산은 변경하지 않습니다.

## live 입력·한도

- 명시적 `--live`, ticker/name, aware decision_at, scope와 새 fixture 경로를 요구합니다. 경로는 Git worktree 밖이며 기존 부모 디렉터리 안의 새 디렉터리여야 합니다.
- 기존 identity 최대 3회/15초/1 MiB 응답 한도, collection 최대 28회/55초/2 MiB 응답·8 MiB 합계 한도를 유지합니다. 전체 case 한도 80초입니다.
- 진단 저장소는 최대 32개 응답, 개별 2 MiB, 원문 합계 12 MiB, manifest 1 MiB로 제한합니다. 한도 초과는 별도 capture 실패입니다.
- 원문을 먼저 잘라 파싱 성공으로 취급하지 않습니다. 완전히 수신한 허용 크기의 응답만 raw 파일로 보존합니다. 초과·중단 응답은 상태와 관측 바이트 수만 기록하고 재생 가능한 본문으로 표시하지 않습니다.

## 허용 요청과 저장물

허용 요청은 HTTPS `dart.fss.or.kr`의 기존 회사 검색·프로필 POST, main/viewer GET뿐입니다. 포트·userinfo·fragment·예상 밖 query/form 키를 거절합니다. 요청 form은 공개 회사 식별·조회 기간·페이지 필드만 허용합니다. Authorization·Cookie·API key 헤더를 전달받으면 진단 경로를 거절합니다. 응답 헤더 전체나 자격증명·환경변수는 저장하지 않습니다.

각 요청은 method/정규화 URL/공개 form 필드/순번, HTTP 상태 또는 정적 전송 오류 유형, 수신 완료 여부, 관측 바이트 수를 기록합니다. 완료된 본문은 고정된 순번 파일명으로 저장하고 SHA-256과 크기를 기록합니다. non-200 응답은 본문을 읽지 않는 기존 수집기 동작을 유지하며 상태만 남깁니다. 본문을 거절한 응답도 `QUARANTINED_CAPTURE`이며 검증된 금융 사실로 승격하지 않습니다.

`content-encoding`은 거절 결과를 재현하는 데 필요한 예외적인 metadata입니다. 전체 헤더 대신 길이가 제한된 해당 필드 값만 그대로 보존합니다. 대소문자/공백의 의미를 수집기 대신 정규화하지 않습니다. request form은 순서를 가진 key/value 쌍 목록으로 저장하여 반복 `publicType` 값을 잃지 않습니다.

응답 종료 상태는 `EOF_COMPLETE`, `BUFFERED_COMPLETE`, `CONSUMER_CLOSED`, `STREAM_ERROR`, `CANCELLED`, `POLICY_REJECTED`로 구분합니다. `aclose()`만 호출됐다고 완료로 표시하지 않습니다. 이미 버퍼링된 MockTransport 응답은 명시적으로 `response.content`를 관측하고 같은 바이트/상태/encoding을 소비자에게 반환합니다. 비정상 encoding은 소비자가 먼저 거절하므로 정상적인 압축 해제를 한 것처럼 저장하지 않습니다. HTTP 또는 encoding 정책으로 읽기 전에 거절한 응답은 status/encoding만으로 해당 거절을 재생할 수 있지만, 정상 200 응답의 미완료 본문을 빈 성공 본문으로 대신하지 않습니다.

회사 식별 응답부터 같은 recorder를 실제 `collect_latest`에 주입하므로 identity→목록→main→표지→재무/주석→adapter→packet을 같은 운영 함수로 실행합니다. manifest에는 `evaluate_general_filing_reports._summary`의 원문 없는 결과를 재사용하고 실행 입력·정책을 더합니다. raw HTML/preview/excerpt는 summary에 넣지 않습니다. raw 파일이 있다는 이유로 발행사·기간·범위·내용 검증에 합격했다고 표시하지 않습니다.

## 파일 안전성과 실패

- 출력 디렉터리 0700, 파일 0600을 적용합니다. 기존 디렉터리/파일 덮어쓰기, 심볼릭 링크 경로, 경로 이탈을 거절합니다. 임의 이름 대신 `response-0001.bin` 같은 고정 형식을 사용합니다.
- 기록과 검증은 비동기 경로를 막지 않도록 필요한 파일 I/O를 `asyncio.to_thread`로 수행합니다. 저장 실패를 정상 수집 성공으로 보고하지 않습니다.
- 생성한 fixture는 외부 전달용 결과물이나 Git 커밋에 넣지 않습니다. 사용자에게는 원문 없는 요약과 재현 명령·검증 결과를 제공합니다.
- 실패·중단 때 완성된 raw 파일을 몰래 삭제하지 않습니다. incomplete marker나 실패 manifest를 남겨 재생 가능 여부를 구분합니다. 기존 사용자 자료는 어떤 경우에도 정리하지 않습니다.

최초 디렉터리 생성 직후 incomplete marker를 먼저 씁니다. 모든 파일 쓰기 task를 recorder가 추적하고, `to_thread` 작업은 shield하여 호출자 취소와 구분합니다. 최상위 진단 실행은 성공·실패·취소 모두에서 이미 시작한 쓰기의 완료/오류를 회수합니다. collector가 observer 실패를 일반 수집 실패로 흡수해도 recorder의 독립 `failed/incomplete` 상태를 검사합니다. 기록·해시·쓰기 결과가 모두 확인된 뒤에만 새 manifest를 O_EXCL로 쓰고 fsync하며, incomplete marker 제거를 마지막 성공 확정으로 사용합니다. 취소·쓰기 실패·manifest 실패 때 marker는 남고 strict replay는 거절합니다. 프로세스 강제 종료 시에도 marker가 남으므로 부분 파일을 성공 fixture로 재사용하지 않습니다. OS 파일 I/O 자체를 강제 취소할 수 있다는 주장은 하지 않습니다.

## replay 계약

- manifest 크기·스키마·허용 파일명·개별/합계 한도·파일 권한·심볼릭 링크·모든 해시를 먼저 검사합니다. 불일치하면 응답 제공 전에 실패합니다.
- 요청은 저장된 순서의 method/정규화 URL/form과 정확히 일치해야 합니다. 누락·추가·순서 차이는 `REQUEST_DIVERGENCE`입니다. 네트워크 fallback은 없습니다.
- 완성된 응답만 같은 바이트로 제공하고, 기록된 HTTP/전송 실패는 같은 정적 종류로 재현합니다. 중단되어 본문이 없는 200 응답은 정상 응답으로 재생하지 않습니다.
- frozen decision_at과 같은 정책으로 실제 identity와 collector를 다시 실행합니다. parsed record와 `section_blocks`/`packet`까지 재생할 수 있으나, 단순 원문 위치 검사는 금융 사실의 독립 검증이 아닙니다.
- 모든 요청 소비, identity 결과, selection/기간/범위/선택 문서 hash, 후보/최종 전달을 비교합니다. 새로운 observed_at 등 실행 시각은 별도 기록하고 과거 시각으로 위장하지 않습니다.
- 생산 코드가 바뀌면 코드 해시를 구분합니다. collection 순서가 바뀌어 strict replay가 불가능해지면 파서-only replay와 전체 acquisition replay를 분리해서 표시합니다.

## 구현 전 고정할 회귀

1. 정상·범위 불일치·UTF-8 불량 응답의 정확한 raw bytes 보존, body/metadata 해시 및 원문 위치 대조.
2. non-200·전송 단절·부분 수신·크기 초과에서 거짓 완성 본문이 생기지 않음.
3. 기본 수집기의 파싱/선정/네트워크 호출 수가 recorder 유무로 달라지지 않음.
4. max count/bytes/manifest 한도, 저장 실패, cancellation, 기존 경로/심볼릭 링크/경로 이탈 거절.
5. 악성 manifest의 임의 로컬 파일 참조·URL·form 키·변조 hash·미완료 상태를 거절함.
6. 실제 모듈을 통과하는 identity→collector→adapter→packet의 capture/replay가 network-free로 일치함. 추가 요청·미소비 응답·잘못된 요청 순서에 실패함.
7. 200 gzip/비정상 encoding의 사전 거절, 이미 버퍼링된 MockTransport, 반복 form 키 보존, 쓰기 도중 취소와 task 회수, manifest 쓰기 실패 시 marker 보존을 시험합니다.

회귀 명령은 신규 두 테스트와 기존 `test_dart_identity.py`, `test_dart_public_filings.py`, `test_dart_section_collection.py`, `test_dart_report_evidence.py`, `test_latest_filing_report_wiring.py`, `test_general_filing_report_evaluation.py`를 먼저 실행하고, 이후 CI의 연구·보고서 전체 목록을 실행합니다. 새 코드 Ruff·compile·diff 및 독립 리뷰도 필수입니다.

원복 범위는 새 진단 도구와 테스트, `collect_latest`의 선택형 주입점, CI 테스트 등록에 한정합니다. 새 fixture는 기존 사용자 데이터와 분리해 보존하며 원복 때문에 삭제하지 않습니다. 운영 설정·인증 파일·매매 코드의 변경은 없습니다.

## 검증·피드백·다음 단계

우선 합성 실패 테스트를 만든 뒤 구현합니다. 독립 리뷰의 파일/원문/실패 처리 차단 지적을 해결하고, 기존 DART·report 회귀를 실행한 후 LG live capture를 1회 수행합니다. 원문을 확보하면 저장한 동일 바이트에서 baseline/current 파서를 비교하고, 원인을 확인한 뒤에만 별도의 파서 수정 설계를 작성합니다.

live가 다시 전송 실패하면 이미 확보한 원문과 요청 기록부터 확인합니다. 같은 오류를 여러 종목에 반복하지 않고, 공식 사용자 접근이나 공개 원문 제공 등 필요한 최소 지원을 요청합니다. 모델 OAuth 미복구는 S5 진입 시 해결하며 S1의 공개 원문 작업을 차단하지 않습니다.
