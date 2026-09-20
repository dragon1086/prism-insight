# PR752 HTML 8 MiB 확대 결과

## 이번 완료 범위

기준 HEAD `7b4c7171d784619d40a69a60cd15f52418dca809`에서 승인된 HTML 2→8 MiB 확대를 구현하고 오프라인 검증했습니다. 기존 Draft PR #752 범위이며 머지·배포·운영 설정 변경은 하지 않았습니다. 아래 결과는 전체 보고서 품질 합격이나 한국 범용성 완료를 뜻하지 않습니다.

- `prism_core/filing_html_policy.py`: UTF-8 HTML 개별 상한을 한 곳에서 정의합니다.
- `filing_html.py`: 8 MiB admission과 협력적 10초 처리 제한을 적용했습니다. 기존 활성 노드·깊이·record·grid·cell·locator·표 제한은 유지합니다. 문서 자원 제한 실패는 records/headings를 모두 폐기하고, 기존 개별 표 해석 실패는 PARTIAL로 구분합니다.
- `dart_public_filings.py`, `dart_report_evidence.py`: opt-in 금융 본문/주석/fragment의 수집·해시·provenance admission을 연결했습니다. 누적 취득 8 MiB는 개별 상수와 분리했습니다. 목록·main·cover·비옵트인 금융 본문·JS는 2 MiB를 유지합니다.
- `dart_section_html.py`: 큰 본문의 scope·preview용 DOM을 본문당 한 번 만들고 재사용합니다. 전체 노드 100,000개(주석 포함), 깊이 100, 8 KiB feed, 공통 10초 deadline을 적용합니다. preview는 전체 문자열 대신 공백 정규화한 앞 1,200자만 만듭니다.
- `tools/dart_fixture_transport.py`: 기록/재생의 viewer URL envelope는 8 MiB, 제어 응답은 2 MiB, 전체는 12 MiB입니다. URL만으로 cover와 금융 본문을 구분하지 않으므로 collector가 더 좁은 정책을 집행합니다. 하향 전용 recorder cap으로 기존 child 진단의 2 MiB 저장 제한도 유지합니다.
- `report_insight_prefetch.py`, capture/evaluation 도구: revision `bounded-html-v9-8mib`, 새 정책/보조 모듈 fingerprint, 8 MiB 메타데이터를 반영했습니다. 모델 담당자별 6,000바이트·후보 24개·Markdown 2 MiB는 바꾸지 않았습니다.
- CI에 신규 parser/collector/fixture 경계 테스트를 연결했습니다. 새 의존성이나 종목별 예외는 없습니다. 기존 매매 규칙은 변경하지 않았습니다.

## 검증 근거

- 변경 전 관련 80개 통과. 신규 parser RED 6개, collector RED 3개, fixture 경계 RED 6개 및 fingerprint/메타데이터 RED 3개를 확인한 뒤 구현했습니다.
- 최종 연구 입력 CI와 같은 78개 파일 회귀: **2,099개 통과**, 기존 경고 4개. 별도 KR/US provider 실모듈 통합 **46개 통과**.
- 변경 Python 22개 전체 Ruff(입력 정렬 포함), AST/구문 검사, `git diff --check` 통과. Python 전용 타입 검사기는 미구성이며 TypeScript backend의 LSP 결과를 Python 타입 검증으로 세지 않았습니다.
- 독립 설계 검토 후 구현했고 최종 독립 코드 리뷰는 **APPROVE**입니다. 리뷰의 관련 365개 및 후속 152개 검사는 전체 회귀와 중복되므로 더해서 보고하지 않습니다.
- ASCII/다중바이트의 정확히 8 MiB와 +1, 후반 조건 및 원문 XPath, 해시/URL/scope, 합계 예산, record/grid/cell/locator/node/depth, 파서·DOM deadline, stream EOF 전 중단/취소/응답 close, fixture 불완전 격리와 제어 응답 제한을 검증했습니다.
- 정확히 8 MiB인 본문은 앞선 목록/표지 비용 때문에 합계 8 MiB를 초과하여 취득에 실패합니다. 이 경우 새 요청이나 fragment fallback으로 예산을 우회하지 않습니다. 단일 본문 허용과 전체 취득 성공은 다릅니다.

중간 실패도 보존했습니다. 첫 전체 회귀는 diagnostic child recorder가 원래의 2 MiB를 잃은 문제로 1개 실패/2,074개 통과였습니다. 기존 실패 테스트를 완화하지 않고 하향 전용 recorder cap을 추가해 해결했습니다. 독립 리뷰 중 capture fingerprint 1개 실패는 동시 소스 수정으로 `code_unchanged=False`가 된 정상 방어였으며 코드 고정 후 재실행 통과했습니다.

