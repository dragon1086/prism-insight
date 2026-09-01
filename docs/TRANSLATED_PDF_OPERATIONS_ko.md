# 번역 PDF 운영 계약

KR·US 원문 보고서와 매매 처리가 끝난 뒤 다국어 PDF를 비동기 후속 작업으로 생성하고 전송한다. 번역 PDF 실패는 주문·포지션·원문 보고서 완료 상태를 변경하지 않는다.

## 기본 정책

- 동시 번역: 3건
- 개별 번역 시도 제한: 360초
- timeout 최대 시도: 2회
- 전체 번역 배치 제한: 1,800초
- PDF 번역 추론 강도: `low`

환경변수로 각각 `PRISM_TRANSLATED_PDF_MAX_CONCURRENCY`, `PRISM_TRANSLATED_PDF_ITEM_TIMEOUT_SECONDS`, `PRISM_TRANSLATED_PDF_MAX_ATTEMPTS`, `PRISM_TRANSLATED_PDF_BATCH_TIMEOUT_SECONDS`를 조정할 수 있다.

개별 timeout만 같은 번역을 한 번 더 시도한다. 일반 번역 오류와 쿼터 오류는 반복 호출하지 않고 기존 오류·알림 경로로 보낸다. 전체 배치 제한에 도달하면 남은 작업을 취소한다.

## 로그 판정

- `status=retry reason=item_timeout`: 복구 시도 중인 경고다.
- 이후 같은 시장·언어·보고서에 `status=translated`와 `status=sent`가 있으면 복구 성공이다.
- 최종 `status=skipped reason=item_timeout attempts=2`: 실제 산출물 누락이다.
- `status=deadline_exceeded`: 배치 상한에 도달한 실제 장애다.
- 성공 로그의 `queue_seconds`와 `request_seconds`를 분리해 대기열 포화와 LLM 지연을 구분한다.

원시 오류 줄 수를 장애 건수로 사용하지 않는다. 시장·언어·원본 보고서를 키로 묶고 최종 상태로 판정한다.

## 2026-09-01 KR 장애 기록

최근 4개 KR 배치에서 일본어 번역은 12건 중 7건이 기존 360초 단일 시도 제한을 넘었다. 2026-09-01 오후 배치에서는 SK이노베이션과 아난티 일본어 PDF가 누락됐지만 다른 10개 번역 PDF와 원문 보고서·매매·tracking은 정상 완료됐다.

번역 전용 요청이 공용 `medium` 추론 강도를 사용했고 timeout 재시도가 없던 것이 직접적인 취약점이었다. PDF 번역만 `low`로 낮추고 timeout 재시도 1회를 추가했으며, 재시도를 수용하도록 전체 상한을 1,800초로 늘렸다.
