# KR 일일 제품 사용자 UAT 패키지 — StockEasy 정정 실행

상태: 사용자 검토 대기. `uat_accepted=false`, `operational_readiness=false`.

이 문서는 PR #60의 `STOCKEASY_UNAVAILABLE` 주장을 폐기하고, 2026-07-30 실제 승인된 bounded StockEasy UI snapshot을 production `python -m prism_app kr-daily` 경로에 연결한 실행을 기록합니다. fixture나 수동 overlay가 아니라 기존 후보 발굴, KIS/AgentNews, OAuth 분석 시도, SQLite, Markdown report, dashboard를 거친 실제 실행입니다.

## 1. 사용자가 먼저 확인할 결론

- StockEasy 실제 import는 `CONNECTED / SITE_AVAILABLE / IMPORTED`였습니다.
- 네 필수 행은 모두 `IMPORTED`: `SECURITIES`, `MARKET_OVERVIEW`, `LEADING_SECURITIES`, `LEADING_SECTORS`.
- 원천 주장 19건(`CORE_PRISM` 12 + `SUPPLEMENTAL_LEADERSHIP` 7)은 stable identity 14건으로 dedupe되었고 절단은 0건입니다.
- StockEasy-only `009540`과 core+supplement 중복 종목이 모두 기존 후보/report/dashboard 경로에 나타났습니다.
- KIS/KRX가 계속 가격·수익률·적격성 권한을 보유합니다. StockEasy 숫자는 전략 feature에 주입되지 않았고 `entry_signal_authority=false`입니다.
- 13개 종목에서 SWING/TREND 각각의 결정 감사 snapshot과 proposal record가 저장되었습니다. 그러나 26개 외부모델 응답은 모두 parse/validation `REJECTED`여서 유효 시나리오로 완료되지 않았습니다.
- `079550`은 KIS market-data transport 실패로 두 전략을 시작하지 못했습니다.
- 따라서 최종 제품 상태는 정직하게 `ANALYSIS_INCOMPLETE`이며, 투자 결론 `NO_ENTRY`나 제품 완료로 바꾸지 않습니다.
- broker/account/order/message/schedule/credential/risk effect는 없었습니다.

## 2. 실제 실행 시각과 원천

| 항목 | 결과 |
|---|---|
| 요청 시각 | `2026-07-30T11:10:50+09:00` |
| context 시각 | `2026-07-30T11:10:57.690232+09:00` |
| 최종 as-of | `2026-07-30T11:12:19.671530+09:00` |
| 세션 날짜 | `2026-07-29` |
| 후보 원천 | KRX data 실패 후 명시적 Naver fallback + StockEasy supplement |
| source 상태 | AgentNews `FRESH`, KIS `PARTIAL` |
| StockEasy 관측/가용 | `2026-07-30T10:17:42+09:00` / `2026-07-30T10:30:24+09:00` |
| StockEasy 품질 | `FRESH` |

StockEasy `SITE_AVAILABLE`은 capture-time 사용자 관측입니다. `site_status_basis=OPERATOR_ATTESTED_VISIBLE_UI_SNAPSHOT`, `site_currently_verified=false`로 저장되어 check-time 네트워크 연결을 주장하지 않습니다.

## 3. 후보 및 전략 경로

| 항목 | 결과 |
|---|---:|
| raw assertions | 19 |
| core / supplement | 12 / 7 |
| stable identities | 14 |
| excluded / invalid / truncated | 0 / 0 / 0 |
| 두 전략 audit record가 저장된 종목 | 13 |
| candidate transport failure | 1 (`079550`) |
| decision snapshots | 26 |
| trade-plan proposals | 26 |
| proposal parse `REJECTED` | 26 |
| proposal validation `REJECTED` | 26 |

13개 종목의 정량 feature/score와 hard veto는 보존되었지만, invalid proposal을 투자 판단으로 투영하지 않았습니다. 이 실행은 StockEasy 후보가 정상 KIS/정량/OAuth 감사 경로로 들어갔음을 보이지만 유효한 SWING/TREND 시나리오 완성을 증명하지 않습니다.