## 실제 저장 원문과 자원 측정

새 HTTP·모델 호출 없이 비공개 원문 7개를 SHA 대조 후 처리했습니다. 기존 6개는 이전 HEAD 파서의 전체 반환값과 정확히 같았습니다. 이미 사용한 금융·지주·인터넷·해운·철강 원문이며 새 독립 홀드아웃이 아닙니다.

기존에 HTML_BYTE_LIMIT로 거절됐던 연간 주석:

- 원문 **2,833,488바이트**, SHA-256 `556ee3df6e4131bf77c72d8ca8b4a27c02c9e6cf2abf62b01ea846dbb8f724df`.
- **965 record**, 전체 58,596개 노드 중 peak active 1,016개, grid 77,055개, origin cell 35,144개.
- 파서 약 **0.525초**, 해당 프로세스 peak RSS **79,413,248바이트(약 75.7 MiB)**. 별도 collector helper 약 0.166초, peak RSS 약 105.5 MiB.
- 원문 위치 **36,109개** 감사 오류 0개. 후보 355개 중 tuple 후보 299개, 원본 셀 29,841개를 정확히 복원했습니다.
- 원문은 **PARTIAL**입니다. `HTML_RECOVERED_WITH_ERRORS`, `TABLE_UNSUPPORTED`를 숨기지 않았습니다. 8 MiB 허용은 중첩 표 등 모든 구조의 지원을 의미하지 않습니다.

7개 원문 전체에서 위치 86,480개 감사 오류 0개, tuple 셀 70,719개 정확 복원을 확인했습니다. 코드가 큰 전체 DOM을 재생성하는 oracle 검증은 파서 측정 이후 별도로 수행했습니다.

8 MiB 합성 부하도 별도 프로세스로 측정했습니다. 고밀도 노드는 약 3.72초에 locator 제한으로 폐기됐고 큰 DOM helper는 약 0.084초에 노드 제한으로 거절됐습니다. 긴 속성·긴 문장·공백 분리 문장 등을 포함한 해당 측정군 최대 peak RSS는 **136,708,096바이트(약 130.4 MiB)**였습니다.

RSS는 macOS 프로세스 전체 high-water 값이며 추가 Python 할당량이나 서버 보장치가 아닙니다. OS 메모리 강제 제한은 새로 두지 않았습니다. 10초는 협력적 검사이며 단일 libxml/C 호출을 선점하거나 동기 파서 도중 asyncio 취소를 즉시 실행하는 hard timeout이 아닙니다. 운영 서버 성능·스모크·첫 정규 배치는 미측정입니다.

## 중요한 미완료: 최종 입력 gold는 여전히 실패

기존 선택 source 구성에서 연간 fragment를 전체 연간 주석으로 바꾸는 **오프라인 component 비교**를 수행했습니다. 기존 필수 조건과 출처, 원래 gaps를 유지했고 네트워크 전체 replay나 최신성 재검증으로 취급하지 않았습니다.

- 최종 news/overview/status: **5,776 / 5,664 / 5,821바이트**.
- 최신 필수 표 **781 / 1018은 여전히 미전달**입니다.
- 전체 연간 주석 후보 355개도 최종 전달 0개입니다.
- 6,000바이트와 앞 24개 후보 제한을 그대로 두었으므로 HTML 반입 확대만으로 최종 품질이 개선됐다고 주장하지 않습니다. 기본/연간 역할을 섞거나 gold를 완화하지 않았습니다.

다음 작업은 일반적인 후보 선정·주제 다양성과 조건을 보존하는 표현 설계입니다. 6,000/12,000/24,000/32,000바이트 비교 및 운영 모델 한도 증액은 이번 승인에 포함하지 않았으며 구현하지 않았습니다. 새 KR 검증군, 미국 공식 SEC 경로, 실제 모델→Markdown→PDF→BUY/SELL·뉴스, 기존 BUY snapshot 두 건 및 최종 운영 게이트는 여전히 남아 있습니다.

## 재현 자료

Git 밖 `workspace/lmg90x0p`에 `html_8mib_audit.py`, `html_8mib_resource_audit.py`, `html_8mib_evidence_20260921.json`, `html_8mib_resources_20260921.json`을 보관했습니다. 원문은 첨부/커밋하지 않습니다. 두 저장소 handoff의 최신 체크포인트를 다음 세션의 출발점으로 사용합니다.
