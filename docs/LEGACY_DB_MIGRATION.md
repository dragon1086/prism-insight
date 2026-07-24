# 레거시 DB 읽기 전용 복사 마이그레이션

## 상태와 범위

이 문서는 Phase 1A의 `stock_tracking_db.sqlite` 보존 경계와 copy-only 마이그레이션 계약을 정의합니다. 이 기능은 독립 CLI/라이브러리 기반이며 애플리케이션 runtime에는 연결되지 않았습니다. 사용자의 실제 DB를 자동으로 찾거나 열지 않습니다.

핵심 원칙은 다음과 같습니다.

- 원본은 항상 SQLite URI `mode=ro`와 `PRAGMA query_only=ON`으로만 엽니다.
- 원본에서 `UPDATE`, `ALTER`, `DROP`, `VACUUM`, writable `ATTACH`를 실행하지 않습니다.
- SQLite online backup API로 비공개 임시 snapshot을 만들고, 이후 검사와 변환은 해당 snapshot에 대해서만 수행합니다. 이 방식은 WAL에 commit된 행도 포함합니다.
- 원본 본체와 `-wal`의 checksum, size, mtime을 작업 전후에 비교합니다. 차이가 있으면 결과를 게시하지 않고 실패합니다.
- 대상은 기존 파일이 아니라 새 디렉터리 하나입니다. 그 안에 Task 5 정책으로 `research.sqlite`, `paper.sqlite`, `ops.sqlite`를 새로 생성합니다.
- destination 이름별 `O_EXCL` reservation lock을 획득한 뒤 세 DB를 임시 sibling 디렉터리에서 모두 생성·검증하고 디렉터리 rename으로 게시합니다. 실패 시 작업이 만든 임시 대상과 lock만 삭제합니다.
- 실제 원본 DB, 생성 DB, report JSON은 private user data입니다. commit, upload, 공유 대상이 아닙니다.

## 현재 manifest 결정

현재 Task 5 schema로 의미를 왜곡하지 않고 복사할 수 있는 행만 지원합니다.

| source table | destination DB | destination table | disposition | transform |
|---|---|---|---|---|
| `trading_intuitions` | research | `lessons` | IMPORT | `legacy-lessons-v1` |
| `trading_principles` | research | `lessons` | IMPORT | `legacy-lessons-v1` |
| `stock_holdings`, `us_stock_holdings` | paper | 없음 | DEFERRED_UNSUPPORTED | `deferred-v1` |
| `trading_history`, `us_trading_history` | research | 없음 | DEFERRED_UNSUPPORTED | `deferred-v1` |
| `watchlist_history`, `us_watchlist_history` | research | 없음 | DEFERRED_UNSUPPORTED | `deferred-v1` |
| `analysis_performance_tracker`, `us_analysis_performance_tracker` | research | 없음 | DEFERRED_UNSUPPORTED | `deferred-v1` |
| `holding_decisions`, `us_holding_decisions` | research | 없음 | DEFERRED_UNSUPPORTED | `deferred-v1` |
| `us_pending_orders` | paper | 없음 | DEFERRED_UNSUPPORTED | `deferred-v1` |
| `trading_journal` | research | 없음 | DEFERRED_UNSUPPORTED | `deferred-v1` |
| `portfolio_adjustment_log`, `us_portfolio_adjustment_log` | research | 없음 | DEFERRED_UNSUPPORTED | `deferred-v1` |
| `user_memories`, `user_preferences` | research | 없음 | DEFERRED_UNSUPPORTED | `deferred-v1` |
| `jeoningu_trades` | research | 없음 | DEFERRED_UNSUPPORTED | `deferred-v1` |
| `portfolio_broadcast_log` | ops | 없음 | DEFERRED_UNSUPPORTED | `deferred-v1` |

`DEFERRED_UNSUPPORTED`는 데이터를 버린다는 뜻이 아닙니다. 현재 target schema에 없는 의미를 `observations`, `outcomes`, `positions` 등에 억지로 넣지 않기 위한 명시적 중단 상태입니다. 해당 table이 비어 있으면 검사 metadata만 기록할 수 있지만, 한 행이라도 있으면 migration은 실패합니다. manifest에 없는 table도 같은 원칙으로 처리하며, 비어 있지 않으면 실패합니다. 예상하지 못한 view 또는 virtual table도 unsupported schema로 실패합니다.

보유·주문·계좌 관련 레거시 행은 현재 복사하지 않습니다. 특히 `account_key`, `account_name`, `user_id`, `message_id`를 target payload나 CLI 출력으로 전달하지 않습니다. 이런 table을 지원하려면 별도 target schema와 PIT/identity/계좌 격리 계약이 먼저 승인되어야 합니다.

## 레거시 lesson 격리

두 지원 table은 모두 다음 불변 조건으로 변환합니다.

- `status = LEGACY_UNVALIDATED`
- `strategy_id = LEGACY_UNVALIDATED` (현재 research schema의 안정적인 sentinel application ID)
- `payload_json.activation_allowed = false`
- `payload_json.score_adjustment = 0`
- 레거시 `is_active`는 target 활성 상태로 복사하지 않음
- 원본 table과 transform version을 payload provenance로 저장

