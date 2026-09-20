# S2c-3 결과: 부분 주석의 외부 부모 범위 근거

## 구현과 안전 경계

`note_fragment_blocks`는 전체 main의 strict graph와 해시, 발행사/receipt, 표지·재무제표·부모·자식의 같은 문서 tuple, 자식 제목 및 모든 인식된 제목 이벤트를 확인한 경우에만 부모 scope를 부여합니다. HTML·section_path·표 값·원문 위치는 바꾸지 않고 별도 `scope_context`로 외부 문맥의 출처를 보존합니다.

제목 이벤트는 새 private opt-in에서만 최대2,000개 수집합니다. 기본 parser와 일반 section_blocks의 동작은 그대로입니다. 정확한 금융 제목 모양의 plain prose도 보수적으로 거절하지만, 모든 비표준 제목·표 내부 범위 전환을 탐지한다고 주장하지 않습니다.

설계 리뷰에서 내부 반대 범위, 마지막 무내용 전환, 다른 dcmNo의 cover/financial 연결, 접두어·접미어 충돌을 먼저 보완했습니다. 코드 리뷰에서는 빈 query를 버려 정확한 URL 검증을 우회하는 반례를 RED로 고정해 수정했습니다. 검증 규칙을 끄거나 원문 앞에 부모 제목을 붙이지 않았습니다.

## 같은 실제 원문의 결과

추가 HTTP/모델 호출 없이 기존 private 원문만 사용했습니다. 먼저 보존된 cover와 financial_statements의 hash·기간·연결 범위를 재검증하고, C2에서 취득한 동일 child 원문을 새 API로 처리했습니다.

- `11213317:47`: 742,428바이트, SHA `03350ac2ddcb5728a93e25a1eb35552decd5df785b1f2eba667ec2ba701d24e4`. 기존 unknown75개가 consolidated75개로 검증됐고 보고서 후보35개를 만들었습니다.
- `11213317:78`: 9,130바이트, SHA `fba2d80cb1375ecacf25018506366da0a8f191d12609f7d3b12b22eb17c4113d`. 기존 unknown6개가 consolidated6개로 검증됐고 후보3개를 만들었습니다.
- 총81개 record는 scope와 새 scope_context 외 모든 필드가 동일합니다. 원문 위치11,125개/76개 및 자식 제목 위치를 대조해 오류0건입니다.
- 두 fragment만 넣은 격리 packet에서는 news_analysis3개/5,696바이트, company_status1개/5,445바이트를 전달했습니다. company_overview는 근거0개/897바이트입니다. 전달된 record의 scope_context를 JSON·공유 provenance 복원 후에도 보존했습니다.

이는 **부분 문서 어댑터와 packet 경계**의 검증입니다. 실제 collector 전체 실행·새 보고서 생성·금융 사실 gold 검증이 아닙니다. 54개 전체 주석이나 기본/연간 전 내용을 읽었다는 뜻도 아니며 PARTIAL_COVERAGE를 유지합니다.

## 회귀와 변경 파일

- 신규159개, 최종 연구 입력 CI 목록1,836개 통과(기존 의존성 경고4개).
- 실제 KR/US provider shape 회귀46개 별도 통과.
- 기존 고정 대형 HTML3개는 record/최종 전달 수를 유지했고 원문 위치와 전달 provenance 오류0건입니다.
- 독립 코드 리뷰 APPROVE, Ruff·구문·diff 통과. Python 전용 타입 검사 완료 주장은 하지 않습니다.
- 변경: `dart_report_evidence.py`의 공유 admission/변환과 명시적 fragment API, `filing_html.py`의 opt-in heading 이벤트, `filing_report_evidence.py`의 scope_context 보존, 신규 회귀와 CI.

기존 검증·변환 경로를 재사용했습니다. 운영 collector는 아직 새 fragment API를 호출하지 않으므로 전체 대형 주석 수집 복구라고 표시하지 않습니다. 다음 단계는 기존 호출/바이트/시간 예산 안에서의 실제 부분 수집 연결과 누락 표시입니다. 이후 단위·조건 연결, 필수 근거의 최종 예산 검증, 미국·실제 모델·BUY/SELL 게이트가 남았습니다. 머지·배포하지 않았습니다.
