# 2주 점검 (2026-10-18) — 무엇이 어디에 남고, 무엇을 볼 것인가

10/2~10/4에 여러 매매 변경이 한꺼번에 LIVE가 되었습니다. 첫 실전은 한국 10/6(월), 미국 10/6 02:50 KST부터입니다.
10/18에 사용자와 함께 "각 변경이 효과가 있었는가"를 판단합니다. 사용자 지시는 "빈틈없이 로깅해서 모든 측면을 보도록"입니다.
그래서 변경마다 **발동했는가, 얼마나 자주, 결과와 반사실(그 규칙이 없었다면), 모순·실패**를 증거로 답할 수 있어야 합니다.
판단 기준은 [하네스 North star](TRADING_CHANGE_REVIEW_HARNESS.md)입니다.

## 1. 실행

```bash
# 출력만 (텔레그램·파일 없음)
python tools/two_week_review.py --dry-run
# 10/18 본 실행: 운영자 전용 알림방(prism_core.ops_alert, 3400자 단위 분할) + logs/reviews/ 전체 파일
python tools/two_week_review.py --start 2026-10-05 --end 2026-10-18
```

- 읽기 전용입니다. 추적 DB와 B3 저장소는 `mode=ro`로 엽니다. 이벤트 스풀, `runtime/` 상태 파일, 로그는 읽기만 합니다.
- 가격은 주간 주도주 리포트와 같은 경로(KR `cores.stock_chart`, US prism-us `USDataClient`)로 시장당 최대 120회(`--max-price-calls`)
  조회합니다. 반사실 가격(매도 보류·막힌 증액·자리를 잃은 후보)을 먼저 조회해 한도에 밀리지 않게 합니다. DART는 부르지 않습니다.
- 본 실행 결과는 `logs/reviews/two_week_review_<시작>_<끝>.txt`에 전체가 남으므로 SSH로 그대로 읽을 수 있습니다.
- 옵션: `--market kr|us`, `--no-prices`(DB·이벤트만), `--no-logs`(로그 집계 생략), `--db`, `--events`, `--runtime`, `--log-root`, `--out-dir`.

## 2. 근거가 남는 곳 (운영 서버 기준)

| 근거 | 위치 | 보존 |
|---|---|---|
| 관측 이벤트 | `logs/prism_events.jsonl` (`observability.events.emit_event`, ClickStack으로 전송) | 지우지 않음(회전 없음) |
| 보유·청산·시나리오 | `stock_tracking_db.sqlite`: `stock_holdings`/`trading_history`(+`us_`), 시나리오 JSON에 `micro_split`(다리·가중), `runner`, `reentry`, `_entry_score_policy`, `_decision_context` | 영구 |
| 매수 보류 | `watchlist_history`/`us_watchlist_history`(점수·사유·시나리오의 `rejection_reason`) | KR은 30일 지나면 삭제(2주 점검에는 영향 없음) |
| 증액 집행기 감사 | `runtime/b3-ae-shadow.sqlite` `b3_events` (`add_plan.qualified/executed/invalidated/rail_blocked`) | 영구 |
| 재진입 v3 | `runtime/reentry_v3_state_{kr,us}.json`(감시·판단·재점검·주문·가상 장부), `runtime/reentry_v3_recheck_results_*.jsonl`, `runtime/reentry_v3_live_*.jsonl` | 끝난 감시는 150일 뒤 보관 파일로 이동 |
| 배치 로그 | 저장소 루트 `orchestrator_YYYYMMDD.log`, `us_orchestrator_YYYYMMDD.log`(트래커·매도 판단 줄 포함) | 1일 뒤 gzip, gz는 30일 |
| 루프·누적 로그 | `logs/loop_*.log`, `logs/trend_exit_seller.log`, `logs/us_{morning,afternoon}.log`, `logs/oauth_health.log` | 회전 없음 |
| 트리거 결과 JSON | `trigger_results_*.json` | **7일 뒤 삭제** → 그래서 선발 근거를 이벤트로도 남김(아래) |
| KR 배치 표준출력 | `logs/stock_analysis_*.log` | **일요일마다 비움** → KIS 한도 초과를 이벤트로도 남김(아래) |

## 3. 이번에 추가한 기록 (동작 변화 없음, 실패해도 매매에 영향 없음)