따라서 이 복사는 active lesson 승격이나 점수 조정이 아닙니다. 향후 feedback/strategy consumer도 `LEGACY_UNVALIDATED`를 활성 입력에서 제외해야 하며, 그 runtime wiring은 후속 Task 19 범위입니다.

## schema gate

지원 table은 required/optional column allowlist를 사용합니다.

- `trading_intuitions` required: `id`, `category`, `condition`, `insight`, `created_at`
- `trading_principles` required: `id`, `scope`, `condition`, `action`, `created_at`
- 알려진 optional column만 허용하며 `market` drift를 명시적으로 허용합니다.
- required column 누락, 예상하지 못한 column, 잘못된 source ID, 비어 있는 required text, JSON으로 안전하게 정규화할 수 없는 값은 reject입니다.
- `PRAGMA user_version`은 현재 알려진 레거시 값 `0`만 지원합니다.
- reject가 하나라도 있으면 부분 성공하지 않고 전체 migration을 중단합니다.

## checksum과 count

report는 raw row를 포함하지 않고 table별로 다음만 제공합니다.

- source/transformed/rejected row count
- source checksum
- transformed checksum
- destination DB/table, disposition, transform version
- 안전한 issue code

checksum canonicalization은 SQLite storage class를 명시적으로 구분합니다. `NULL`, integer, finite real(`float.hex()`), NFC-normalized text, blob을 서로 다른 typed value로 encode하고, 각 row를 length-prefix한 뒤 SHA-256 ordered fold를 계산합니다. 원본 파일과 WAL fingerprint는 1 MiB chunk 단위로 streaming hash합니다. 지원 table은 declared `id`로 읽고 transform 결과는 안정적인 `lesson_id`로 정렬합니다. source checksum과 transformed checksum은 서로 다른 domain이며 직접 같다고 가정하지 않습니다. destination verification은 transform 결과 checksum과 row count를 다시 계산합니다.

`lesson_id`는 `transform_version + source_table + source id`의 SHA-256 기반 application ID입니다. 동일한 source와 transform은 새 destination bundle에서도 같은 lesson ID와 business-row checksum을 만듭니다. Task 5의 `schema_migrations.applied_at`은 운영 metadata이므로 business-row checksum 대상이 아닙니다.

## 명령

검사만 수행하며 destination을 만들지 않습니다.

```bash
python tools/inspect_legacy_db.py /path/to/stock_tracking_db.sqlite
```

migration 준비 상태만 확인하는 dry-run입니다.

```bash
python tools/migrate_legacy_readonly.py \
  /path/to/stock_tracking_db.sqlite \
  /path/to/new-prism-databases \
  --dry-run
```

준비 상태가 완전할 때만 새 bundle을 생성합니다. destination 디렉터리나 dangling symlink가 이미 있으면 collision으로 실패합니다.

```bash
python tools/migrate_legacy_readonly.py \
  /path/to/stock_tracking_db.sqlite \
  /path/to/new-prism-databases
```

CLI stdout은 metadata-only JSON입니다. source path, raw row, account/user identifier, credential, lesson 본문을 출력하지 않습니다. 실패 출력도 상세 exception이나 private value 대신 고정 error code만 제공합니다.

## 실패와 rollback

다음 조건에서는 fail closed합니다.

- source가 없거나 read-only open/snapshot을 증명할 수 없음
- source 본체 또는 WAL fingerprint 변화
- unsupported `user_version`, view, virtual table
- required column 누락 또는 unexpected column
- non-empty deferred/unknown table
- invalid transform row 또는 reject
- source/transformed/destination count/checksum mismatch
- destination collision
- Task 5 migration 또는 DB boundary 검증 실패
- WAL checkpoint 또는 최종 검증 미완료

실패 시 작업이 만든 hidden staging 디렉터리, 그 안의 DB/sidecar, 그리고 작업이 획득한 reservation lock만 제거합니다. 기존 destination, 기존 lock, sibling 파일, 원본 DB와 원본 sidecar는 삭제하거나 복원하지 않습니다. 비정상 `SIGKILL`/전원 중단은 hidden staging 또는 lock을 남길 수 있으므로 재시도 전 운영자가 source/destination 상태를 확인하고 해당 attempt가 만든 orphan임을 증명한 뒤 정리해야 합니다. 성공 시에도 원본은 그대로 유지되므로 rollback은 새 destination bundle을 사용하지 않고 별도로 삭제하는 방식입니다. 원본에 대한 in-place rollback은 존재하지 않습니다.

## 운영 제한

현재 구현과 테스트는 pytest temporary fixture DB만 사용합니다. 이는 migration contract의 결정론적 foundation만 증명하며 operated migration readiness가 아닙니다. 실제 `stock_tracking_db.sqlite`의 schema/row metadata를 검사하지 않았고 실제 migration도 수행하지 않았습니다. 실제 source에 non-empty deferred/unknown table이 있으면 의도대로 중단됩니다. Operated readiness를 주장하려면 별도 승인 아래 실제 source를 read-only로 inventory하고 immutable snapshot/fingerprint를 만든 뒤 hidden staging destination으로 dry-run copy하여 source/transformed/destination count, reject, checksum, WAL/sidecar 안정성을 검증해야 합니다. 실제 source 검증이 없으면 fixture 결과로 대체하지 않고 `not operated`로 남깁니다. 후속 table 지원은 별도 schema/manifest version과 테스트·승인을 통해 추가해야 합니다.
