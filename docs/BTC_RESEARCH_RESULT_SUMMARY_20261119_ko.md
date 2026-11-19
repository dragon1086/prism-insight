# BTC 연구 결과표

M2/M3 표시 개선이다. 기존 보고서·경제 재생 테이프·연구 호출 원장만 읽는다.
외부 패키지, 새 모델 호출, 시세 수집, 주문, 운영 스케줄이나 매매 규칙을 추가하지 않는다.
기존 `run_replay`·비교 판정기·결과 해시도 변경하지 않는다.

## 사용

```bash
python tools/render_btc_research_summary.py \
  --run /path/to/study/day-baseline \
  --tape /path/to/study/day-baseline/runtime.tape.gz \
  --study /path/to/study \
  --frozen-report /path/to/existing-frozen/report.json
```

Markdown을 표준 출력으로 내보낸다. 필요하면 별도 새 파일에 저장한다.
`--run`만 필수다. 제공하지 않은 근거는 `미확인`으로 표시하며 0·통과로 채우지 않는다.
`--tape`는 기존 economic_header/schema4 JSONL 또는 gzip 테이프다. 헤더/계약·행·판단 봉인 해시를 확인한다.
합성 기능 검사·과거 AI 연구·저장 응답 재생은 실행 종류로 구별한다.

`--study`가 있으면 다음 기존 파일만 사용한다. 다른 디렉터리 배치를 추측하거나 새 장부를 만들지 않는다.

- `registry.json` + `http-guard/`: `CallGuard.inspect_existing`의 읽기 전용 검증으로 연구 전체 HTTP와 미확인 시도를 표시한다. 생성자는 호출하지 않는다. 진행 중 장부를 중단하거나 봉인하지 않는다.
- `cache-certificate.json` + `cache-used.json`: 원 테이프 파일 해시·완료 응답 수·사용된 요청 해시·응답/usage 일치를 대조한다. `tape_path`는 절대 경로 또는 study 상대 경로다.
- 과거 응답 미보존은 인증서의 `missing_response_attempts`가 있을 때만 표시한다.

연구 장부 전체 호출 수는 개별 실행의 호출 수가 아니다. 개별 실행 HTTP는 원본
`execution_metadata.actual_model_calls`가 있을 때만 표시한다. 메타데이터가 모르면
장부 전체 값을 개별 실행 값으로 대신 넣지 않는다. 재사용 기록 대조도 새로운 HTTP 호출이 아니다.
합성 응답이나 frozen 실행의 저장 응답을 새 AI 판단으로 보고하지 않는다.

## 표시 계약

- 계획된 시점, 실제 테이프 판단 수, 단계 수, 텍스트 응답·생성단계 오류·응답 누락을 구별한다. 생성 오류가 곧 실제 서비스 HTTP 오류라는 뜻은 아니다.
- 체결 기록은 `execution_inventory`의 `execType=Trade`를 execId로 중복 제거한다. 같은 ID의 상충 기록은 거절한다. 원장 없이는 종료 거래 수로 체결 수를 추측하지 않는다.
- 종료 거래·승·패·손익0, 이긴 거래 평균 수익률·진 거래 평균 손실률·손익비, 비용 후 순손익·수수료·펀딩·최대낙폭을 나눠 표시한다. 수익률 평균은 원본 `closed_trade_detail.net_return_on_initial_notional`이 완전할 때만 계산한다.
- 미청산 수량, 미완료 주문 계획, 미확인 거래소 주문, 미종료 시나리오를 구별한다. 수량0만으로 정산 완료라고 하지 않는다.
- `--frozen-report`는 이미 존재하는 frozen/HTTP0 보고서다. 계약·테이프·거래소·최종 상태·시계·실행 원장·결과 해시와 손익, 있으면 개별 거래·잔고 내역을 비교한다. 불일치는 `FAILED`, 근거 누락은 `MISSING`이다.
- `MATCH_REPORTED_HASHES`는 제공된 보고서의 일치다. 도구가 독립 재생을 새로 실행하거나 원 거래소 원장을 재검증했다는 뜻이 아니다. 원 실행의 `STOPPED`도 그대로 표시한다.
- 원본·장부·토큰·프롬프트를 수정하거나 출력하지 않는다. 잘못된 입력·봉인 불일치는 오류로 종료하며 민감한 입력 대신 제한된 오류 코드만 출력한다.

## 검증 기준

단계 수/응답 수/HTTP/재사용을 섞지 않는 정상 사례, 누락과 0, active 장부의 읽기 전용 유지,
캐시 변조·다른 실행 테이프·보고서 해시/손익 불일치·일부 결과 누락, 미청산/미종료,
합성/frozen 구분, 잘못된 수치와 체결 ID 충돌, 독립 CLI 실행과 입력 파일 불변을 확인한다.

모의 연구 표이며 실계좌 수익이나 M5 승인표가 아니다. 수익성 증명과 운영 승격은 기존
`analysis/profitability_gate.py`·입증 계약을 따른다. 외부 저장소에서 참고한 것은
실제/합성/미도달 결과를 분리해 설명하는 방식이며 외부 코드나 미국 주식 5분봉 규칙은 도입하지 않았다.