| 변경 | 빈틈 | 추가한 기록 |
|---|---|---|
| 주도주 보유 규칙(#909) | 매도를 막은 순간의 가격이 없어 "막은 게 도움이 됐나"를 판단할 수 없었음 | `runner.sell_blocked`(llm/final/trend_exit 모두)에 `current_price`, `entry_ref`, `buy_price`, `stop_loss`, `peak_close`, `hold_until`, `gain_now_pct`, `phase`. `runner.exit_forced`에 같은 가격 사실, `runner.stop_*`·`runner.detected`에 `current_price`. 로그에도 `price=` |
| 트리거 품질 우선순위(#907) | 보장을 잃은 후보가 로그에 트리거 이름만 남고, 결과 JSON은 7일 뒤 삭제 | 배치마다 `trigger_quality.selection` 이벤트와 JSON `metadata.trigger_quality.selection`: 예전 방식이면 뽑혔을 후보(`displaced`: 종목·트리거·점수·가중치·기준가), 대신 점수 경쟁으로 뽑힌 후보(`fill_picks`), 보장 제외 트리거, 선발 수. 예전 방식(모든 트리거 1등 보장, 등록 순서, 슬롯 한도)을 같은 top-down 결과에서 그대로 재생해 계산. 로그 `[TRIGGER_QUALITY] displaced ticker=...` |
| 증액·가속·위험 한도(#875/#876/#908) | 조건은 맞았는데 안전장치(위험 한도·추격 한도·피라미드)로 막힌 증액이 기록되지 않음. 위험 한도 계산에 쓴 손절선이 없음 | 막힌 증액마다(계획·세션·시나리오별 1회) `micro_split.add_blocked` 이벤트 + `b3_events` `add_plan.rail_blocked`(막힌 사유, 그 시점 가격, 유효·최초 손절선, 가속 판정값). `add_plan_qualified`에 `planned_target`, `current_stop`, `initial_stop`. `add_executed`에 브로커 수량·사유, 계획 목표, 트리거 가격, 세션 |
| 재진입 v3(#900) | 재점검 거절 사유·판단가가 이벤트에 없음, 주문 결과의 체결 기준가 없음 | `reentry_v3.shadow_recheck`에 `rejection_reason`, `decision_price`, `level`. `reentry_v3.live_entry`에 `entry_price`, `holding_count`, `live_skipped`에 `decision_price` |
| AI 매도 실패 시 대체 규칙 | 대체 규칙이 성공하면 아무 기록이 없고, `trading_history`에는 매도 사유 열이 없어 옛 "+10% 익절"이 AI 매도와 같은 `exit_kind='ai'`로 섞임 | KR·US 대체 경로마다 `sell.fallback_used`(계기 `empty_response/parse_failed/json_error/analysis_error`, 매도 여부, 사유, `legacy_ten_pct_rule`) |
| KIS 초당 호출 한도(EGW00201) | 표준출력 print뿐이고 KR 배치 표준출력은 일요일마다 비움 | `kis.rate_limited` 이벤트(API 경로, 상태 코드, 응답 앞부분) |

매수 판단의 "사유 없는 보류"(과거 KR 151건, 이후 큰 수익 44%)는 현재 경로에서 시나리오 `rejection_reason`으로 남고 있음을 운영 DB로
확인했습니다(9/1 이후 사유 없는 행 0건). 점검 도구가 배치마다 사유 없는 보류를 세어 다시 생기면 바로 보입니다.

## 4. 변경별 점검 질문 (리포트 절 번호와 같음)

### 1) 초분할 첫 매수 비중 · 최소 점수 5점(#873) · 보유 7종목 이상 6점 규칙(#908)
- 신규 진입 중 초분할 비율, 첫 비중 분포(30~80%). 계획을 못 세워 건너뛰거나 1슬롯으로 산 경우(로그 `[MICRO_SPLIT_SCORE] ... plan unavailable`, `unavailable, legacy full slot`).
- 반사실: 같은 종목을 처음부터 1슬롯 샀다면의 계좌 기여와 차이(손실 방어 vs 수익 축소).
- 5점 하한 덕분에 들어간 진입(예전 국면 기준 미달)의 결과와 +20% 도달 수.
- 7종목 6점 규칙은 **프롬프트 규칙이며 코드 강제가 없습니다.** 근거는 시나리오 `_decision_context.slots_used`와 점수. 보유 7개 이상에서 5점 후보 보류 수와, 6점 미만 진입(위반 의심) 목록을 봅니다.

### 2) 증액 시나리오 · 가속 구간 두 번째 증액 · 위험 한도
- 계획 수와 상태(ACTIVE/INVALID/CANCELLED), 버린 시나리오 사유(`PLAN_MISSING` 비율이 높으면 BUY가 계획을 안 쓰는 것).
- 증액 주문 수, 실패·미체결, 가속 두 번째 증액(최초가 대비 상승률·거래량 배수·매수가).
- 반사실: 증액분 손익(증액 다리별 증액가→현재가·청산가, 장부 기준). 증액이 없었다면 그만큼 덜 벌었거나 덜 잃었습니다. 증액 후 손실 종목 수.
- 위험 한도로 줄어든 증액, 안전장치로 막힌 증액과 그 뒤 가격(오르면 기회 손실).
- 한계: 증액 체결가는 지정가(전략 장부 가격)입니다. 실제 체결 확인은 브로커 기록을 따로 봐야 합니다.

### 3) 상위 셋업 가중
- 가중 진입 수, 기본→가중 비중, 가중분(더 산 몫) 손익 = (가중 − 기본) × 첫 매수가 대비 수익률 ÷ 10슬롯.

### 4) 주도주 보유 규칙
- 판정·급등형 제외·손절가 재설정·AI 손절가 변경 무시·규칙 매도 수.
- 매도 보류(종목·날짜·경로별 1건)의 경로·사유 분포, 보류 시점 가격 대비 현재가(청산했으면 청산가) → 보류가 도움/손해 건수.
- 주도주별 최고 수익과 현재·청산 수익(반납 폭).
- 한계: 보류 시점 가격은 이 PR 배포 이후 기록부터 있습니다(이전 형식은 반사실 계산에서 제외하고 건수를 보여 줌).

### 5) 재진입 v3
- 감시 수, 기간 내 판단 횟수, 신호, AI 재점검 결과(OK/PARSE_ERROR/ERROR/SKIPPED_CAP), 승인 수, 주문 단계 결과(BOUGHT/SKIPPED_*).
- 실제 재진입 결과(손절·+20% 도달).
- 반사실: 같은 규칙의 가상 장부에서 AI 승인 신호 vs 거절·미평가 신호의 평균 수익. 거절 쪽이 더 좋으면 재점검이 기회를 막은 것입니다.
- AI 거절 사유 상위.

### 6) 트리거 품질 우선순위
- 보장에서 빠진 트리거와 빈도.
- 반사실: 자리를 잃은 후보 vs 대신 뽑힌 후보의 선발 기준가 대비 현재가·최고가, +20% 도달 수(손절 미반영).
- 강한 트리거 진입 비중: 이번 기간 vs 직전 같은 기간.
- 유지·중단 조건은 [TRIGGER_QUALITY_PRIORITY_ko.md](TRIGGER_QUALITY_PRIORITY_ko.md)에 미리 고정한 기준(20거래일)을 따릅니다. 2주는 중간 점검입니다.

### 7) 매수 판단(#880 채점표, #889 초분할 문구) · 스크리닝 필터(#878) · 미국 자본잠식 F2(#882) · 구독자 오래된 신호(#879)
- 분석 건수 대비 진입률, 점수 분포(≤3·4·5·6·7·8+), 보류 사유 분류, 사유 글 없는 보류, 직전 기간 대비 진입 수.
- 보류했는데 이후 +30% 이상 간 종목(놓친 큰 수익)과 그 사유.
- #880·#889는 프롬프트 변경이라 새 필드가 없습니다. 점수 분포·진입률·놓친 큰 수익으로 봅니다.
- #878 `[SCREENING-FILTER]` 로그 횟수·제외 종목 합계, #882 사실 블록 첨부·조회 실패, #879 `[STALE_SIGNAL]`.
- 한계: 구독자는 맥미니에서 돌므로 #879는 맥미니 `logs/subscriber_YYYYMMDD.log`를 따로 확인해야 합니다. 진입 직전 차단(재매수 쿨다운, 보유 한도, 매수 실패, 계획 불가)은 DB 행이 없어 로그 집계로만 봅니다.

### 8) 오류·대체 경로
- AI 매도 대체 규칙 사용(옛 +10% 익절 포함), 청산 기록의 옛 규칙 문구(`exit.executed`의 매도 사유).
- KIS 한도 초과(이벤트+로그), 조회 재시도, 토큰 실패.
- ChatGPT 응답 오류 코드별(429 쿼터, 401 로그인), 로그인 갱신 실패, 프록시 시작 실패, OAuth 경보.
- 심각도 ERROR 이벤트, 텔레그램 수신 미확인 발송.

### 9) 북극성 성적표
- 큰 수익 포착률과 그 종목의 비중 경로(증액·재진입 포함), 최대 낙폭·손실 합계, 거래 빈도와 손절 비용 비율, 스크리닝 정확도(후보 대비 진입, 진입 대비 큰 수익, 놓친 큰 수익).
- 직전 같은 기간과 청산 수·손실 청산·손절·계좌 기여 비교.

## 5. 해석 주의

- 2주는 표본이 작습니다. 숫자는 판단 재료이지 결론이 아닙니다. 시장 국면 변화와 섞여 있으니 같은 기간 직전 비교를 함께 봅니다.
- 날짜는 모두 KST입니다(이벤트 UTC 시각을 KST 날짜로 바꿈). 미국 보유 행의 날짜도 서버 시각(KST)입니다.
- 다계좌는 (종목, 매수일)로 한 번만 셉니다. 계좌 기여는 10슬롯 장부 기준 %p입니다.
- 가격 조회가 실패하거나 한도에 걸리면 해당 반사실은 빠지고, 0) 절에 조회·생략 횟수가 나옵니다.

## 6. 10/18 1회 실행 cron 제안 (crontab은 수정하지 않음)

배포 후 사용자 확인을 받아 db-server crontab에 한 줄을 넣고, 실행 뒤 지웁니다. 연도 가드로 내년에 다시 돌지 않게 합니다.

```cron
CRON_TZ=Asia/Seoul
0 19 18 10 * [ "$(date +\%Y)" = "2026" ] && cd /root/prism-insight && /root/.pyenv/shims/python tools/two_week_review.py --start 2026-10-05 --end 2026-10-18 >> /root/prism-insight/logs/two_week_review.log 2>&1
```

- 결과 파일: `/root/prism-insight/logs/reviews/two_week_review_2026-10-05_2026-10-18.txt`
- 사전 점검: 배포 직후 `python tools/two_week_review.py --dry-run --max-price-calls 20`으로 출력이 나오는지 확인합니다.
