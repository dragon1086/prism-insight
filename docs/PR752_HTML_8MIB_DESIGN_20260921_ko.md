# PR752 HTML 8 MiB 확대: 승인 범위와 검증 계약

## PRD

- 기준 HEAD: `7b4c7171d784619d40a69a60cd15f52418dca809`. 기존 Draft PR #752에서만 개발하며 머지·배포하지 않는다.
- 공시 HTML 파서와 검증된 DART viewer 본문의 개별 UTF-8 상한을 2 MiB에서 8 MiB로 확대한다. 8 MiB는 성공 보장이 아니라 최대 반입 크기다.
- DART 전체 취득 8 MiB, fixture 전체 12 MiB, 호출 수, Markdown 2 MiB, 모델 담당자별 6,000바이트, 후보 24개 제한은 유지한다. HTML과 관계없는 목록·회사 식별·JS 제어 문서의 기존 한도도 유지한다.
- 기존 비공개 원문을 재사용한다. 새 HTTP·모델·주문 호출과 운영 설정 변경은 이번 검증에 포함하지 않는다. 원문은 커밋하거나 첨부하지 않는다.
- 원문 hash/기간/발행사/연결·별도/기본·연간 역할을 보존한다. 최신 소멸·승소와 과거 진행 상태를 뒤섞지 않는다. 종목 특례 및 gold 완화는 금지한다.

## 설계

1. HTML 바이트 정책을 한 상수로 정의하고 파서·DART admission·허용된 viewer 수집·fixture 재생에서 공유한다. 합계 예산은 별도 상수로 고정하여 개별 상한에 비례하지 않도록 한다. fixture의 제어 응답은 기존 2 MiB를 유지한다.
2. 목록/cover/JS를 무조건 8 MiB로 확대하지 않는다. 수집기의 검증된 financial section/notes/fragment 요청에서만 큰 본문을 허용한다. 큰 본문의 scope/provenance 보조 경로는 8 KiB feed, 전체 노드 100,000개(주석 포함), 깊이 100, 본문당 공통 10초 monotonic deadline으로 제한한다. 한 root와 deadline을 scope 검사와 preview에 공유한다. 전체 XPath 목록 대신 순회하고 각 후보 처리 전후에 시간을 검사한다. preview는 itertext로 공백 정규화한 앞 1,200자만 만든다. 이 새 전체 DOM 한도는 아래 streaming 활성 노드 한도를 대체하지 않는다.
3. 기존 활성 노드 30,000, 깊이, record 2,000, 문서 grid 120,000, origin cell 60,000, locator 16 MiB 및 개별 표 구조 제한은 유지한다. 실패 시 이미 만든 근거를 정상으로 반환하지 않는다.
4. 파서에 협력적 monotonic 처리 시간 한도 10초를 적용한다. feed/event 경계 및 반환 전에 확인하고 시간 초과 시 records/headings를 모두 폐기한다. 단일 libxml/C 호출을 선점하는 hard timeout으로 설명하지 않는다. 실제 취소 가능한 async 수집과 동기 파서의 지연 상한을 별도로 보고한다.
5. 기존 캐시가 새 취득/파서 결과로 오인되지 않도록 parser revision을 갱신하고 평가 fingerprint에 정책 파일을 포함한다. fixture는 기존 스키마/해시/순서/완전성 검증을 유지한다.

## 회귀 기준과 실행 순서

독립 설계 검토 → 경계 테스트 RED → 구현 → 단위/실모듈 통합/자원 실측 → 독립 코드 검토 순서다.

- ASCII/다중바이트: 2 MiB 초과 반입, 정확히 8 MiB, 8 MiB+1 거절; 잘못된 UTF-8/해시/URL/scope 거절 유지.
- parser late evidence와 원래 XPath, 단위·각주·조건 보존; record/grid/cell/locator/node/depth/table 상한을 초과하면 명시적 실패.
- 합계 8 MiB 직전/초과에서 종료하고 후속 요청/불완전 결과를 성공 캐시에 넣지 않는다. 정확히 8 MiB짜리 한 본문은 앞선 조회 비용 때문에 실제 취득에서 실패할 수 있음을 검증한다.
- streaming/이미 buffered transport, EOF 전 중단, timeout/cancellation, fixture 기록/재생 및 이전 fixture 호환성.
- 저장된 2,833,488바이트 원문의 SHA 확인 후 처리 시간/peak RSS/구조 통계/조건·위치 보존을 측정한다. 여러 기존 KR 업종 원문도 같은 방식으로 검증한다. 이미 사용한 표본은 새 독립 홀드아웃이라고 부르지 않는다.
- 원문 접근 가능 여부와 최종 6,000바이트 packet의 필수 gold 전달 여부를 각각 판정한다. 기존 S3 gold 실패가 그대로 남으면 숨기지 않는다.

## 완료 및 안전 경계

새 의존성 없음. 매매 프롬프트·보유기간·손절·재진입·주문 규칙 변경 없음. 배포 스모크/첫 정규 배치는 미실행으로 기록한다. 실제 모델/PDF/BUY/SELL, 새로운 KR 홀드아웃 및 미국 공식 공시 검증은 별도 잔여 작업이다.
