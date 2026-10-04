# 주간 주도주 리포트 (Weekly Runner Report)

`tools/weekly_runner_report.py` — 사용자와 어시스턴트가 사업 동반자처럼 PRISM을 정기 점검하기 위한 주간 리포트입니다.
기준은 [`TRADING_CHANGE_REVIEW_HARNESS.md`](TRADING_CHANGE_REVIEW_HARNESS.md)의 North star 네 지표입니다.
KR과 US를 각각 한 번씩 만들고, 사용자의 **개인 운영 알림 채팅**으로만 보냅니다. 공개 채널로는 절대 보내지 않습니다.

## 무엇을 보여주는가

| 절 | 내용 | 북극성 지표 |
|---|---|---|
| 1) 이번 주 요약 | 최근 7일 신규 진입·청산·손실 청산·손절 규칙 건수, 실현 손익, 슬롯 사용률 | 3 |
| 2) 주도주 현황 | 진입 후 +20% 이상 오른 종목: 최고 수익, 현재/실현 수익, 보유 비중, 비중 배분 경로, 재진입 횟수, 보유 규칙 적용 여부 | 1 |
| 3) 놓친 대박 | 최근 8주 분석했지만 진입하지 않았거나 일찍 청산한 뒤 +30% 이상 오른 종목과 사유 | 1, 4 |
| 4) 손절 비용 | 손실 청산 건수·합계, 연속 손실 청산, 재진입으로 되찾은 손익 | 2, 3 |
| 5) 트리거별 성적 | 트리거별 진입 수, 대박(최고 +20% 도달) 비율, 평균 수익, 계좌 기여 | 4 |
| 6) 방향 점검 | 북극성 4지표 한 줄씩 평가 + "같이 볼 로그" 3건 | 전체 |

메시지는 3,400자 이하로 절 단위로 나누고, 길면 `(1/2)` 식으로 번호를 붙여 여러 건으로 보냅니다
(`prism_core/ops_alert.py`가 3,500자에서 자르기 때문입니다).

## 데이터 원천과 정의

- **DB:** `stock_tracking_db.sqlite`를 `mode=ro`로만 엽니다. 쓰기는 없습니다.
  KR은 `stock_holdings`, `trading_history`, `watchlist_history`, `holding_decisions`, `analysis_performance_tracker`,
  US는 같은 이름에 `us_` 접두사가 붙은 표를 읽습니다. 표가 없으면 그 시장만 건너뜁니다.
- **대상:** 최근 N주(기본 8주) 안에 진입했거나 청산한 거래와 현재 보유 종목. 멀티 계좌로 같은 진입이 반복된 행은
  (종목, 진입일) 기준으로 한 건만 셉니다.
- **MFE(최대 유리 변동):** 진입일부터 청산일(보유 중이면 오늘)까지 일봉 고가의 최댓값을 가중평균 매수가로 나눈 수익률.
  보유 종목은 `scenario["highest_price"]`도 함께 봅니다. 가격이 없으면 실현/평가 수익률로 대신합니다.
- **대박(주도주):** MFE가 +20% 이상(오닐의 첫 마일스톤). **놓친 대박:** 이후 최고가가 기준가 대비 +30% 이상.
  미진입은 분석 시점 가격, 조기청산은 청산가 기준이며, 이후 다시 진입한 종목은 제외합니다. 같은 종목은 한 줄만 보여줍니다.
- **손절(손실 청산):** 수익률이 0 미만인 청산 전체. 그중 `exit_kind = 'stop'`은 "손절 규칙"으로 따로 셉니다.
  청산 원인은 `exit_kind`(손절 규칙/추세 이탈/매도 판단)이고, 매도 판단은 청산 3일 전 안의 `holding_decisions.sell_reason`을 덧붙입니다.
- **계좌 기여(%p):** `수익률 × 슬롯 비중 ÷ 슬롯 수`. 슬롯 수는 보유 종목 시나리오의 `max_portfolio_size`(없으면 10)입니다.
  슬롯 비중은 `prism_core.slot_weight.slot_fraction`과 같습니다. 최대 낙폭은 청산 순서대로 계좌 기여를 누적한 곡선의 고점 대비 하락입니다.
- **비중 배분 경로:** `scenario["micro_split"]["legs"]`를 누적한 값(예: `25%→50%→100%`). 초분할 기록이 없으면 `1슬롯 일괄`.
- **재진입:** `scenario["reentry"]`(`attempt_label` 등). **보유 규칙:** `scenario["runner"]`가 있으면 `state`/`status` 또는
  `active`/`applied`/`enabled`/`hold` 플래그로 "적용"을 표시합니다. runner 키의 최종 구조가 정해지기 전이라 일부러 느슨하게 읽으며,
  구조가 확정되면 `runner_label()` 한 곳만 맞추면 됩니다.
- **가격 조회:** KR은 `cores.stock_chart.get_market_ohlcv_by_date`(KIS), US는 `prism-us/cores/us_data_client.py`(yfinance).
  시장당 `--max-price-calls`(기본 80)개 종목으로 제한하고(보유 > 최근 거래 > 추적 수익이 높은 미진입 순), KR은 호출 사이에 0.2초를 둡니다.
  한도 초과·실패는 리포트 끝에 "가격 조회 한도·실패로 N종목" 으로 알립니다. 미진입 종목은 가격이 없으면
  `analysis_performance_tracker`의 7/14/30일 수익률로 대신합니다. **DART는 절대 호출하지 않습니다.**

