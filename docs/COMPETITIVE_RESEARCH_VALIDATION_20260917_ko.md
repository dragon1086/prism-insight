# 경쟁사 조사 검증 1차 결과와 prefetch 개선 설계

## 결론과 완료 범위

현재 Perplexity+Firecrawl을 신뢰 가능한 완성형 경쟁사 조사로 승인할 근거는 부족하다.
유효한 개별 자료도 찾지만 비교를 완성하지 못하는 사례, 주장과 인용 URL의 불일치,
HTTP 200이지만 공시 본문이 없는 사례를 실제로 확인했다.
Perplexity 전체가 무효라는 뜻은 아니며, TradingView가 더 정확하다는 비교도 하지 않았다.

완료: 운영 보고서 8개 감사, 운영과 같은 버전의 MCP 직접 호출, 공개 원문 대조,
prefetch 입력 검증 코어와 회귀 테스트. 미완료: 전면 자동 수집·캐시·운영 연결,
동일 조건 보고서 A/B 비교, 실제 토큰/달러 비용 절감 입증.
운영 프롬프트·매매·cron·자격증명은 변경하지 않았다.

## 표본과 판정 방법

운영 db-server의 기존 원본 한국어 보고서 중 시장별 수정시각 기준 최근 연속 4개를 선택했다.
좋은 사례만 고르지 않았고 번역본과 기업개요에 복사된 동일 근거를 별도 표본으로 세지 않았다.
코드 기준은 배포 commit 03021932(PR #745)와 동일한 22d49a26 트리다.

- KR: 9/17 오전 지엔씨에너지·RF머트리얼즈, 오후 오스코텍·인제니아테라퓨틱스.
- US: 9/15 오후 LYB·TMO, 9/16 오후 LITE·GEV.
- 원본 파일명·SHA256·Competitive Evidence 구획을 별도 감사 JSON에 보존했다.
- 8/8에 근거 기록이 있었다. 7/8은 개별 항목에 SOURCE_CHECKED를 사용했다.
- 동일 기간의 경쟁사 주가 비교가 완성된 구획은 0/8이었다.
- 직접 비교기업 2~3개와 동일 기간·정의·단위의 사업 지표 비교를 완성한 구획도 0/8이었다.
  TMO는 DHR와 숫자를 일부 비교했으나 정의·사업 구성 차이를 INCOMPARABLE로 남겼다.
- 이 기준의 미달은 해당 기업의 경쟁력이 없다는 뜻도, 공개 자료가 존재하지 않는다는 뜻도 아니다.
  좁은 표본의 조사 산출물 점검이며 투자 성과 평가가 아니다.

## 실제 확인한 문제

### 1. 검색 답변과 인용 URL의 연결 오류

운영에 고정된 @perplexity-ai/mcp-server@1.2.0의 perplexity_ask를 Python에서 직접 호출했다.
현재 기준 조사이며 과거 실행을 동일하게 재현한 것은 아니다. 보고서 생성 LLM은 호출하지 않았다.

- LITE 조사: 11.624초, 응답 5,298자.
- RF머트리얼즈 조사: 11.744초, 응답 3,298자.

LITE 응답은 Coherent 연간 매출에 [3]을 붙였지만 [3] URL은 Lumentum 기사였다.
AAOI 데이터센터 매출에 붙인 [5] URL도 Coherent 기사였다. firecrawl-mcp@3.23.6으로
두 URL을 실제 열어 제목·본문을 확인했다. 숫자의 참거짓과 별개로 해당 인용 연결은 부적절했다.

같은 MCP 패키지의 formatter에는 원래 검색 ID가 없거나 모호할 때 URL을 위치 순서로 다시
번호 매기되 답변 본문은 고치지 않는 fallback이 있었다. 이는 구조적 위험이다.
이번 답변이 그 fallback 때문에 잘못됐는지는 원 API 응답이 없어 확정하지 않는다.
답변 생성 모델의 문제와 MCP 가공 문제를 분리해서 검증해야 한다.

### 2. Firecrawl 성공과 본문 확보는 다르다

RF머트리얼즈 KIND 사업보고서 URL은 HTTP 200·도구 오류 없음으로 반환됐지만,
294자의 로딩 안내와 최종 정정문서 확인 문구뿐이었다. 사업 본문은 없었다.
이 응답을 원문 확인 성공으로 계산하면 안 된다.

반대로 Coherent 공식 실적 발표는 2,340자의 유효한 본문을 반환했다.
분기 매출 등 공개 수치를 원문에서 확인할 수 있었다. 즉 Firecrawl 자체가 무효한 것은 아니다.

### 3. 추정치/실적 구분과 과거 증거 한계

LITE 보고서의 산업시장 근거는 2025년 값을 actual로 표시했다. 현재 해당 LightCounting 원문은
2025년 시장 규모도 추정이라고 설명한다. 수치뿐 아니라 actual/estimate 구분도 확인해야 한다.
GEV의 주문·백로그와 인제니아의 회사 파이프라인 소개는 현재 원문과 대응되는 내용이 있었다.
다만 현재 재조회는 당시 그 원문을 실제 읽었다는 증거가 아니며, 회사 소개는 경쟁사 대비 우위의 증명도 아니다.

## 코드에서 확인한 원인

- 뉴스 조사 최대2회·추가 원문2개는 프롬프트 지시이며 실행 quota가 아니다.
- SOURCE_CHECKED는 모델 자기평가이고, 근거 ID는 내용 해시일 뿐 사실 인증이 아니다.
- 경쟁근거 prefetch가 주입되면 검색을 생략한다는 문구는 있으나, 현재 KR 뉴스 호출에는
  경쟁자료 인자가 없고 US도 소셜 자료만 추가로 받는다. 실제 경쟁조사 prefetch 경로는 없다.
- 보고서 호출부는 backend 결과의 text만 반환한다. 현재 경로의 로그만으로 당시 도구 질문·
  원문·경쟁조사 전용 토큰 비용을 재구성할 수 없다.
- 뉴스 근거를 기업개요에 다시 복사하므로 downstream 입력에 중복되는 여지도 있다.
- 기존 59개 관련 테스트는 프롬프트·전달·PDF 생존을 검증한다. 외부 조사 정답률 검사는 아니다.

## 대안 실험: 답변 생성보다 자료 발견을 먼저

같은 운영 Perplexity MCP의 perplexity_search를 사용해 공식 IR 도메인으로 좁힌 검색을
1회 실행했다. 1.055초에 5개 결과를 받았고 Lumentum과 Coherent의 정확한 해당 분기
공식 발표 URL을 확보했다. 이는 생성형 ask 응답과 조건이 다른 탐색 실험이지 성능 A/B가 아니다.

새로 찾은 Lumentum 공식 발표도 Firecrawl로 읽었다(3.056초, 46,119자).
대상 기간·발표일·GAAP/비GAAP 구분이 있는 자료를 확보했다. 이 자료와 Coherent 자료로
비교표의 기초를 만들 수 있지만, 두 회사의 사업 구성이 달라 수치만으로 지배력을 판정하지 않는다.
현재까지 직접 MCP 유료 작업은 ask 2회 + search 1회 + scrape 5회다.
모든 작업을 LLM 도구 선택 루프 없이 실행했지만 ask 자체의 모델 비용과 검색·스크래핑 요금은 존재한다.
응답에 비용/usage가 노출되지 않아 실제 토큰·달러 비용은 UNKNOWN이다.

권장 수집 순서:
1. 코드로 주체·사업 범위·질문·기준시점을 고정한다.
2. 가격 상대강도는 검증된 가격자료로 계산한다. 뉴스 검색에 수익률 계산을 맡기지 않는다.
3. 구조화된 검색으로 출처 후보를 모으고, 회사별 공식 IR·공시를 우선한다.
4. 공개 원문을 한 번 확보해 source ID·hash·조회/발표시각과 묶는다.
5. 법인·기간·정의·단위·실적/전망을 맞춘다. 출처 한 장만으로 여러 경쟁사 검증을 했다고 하지 않는다.
6. 짧은 근거와 공백 목록을 기존 뉴스·기업분석 에이전트가 공유한다.
7. 중요한 미해결 질문에만 제한된 추가 ask/research를 허용한다.

Search API는 답변 생성 대신 순위가 있는 검색 결과를 반환한다. 수집 단계에서 이를 사용하면
별도 생성형 요약을 반복하는 구조를 줄일 수 있다. 실제 총비용 절감은 후속 측정해야 한다.

## 이번에 구현하고 검증한 것

- prism_core/competitive_prefetch.py: 외부 호출 없는 오프라인 입력 패킷 생성기.
- tests/test_competitive_prefetch.py: 신규 26개 회귀 테스트.
- 기존 관련 테스트를 합쳐 85개 통과. Ruff·컴파일·diff 검사 통과.

원문 불일치·로딩 화면·미래 날짜·중복 ID·입력 예산 초과를 명시적으로 분리한다.
모델이 붙인 SOURCE_CHECKED는 신뢰하지 않는다. 원문 문구 일치도 사실·회사·기간·수치의
타당성 검증과 구분하고, 원 주장 값은 unverified_claim으로 분리한다.
모델 입력에는 출처·시점과 누락된 주장 ID·이유도 포함한다.

실제 응답에서 선정한 4개 확인 항목으로 오프라인 시험했다. 인용 불일치 2개와 KIND 로딩 화면은
검증된 입력으로 보내지 않았고, Coherent 원문에 실제 있는 문장 1개만 텍스트 대응 항목으로 보냈다.
입력은 1,638자였다. 사람이 선택한 제한된 사례이며 전면 자동 추출·사실판별이나 비용 절감을
증명한 것은 아니다. 다른 회사의 문장을 그대로 인용하는 오류 등은 의미 검토가 여전히 필요하다.
실제 공유 캐시·네트워크 예산·운영 주입은 아직 구현하지 않았다.

## 다음 통과 조건

- KR2·US2 파일럿 후 각각6개로 확장하며 지주회사·다각화·소형/자료부족 사례를 포함한다.
- 같은 질문·시점·보고서 모델에서 현행 조사와 prefetch 입력을 비교한다.
- 없는 URL/인용·미래정보·거짓 원문확인은 0건이어야 한다.
- 근거 일치와 질문 해결률이 악화되지 않으면서 cold/warm 실제 호출·토큰·비용·시간을 비교한다.
- 대체 공급자는 남은 공백에만 시험한다. TradingView 가입은 이 검증의 선행 필수조건이 아니다.
- BUY 점수·게이트·주문을 바꾸지 않는다. 이번에 운영 효과가 검증됐다고 보고하지 않는다.

## TradingView 가입과 인증

공식 MCP는 API key 발급 방식이 아니라 TradingView 계정의 OAuth 2.1 로그인이다.
Essential 이상 유료 플랜이 필요하며 체험은 MCP 대상이 아니다. 보고서 자료 범위 검증을 위해
곧바로 상위 플랜이나 모든 실시간 거래소 상품을 살 필요는 없다. 전문 사용자 여부와 별도 데이터
권한·자동 처리·저장·채널 배포 허용 범위는 구독과 별도로 확인한다.

사용자 본인의 Mac 터미널에서:

```bash
codex mcp add tradingview --url https://mcp.tradingview.com/mcp
```

브라우저에서 유료 계정으로 로그인·승인한다. 인증이 완료되지 않았다면:

```bash
codex mcp login tradingview
```

새 Codex 세션에서 읽기 조회를 확인한다. 비밀번호·쿠키·토큰을 Telegram에 보내지 않는다.
Mac의 인증이 운영 서버 배치에 자동 공유되는 것은 아니다. 운영 연결은 별도 안전한 자격증명·
갱신·도구 허용 목록을 검증한 뒤 진행한다. 이번에는 설치·결제·인증·서버 설정을 바꾸지 않았다.

## 참고

- https://www.tradingview.com/mcp/docs
- https://www.tradingview.com/policies/
- https://learn.chatgpt.com/docs/extend/mcp?surface=cli
- https://docs.perplexity.ai/docs/getting-started/integrations/ag2
- https://www.lightcounting.com/newsletter/en/january-2026-optics-for-ai-clusters-366
- https://www.gevernova.com/news/press-releases/ge-vernova-reports-second-quarter-2026-financial-results-raises-2026-financial
- https://www.ingeniatx.com/en/sub/technology/pipeline.php
- https://www.coherent.com/news/press-releases/fourth-quarter-and-fiscal-year-2026-results
- https://investor.lumentum.com/financial-news-releases/news-details/2026/Lumentum-Announces-Fourth-Quarter-and-Full-Fiscal-Year-2026-Results/default.aspx
