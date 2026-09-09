# 과거 청산 기록 전용 자료 계약 v1

상태: 구현·로컬 fixture 검증. **서버 원본 조회와 운영 자료 내보내기는 아직 실행하지 않았습니다.**
실행 전 담당자의 코드·출처·시간대 검토가 필요합니다.

## 목적과 금지선

`tools/build_historical_closed_record_evidence.py`는 기존
`tools/backfill_observability.py`의 검토된 청산 이력 조회문과 시각 파서를 재사용합니다.
그러나 기존 backfill CLI, emit_event, production spool, state writer는 호출하지 않습니다.
후보 성과 테이블도 조회하지 않습니다. 30일 성숙 후보만 추출하는 기존 후보 조회문으로
전체 후보 집합이나 새로운 candidate.evaluated를 만들지 않습니다.

별도 계약은 `historical-closed-record-evidence-v1`, schema 1입니다.
현재 prospective Evidence Packet이나 그 필터를 변경하지 않습니다.
`ingestion_mode=backfill`, `prospective=false`, `independent_holdout=false`이며
SHADOW/LIVE 자동 승격은 금지됩니다. 이 파일은 현행 prospective Packet 입력이 아닙니다.

## 원본 정체성과 계정별 분리

- 원본 테이블 PK로 `source_record_ref`를 만듭니다. 원래 decision/position ID라는
  뜻이 아니며 `original_decision_ref`와 `original_position_ref`는 null입니다.
- account_key는 메모리에서 별도 `legacy_book_ref`를 만드는 데만 사용합니다.
  원문 계정 키·계정명·회사명·scenario·sector는 출력하지 않습니다.
- 서로 다른 account_key는 별도 가명 legacy book으로 유지합니다. 같은 종목·시간·가격을
  근거로 서로 다른 행을 한 전략 거래로 합치지 않습니다.
- 원본 계정 키가 없으면 `UNKNOWN_UNGROUPABLE`이며 가상의 공통 계정을 만들지 않습니다.
- 두 참조 모두 전용 32바이트 비밀키 기반 HMAC-SHA256입니다. 일반 거래용 비밀키나
  인증 토큰을 재사용하지 마십시오. 안정적인 재실행에는 같은 전용 키와 namespace가 필요합니다.
- `canonical_strategy_book_verified=false`입니다. 가명 legacy book의 건수를 canonical
  전략 표본 수로 주장하거나 계정을 합친 성과를 계산하지 않습니다. 브로커 체결 여부는
  행을 제외하는 조건이 아닙니다.

## 허용 필드와 누락

기록된 진입·청산 시각, 가격, 원본 수익률, 보유 일수, 알려진 trigger/mode/exit-kind만
보존합니다. 수익률을 다시 계산하거나 현재 가격을 조회하지 않습니다. 등록된 문자열
목록에 없는 trigger 등은 null로 남깁니다. 임의 원문이 출력되는 것을 막기 위한 제한이며,
누락·미인식 trigger는 별도 quality reason으로 표시합니다.

원래 판단 시각, regime, policy, 손절가 변경 이력, decision/position 연결은 만들지 않습니다.
현재의 regime이나 프롬프트로 빈칸을 채우지 않습니다. `observed_at`도 null입니다.
원본 청산 시각을 해석하지 못하면 고정 마감을 지켰는지 증명할 수 없어 제외합니다.
그 외 부정확한 원본 필드는 null/quality reason을 유지하며 분석 적격성을 주장하지 않습니다.

## 시간·해시 계약

- 마감은 `2026-09-09T16:18:06.031436Z`로 고정됩니다.
- offset 없는 원본 시간대는 필수 CLI 인자로 선언해야 합니다. 시장명으로 추정하지 않습니다.
  KR라도 실제 기록 코드가 어떤 시간대를 썼는지 담당자가 확인해야 합니다.
- `retrieval_started_at`/`retrieved_at`은 실제 내보내기 시각입니다. 과거 관측 시각으로 위장하지 않습니다.
- `source_projection_sha256`은 민감정보가 제거된 허용 컬럼 투영을 식별합니다.
  전체 DB 원본이나 변경 이력의 불변성을 증명하는 해시가 아닙니다.
- 같은 SQLite 읽기 트랜잭션에서 얻은 투영으로 `logical_source_sha256`을 계산합니다.
  동일 원본·키·namespace·시간대의 재실행은 같은 논리 해시를 가져야 합니다.
- 실행 시각까지 포함한 `artifact_sha256`은 개별 내보내기를 식별하므로 실행마다 달라질 수 있습니다.

## 읽기·쓰기 경계

- SQLite는 `mode=ro`, `PRAGMA query_only=ON`, 허용 목록 authorizer를 함께 사용합니다.
  UPDATE/DELETE/CREATE/ATTACH/load_extension 및 query_only 해제는 금지합니다.
- 30초 SQLite 진행 제한, 10만 개 출력 행 제한을 둡니다.
- 실행 중인 WAL DB에는 `immutable=1`을 쓰지 않습니다. 미반영 WAL을 무시해 오래된 내용을
  읽는 것을 피하기 위해서입니다. 운영 읽기에서 필요한 기존 SQLite WAL/SHM 조정 파일의
  접근 특성까지 파일시스템 수준 불변성을 주장하지는 않습니다. 엄격한 파일시스템 무변경이
  필요하면 담당자가 먼저 만든 일관된 별도 스냅샷을 입력하십시오.
- 출력 root는 원본 DB 디렉터리 밖이어야 합니다. 기존 파일·심볼릭 링크는 덮어쓰지 않으며
  JSONL/spool 경로도 받지 않습니다. 최종 JSON은 처음부터 권한 0600으로 새로 만듭니다.
- 출력 root 및 전용 HMAC 키 파일은 담당자가 미리 준비합니다. 도구는 디렉터리, 키,
  서버 설정, spool, state, 원본 DB를 생성하거나 바꾸지 않습니다.

## 검토 후 실행 예시

다음은 명령 형식일 뿐이며 이 문서를 작성하면서 운영 DB에 실행하지 않았습니다.

```bash
python tools/build_historical_closed_record_evidence.py \
  --db /absolute/reviewed/source.sqlite \
  --market KR \
  --source-namespace prism-legacy-history-v1 \
  --naive-source-timezone Asia/Seoul \
  --pseudonym-key-file /private/dedicated-history-hmac.key \
  --output-root /absolute/isolated-export \
  --output /absolute/isolated-export/kr-historical-closed-v1.json
```

서버에서 내보낸 뒤에도 먼저 계정별 건수·기간·결측과 원본 시간대·book 의미를 검토해야
합니다. KR 자료를 확보했다는 사실만으로 ADX 재생, 분봉 손절 연구, canonical 전략
독립성, prospective 성과가 검증된 것은 아닙니다.