## 4. StockEasy 권한·증거 경계

- visible evidence 25건: KOSPI/KOSDAQ breadth, investor flow, 주도 그룹, 7개 leading-security turnover/session-return, 시장 수준 no-new-52-week-high 상태.
- screenshot을 만들지 않았으므로 `temporary_capture_used=false`, `temporary_capture_deletion_verified=true`입니다.
- `price_authority=KIS_KRX`, `entry_signal_authority=false`, `fail_soft=true`.
- `authority_crosscheck_status=NOT_PERFORMED`, `supplemental_numeric_values_used_for_strategy=false`.
- 상대강도·모멘텀·peak는 승인 화면에서 명확히 관측되지 않아 만들지 않았습니다.
- cookie, token, credential, internal API, account/payment/profile, raw private payload를 수집하지 않았습니다.
- 현재 약관 확인은 자동화·재배포 승인을 의미하지 않습니다. private personal-research bounded observation에 한정합니다.

## 5. SQLite 및 사용자 표면 readback

읽기 전용 DB 조회 결과:

| 레코드 | 건수 |
|---|---:|
| `decision_snapshots` | 26 |
| `trade_plan_proposals` | 26 |
| `proposal_dispositions` | 0 |
| `proposal_outcomes` | 0 |
| `retrospectives` | 0 |
| `lesson_candidates` | 0 |
| `ops.job_runs` | 26 |

Markdown와 dashboard는 동일한 StockEasy status, 네 requirement, 25 observations, source/capture/ingestion clocks, content hash, permission/source-scope ID, 권한 경계를 표시합니다. Dashboard count와 command result count도 일치합니다.

결정 감사 spine은 존재하지만 outcome/retrospective/lesson/next-run SHADOW retrieval이 없으므로 **self-feedback incomplete**입니다.

## 6. Private artifact manifest

아래 파일은 ignored `.hermes/uat/t_57dc74b9/runtime-connected-final-v2/`에만 있으며 커밋·업로드하지 않습니다.

| 파일 | SHA-256 |
|---|---|
| `result.json` | `584fec771337d4827471c53111ca08d45c225b4a70db67f34db673328b73494e` |
| `kr-daily.md` | `5cbae8c5c2d72963a9e133fcf9ed62cf567f9b2cea0b3655cd7fbcfb278b3690` |
| `dashboard.json` | `08c17820bae19846ee13d5fea53ecbac7ef715d4eb413b3447316be34217775b` |
| `research.sqlite` | `8f402cc4418163be3cecc85c1933e2a99a1a92c27feda5761e0979da9da00ee3` |

Snapshot/permission/collection manifest와 세부 checklist는 `docs/STOCKEASY_UAT.md`에 있습니다.

## 7. 사용자 UAT 체크리스트

- [ ] 실제 private report/dashboard에서 `CONNECTED / SITE_AVAILABLE / IMPORTED`와 네 imported 행을 확인했습니다.
- [ ] 7개 StockEasy nomination, stable dedupe, StockEasy-only/중복 channel을 확인했습니다.
- [ ] StockEasy가 KIS/KRX 권한을 대체하지 않고 전략 숫자 입력으로 사용되지 않았음을 확인했습니다.
- [ ] 26 proposal이 모두 invalid/rejected이며 `ANALYSIS_INCOMPLETE`가 유지된 것을 확인했습니다.
- [ ] `079550` KIS failure가 숨겨지지 않았음을 확인했습니다.
- [ ] DB/report/dashboard의 count, provenance, hash를 확인했습니다.
- [ ] self-feedback와 operated readiness가 아직 미완성임을 확인했습니다.
- [ ] broker/account/order/message/schedule/credential/risk effect가 없음을 확인했습니다.
- [ ] 위 제한을 포함한 bounded StockEasy runtime/readback을 승인합니다.

마지막 항목은 사용자만 승인할 수 있습니다. `user UAT approval: not granted` 상태이며 테스트나 CI로 자동 승인하지 않습니다.
