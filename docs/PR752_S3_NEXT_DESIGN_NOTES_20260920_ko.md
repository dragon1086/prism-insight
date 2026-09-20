# S3 후속 설계 입력: 최종 예산 안에서 조건을 보존하는 선정

상태: 원문 독립 gold와 실패 원인을 확인한 설계 입력입니다. **아직 구현 승인 설계가 아니며 우선순위·투영 변경을 적용하지 않았습니다.** 새 구현 전에 구체 PRD/test spec과 critic 검토가 필요합니다.

## 확인된 기준선 원인

독립 architect가 `bcdd348b` 원문 SHA를 확인했습니다. 삼성생명 기본 주석 record 670개에서 block 341개, news risk block 82개를 생성했습니다. 필수 표 781은 news index 42/7,957바이트, 표 1018은 index 67/1,229바이트입니다. `packet()`의 source/topic별 앞 24개 제한으로 둘 다 byte-fit 판단 전에 빠졌습니다. 첫 8개는 회계정책이 아니라 담보제공자산의 caption/data/비교기간 반복입니다.

HTML의 `material_html_records()`는 prose 묶음과 제한만 처리하고 Markdown의 정책 절 제외·specificity·note-family 다양화·topic round-robin을 사용하지 않습니다. `_record_blocks()`는 topic tag가 있으면 classify score가 낮아도 통과시키며 입력 순서를 유지합니다. S2c-5가 layout-only를 제외하므로 이 기준선 index는 다시 측정해야 합니다.

## 검토할 최소 방향

- 전체 HTML 후보를 본 뒤 source/topic 제한 전에 일반 retrieval 순서를 정합니다. 투자 위험 점수나 매매 가점으로 노출하지 않습니다.
- 정책 절보다 구체 공시, 명시적인 기간·단위·대상과 완전한 조건 묶음, note-family 다양성을 검토합니다. 승소·해소·환입도 진행·불확실성과 동등하게 보존합니다.
- 명시적인 원문 기간만 사용하고 primary/annual 역할을 유지합니다. annual을 반드시 하나 넣거나 전부 빼는 규칙은 금지합니다.
- COMPLETE table만, 모든 행과 원래 row-label/header를 유지한 채 제한된 data 열 선택을 검토합니다. 모든 조건 셀을 포함하고 행·열 좌표를 다시 매기지 않습니다. 수치·괄호·dash·빈칸을 변환하지 않습니다.
- row-label 경계가 모호하거나 선택 mask가 merged cell을 자르면 투영하지 않습니다. colspan을 줄이거나 셀을 복제하지 않습니다. POSCO의 넓은 다단 header는 이 최소 계약으로 해결되지 않을 수 있으며 별도 실패로 남깁니다.
- 모든 부분집합을 탐색하지 않습니다. 셀을 제한된 횟수로 훑어 column의 명시 topic/조건을 구하고, 동일 owner/topic의 제한된 union만 검토합니다. 회사명·table index·고정 열 번호를 운영 규칙에 넣지 않습니다. 후보 수/CPU/바이트 상한을 구현 전에 고정해야 합니다.

## 손실 없는 표현의 실측 하한

원래 row/col/rowspan/colspan/tag/text의 명시 schema와 tuple 표현을 메모리에서만 측정했습니다. 이는 운영 선정 규칙이나 실제 packet 통과가 아닙니다.

- G1/G2의 같은 표에서 필요한 두 data 열과 원래 row-label 2열, 9행 전체는 29개 원본 셀입니다. 기존 객체 표현 2,792바이트, schema가 있는 tuple 1,958바이트, relative cell XPath까지 넣으면 2,632바이트입니다.
- G3 전체 7×2/14셀은 tuple 900바이트, XPath 포함 1,229바이트입니다.
- 합계 2,858바이트(좌표만) 또는 3,861바이트(XPath 포함)입니다. excerpt JSON escaping, 문맥, filing/provenance, 공지·gap은 아직 포함하지 않았으므로 6,000바이트 성공 근거가 아닙니다.

새 schema에는 고정 version/필드/tuple 길이, bool을 거절하는 정수 좌표, shape/span/중복·겹침·전체 행 검증과 복원 상한이 필요합니다. 기존 `expand_filing_record()`는 이 검증을 하지 않습니다. 구현한다면 원본 셀과 compact→expand가 완전히 같아야 합니다. 기존 filing/provenance 공유·DOM prefix factoring을 재사용하고 envelope를 겹겹이 추가하지 않습니다.

## 필수 검증

먼저 `PR752_S3_SAMSUNG_LIFE_GOLD_20260920_ko.md`의 G1~G3를 고정한 그대로 사용합니다. 최신 승소·의무 소멸, 추정 충당부채의 불확실성, 별개 자회사의 소송 주체·6건·금액을 누락하거나 혼합하면 실패입니다. 필수 gold를 사후에 선택 항목으로 바꾸지 않습니다.

실제 packet 전체 UTF-8 6,000바이트, 원문 셀/단위/조건/XPath, 최신과 과거의 구분, 다른 업종의 header/span/각주 반례를 검증합니다. 이 개발 원문을 새 검증군으로 세지 않으며 별도 holdout을 먼저 고정해야 합니다. 모델/Markdown/PDF/BUY/SELL 단계는 이 입력 검증 뒤에 진행합니다.