한계: 8주 안의 표본이 작아서(진입 10~20건) 비율은 방향 참고용입니다. 운영 DB의 기존 거래에는
`micro_split`·`reentry`·`runner` 기록이 아직 없으므로(2026-10-04 기준), 이 항목들은 해당 기능으로 진입한 거래부터 채워집니다.

## 실행

```bash
python tools/weekly_runner_report.py --dry-run                 # KR+US 출력만, 발송 없음
python tools/weekly_runner_report.py --market kr --weeks 8     # 한 시장만
python tools/weekly_runner_report.py --no-prices --dry-run     # 가격 조회 없이 DB 필드만
python tools/weekly_runner_report.py                           # 개인 운영 알림 채팅으로 발송
```

옵션: `--db`(기본 `STOCK_TRACKING_DB` 또는 repo의 `stock_tracking_db.sqlite`), `--as-of YYYY-MM-DD`(기본 KST 오늘),
`--weeks`, `--max-price-calls`, `--no-prices`, `--dry-run`.

## 발송 대상과 안전장치

- 발송은 `prism_core.ops_alert.send_ops_alert`만 사용합니다. `.env`의 `OPS_ALERT_BOT_TOKEN`/`OPS_ALERT_CHAT_ID`
  (없으면 `OAUTH_ALERT_BOT_TOKEN`/`OAUTH_ALERT_CHAT_ID`)로 개인 채팅에 보내고, 설정이 없으면 공개 채널로 대체하지 않고 실패 코드(1)로 끝납니다.
- `PRISM_DISABLE_SIGNAL_PUBLISH=1`이면 출력만 하고 발송하지 않습니다.
- `--dry-run`은 어떤 경우에도 발송하지 않습니다.

## cron 제안 (적용하지 않았습니다)

운영 서버(db-server)는 KST이고 `.env`에 알림 설정이 이미 있습니다. 일요일 18:30에 실행하려면 crontab에 다음 한 줄을 추가합니다.

```cron
30 18 * * 0 cd /root/prism-insight && /root/.pyenv/shims/python tools/weekly_runner_report.py >> /root/prism-insight/logs/weekly_runner_report.log 2>&1
```

배포 전에는 `--dry-run`으로 한 번 확인하고, 첫 발송은 수동으로 실행해 개인 채팅에만 도착하는지 봅니다.
같은 시각에 도는 일요일 작업(`weekly_insight_report.py` 10:00 등)과 겹치지 않습니다.

## 테스트

`tests/test_weekly_runner_report.py` — 가상 DB와 가짜 가격 함수만 사용하며 네트워크·Telegram이 없습니다.
멀티 계좌 중복 제거, 초분할 경로·가중평균 매수가, MFE, 놓친 대박(미진입/조기청산/재진입 제외/종목당 한 줄/추적 수익률 대체),
여섯 절 렌더링, 낙폭·연속 손실 계산, 메시지 분할, 읽기 전용 연결, 가격 조회 한도, dry-run·kill switch·개인 채팅 전용 발송을 검증합니다.

```bash
.venv/bin/python -m pytest tests/test_weekly_runner_report.py -q
```

## 읽는 법: 운영 DB 사본 예시 (2026-10-04, KR 일부)

```
[PRISM 주도주 리포트] KR · 2026-10-04 기준 (최근 8주)

1) 이번 주 요약
- 신규 진입 1건, 청산 1건(손실 청산 1건, 그중 손절 규칙 1건)입니다.
- 청산 실현 손익은 평균 -5.8%, 계좌 기여 -0.97%p(슬롯 가중, 6슬롯 기준)입니다.
- 보유 1종목, 슬롯 사용 1.0/6(17%)입니다.

3) 놓친 대박 (최근 8주, 이후 +30% 이상)
- 인제니아테라퓨틱스(Reg.S)(950260) 미진입 9/17, 이후 최고 +67% / 사유: 점수 부족 (5/8)
- 삼화콘덴서(001820) 조기청산 9/3, 이후 최고 +53% / 사유: 손절 규칙으로 -5.2% 청산 후 상승

6) 방향 점검 (북극성 4지표)
- 대박 포착: 진입 9건 중 1건(11%)이 +20%에 도달했습니다. 대박 종목에서 비중이 충분했습니다.
- 손실·낙폭: 청산 기준 최대 낙폭 -3.71%p, 손실 합계 -4.95%p이고 이익은 +1.24%p로 손실이 이익을 앞섭니다.
- 거래 빈도·손절: 주당 평균 1.1건 진입, 손실 청산 6건이며 손절 비용이 이익을 크게 갉아먹고 있습니다.
- 스크리닝 정확도: 분석 후보 65종목 중 진입 9건(14%), 진입 대비 대박 11%, 놓친 대박 5건입니다.
```

북극성 평가 문구는 단순 기준입니다. 손절 비용 비율(손실 합계 ÷ 이익 합계)이 0.4 이상이면 "상당 부분", 0.7 이상이면 "크게 갉아먹고 있다"고
표현하고, 비중이 절반 미만이던 대박 종목이 있으면 증액 규칙 점검을 권합니다. 숫자가 근거이고 문구는 읽기 위한 요약입니다.
