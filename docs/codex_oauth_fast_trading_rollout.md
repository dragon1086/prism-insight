# Codex OAuth Fast 매매 에이전트 단계적 전환 계획

작성일: 2026-08-22
목표: API key 과금 없이 ChatGPT OAuth Codex Fast를 US에서 먼저 검증하고,
검증 후 KR 매수·KR/US 매도 판단까지 확대한다.

## 1. 인증·표면 구분

- 기존 PRISM 경로: mcp-agent → 일반 OpenAI SDK → ChatGPT OAuth proxy.
  `service_tier=priority`를 전달해도 실제 응답은 `default`였다.
- 파일럿 경로: Codex CLI/SDK → 저장된 ChatGPT 로그인 → Codex Fast.
- Codex Fast 설정:

```toml
service_tier = "fast"

[features]
fast_mode = true
```

- GPT-5.6 Fast는 Standard보다 많은 ChatGPT 크레딧을 사용한다. API key 과금과는
  별개다.

## 2. Phase 0 결과: 로컬 가용성

로컬 `codex-cli 0.144.1`, ChatGPT 로그인 상태에서 비대화식 실행 성공.

- 짧은 동일 프롬프트:
  - Fast 5.22초
  - Standard 6.16초
- production 프롬프트 + 실제 종결 거래 보고서:
  - 손실 사례 산일전기 -16.31%:
    - Fast: 39.39초, `미진입`, outcome +1, JSON 정상
    - Standard: 69.10초, `진입`, outcome -1, JSON 정상
  - 수익 사례 SK하이닉스 +12.48%:
    - Fast: 42.99초, `진입`, outcome +1, JSON 정상
- Fast 2건 합산: outcome +2/2, parse 2/2.

해석 제한: 표본 2건이며 Standard 비교는 1건뿐이다. 품질 우위 일반화 금지.

## 3. Phase 1: 12건 오프라인 평가

기존 `tasks/eval/trading_ground_truth.json`의 8개 손실·4개 수익을 전부 평가한다.

통과 기준:

1. JSON parse 12/12.
2. 기존 API `gpt-5.6-sol/high` net outcome +4 이상.
3. 손실 회피 5/8 이상, 수익 포착 3/4 이상.
4. p50 지연 55초 이하.
5. timeout/CLI 오류 0건.

Fast 크레딧 사용량을 먼저 계산·표시하고 실행한다.

## 4. Phase 2: US 매수 시나리오 동등성 검증

- 기존 mcp-agent 결과가 주문의 유일한 권위값이다.
- Codex Fast는 기존 에이전트와 같은 MCP 서버·읽기 도구를 사용한다.
- Codex timeout 90초, 동시성 1, 실패 시 기존 경로 유지.
- 매수·매도 모두 실제 MCP 호출이 1건 이상 완료되어야 성공으로 인정한다.
- SQLite는 `list_tables`, `describe_table`, `read_query`만 노출한다.
- 셸·파일·내장 웹 검색은 금지하고 MCP 도구만 허용한다.

승격 기준:

- parse 100%
- 기존 대비 손실 회피 악화 없음
- 수익 포착 악화 없음
- p50 지연 55초 이하
- 결정 불일치 사례 사람 검토 완료

## 5. Phase 3: US 매수 운영

- Codex Fast를 1차 경로로 사용.
- 기존 mcp-agent를 timeout/parse/CLI 실패 fallback으로 유지.
- DB·주문·메시지 반영은 기존 순차 경로에서만 수행.
- 10거래일 안정화 전에는 US 매도 판단으로 확대하지 않는다.

## 6. Phase 4: KR 매수 shadow → 운영

- US와 동일한 adapter·timeout·원장·fallback을 재사용한다.
- KR 전용 production 시스템 프롬프트와 KRX sector constraint만 교체한다.
- 기존 12건 realized-PnL 평가를 회귀 기준으로 유지한다.

## 7. Phase 5: KR/US 매도 판단

- 가장 마지막에 적용한다. 매도 판단은 보유 포지션과 실제 주문에 직접 영향을 준다.
- Codex는 판단만 수행하고, broker/SQLite 적용은 반드시 기존 순차 chokepoint가 담당한다.
- shadow에서 기존 결정과 불일치한 모든 `SELL`을 사람이 검토한 뒤 승격한다.

## 8. 서버 준비 조건

필요 조건과 현재 상태:

1. 공식 Codex CLI 설치: 완료 (`/opt/prism-codex`, 0.144.1).
2. 서버 전용 ChatGPT-managed Codex 로그인: 완료.
3. `auth.json`·설정은 0600 권한·비추적 유지: 완료.
4. Fast 설정을 `/root/.codex-prism`에 격리: 완료.
5. 매매 adapter는 read-only sandbox와 ephemeral thread만 사용: 완료.
6. KR/US 전용 MCP 프로필로 시장 도구를 격리: 완료.

운영 primary 활성화는 MCP 도구와 판단 결과의 동등성 검증 뒤에만 한다.

## 9. 평가 기록 보존

초기 평가 하네스는 Git에 포함되지 않은 거래 표본과 로컬 보고서 아카이브에
의존하는 일회성 도구였으므로 저장소에 포함하지 않는다. 검증된 기준과 결과는
이 문서와 agentmemory에 보존하며, 운영 backend의 명령·격리·MCP 호출 계약은
`tests/test_codex_oauth_fast_backend.py`에서 회귀 검증한다.

## 10. 12건 전체 평가 결과

사용자 승인에 따라 동시성 2로 전체 12건을 실행했다.

- JSON parse: 12/12
- net outcome score: +4
- 손실 회피: 5/8
- 수익 포착: 3/4
- p50 latency: 48.11초
- CLI/timeout 오류: 0
- 사전등록 게이트: 전부 통과

기존 direct API `gpt-5.6-sol/high` 결과(+4, 5/8, 3/4, p50 71.8초)와
판단 품질이 같고 p50은 약 33% 짧았다.

## 11. 운영 전환 시도와 즉시 롤백

Rocky가 로컬 검증 통과 후 shadow를 생략하고 운영에 직접 적용하도록 승인했다.

### 서버 Codex 격리

- CLI: `/opt/prism-codex`, codex-cli 0.144.1
- CODEX_HOME: `/root/.codex-prism`
- 로컬 Mac 인증 파일을 복사하지 않고 서버 별도 device login
- 서버와 로컬 모두 `Logged in using ChatGPT` 확인
- `auth.json`·`config.toml` 권한 0600
- 서버 Fast 최소 호출 6.29초, production backend JSON 호출 7.97초

초기 무도구 파일럿은 사용자의 직접 운영 승인에 따라 KR/US 매수 primary로 잠시
설정했으나, 기존 매수 에이전트도 MCP를 실제 사용한다는 점을 재확인한 직후 4개
크론의 활성화 플래그를 모두 제거했다.

### 롤백 직후 상태

- KR/US 매수·매도: 기존 mcp-agent가 계속 primary
- Codex Fast: 코드와 서버 프로필만 배치된 비활성 상태
- timeout: 90초
- 향후 활성화 시 Codex CLI/인증/timeout/MCP/parse 실패는 자동 fallback
- US/한국의 DB·주문·메시지 순차 처리 불변

### cron 환경

- `PRISM_US_CODEX_FAST_TRADING`, `PRISM_KR_CODEX_FAST_TRADING`: 미설정
- `PRISM_US_CODEX_FAST_SELL`, `PRISM_KR_CODEX_FAST_SELL`: 미설정
- 따라서 롤백 직후 정규 배치는 Codex 경로를 실행하지 않았다.

## 12. MCP 동등성 구현 및 서버 스모크

전용 프로필:

- `kr_trading.config.toml`: `time`, `kospi_kosdaq`, 읽기 전용 `sqlite`, `perplexity`
- `us_trading.config.toml`: `time`, `yahoo_finance`, 읽기 전용 `sqlite`, `perplexity`
- SQLite 쓰기 도구 `write_query`, `create_table`, `append_insight`는 미노출

백엔드는 JSONL의 `mcp_tool_call`을 파싱하고, 완료된 MCP 호출이 0건이면 실패로
처리해 기존 mcp-agent로 폴백한다. KR/US 매수와 매도에 같은 규칙을 적용했다.

db-server 읽기 전용 스모크 결과:

- KR: `time-get_current_time`, `kospi_kosdaq-get_index_ohlcv`,
  `sqlite-list_tables` 성공 (16.30초)
- US: `time-get_current_time`, `yahoo_finance-get_historical_stock_prices`,
  `sqlite-list_tables` 성공 (18.49초)
- 공통: `perplexity-perplexity_ask` 성공 (15.96초)
- 스모크 중 주문·DB 수정·텔레그램 전송 없음

실제 매매 프롬프트 메서드 단독 프로브:

- US 매수(HOOD 최신 보고서): 57.66초, MCP 6회, JSON 성공
- US 매도(PYPL 실제 보유 입력): 67.49초, MCP 9회, `보유`
- KR 매수(삼성전자 최신 보고서): 79.69초, MCP 6회, JSON 성공
- KR 매도(메모리 DB 가상 보유 입력): 46.16초, MCP 9회, `보유`
- Codex 바이너리 강제 실패: 기존 US mcp-agent가 자동 기동해 70.68초에
  MCP 조회와 JSON 반환, cleanup까지 성공
- 모든 프로브는 시나리오/판단 메서드까지만 호출했다. 주문·프로덕션 DB 쓰기·
  텔레그램 전송은 실행하지 않았다.

## 13. 최종 운영 활성화

위 검증 후 2026-08-22 03:32 KST에 KR/US morning·afternoon 4개 크론을
다음 설정으로 활성화했다.

- `PRISM_KR_CODEX_FAST_TRADING=1`, `PRISM_KR_CODEX_FAST_SELL=1`
- `PRISM_US_CODEX_FAST_TRADING=1`, `PRISM_US_CODEX_FAST_SELL=1`
- `PRISM_CODEX_HOME=/root/.codex-prism`
- `PRISM_CODEX_BIN=/opt/prism-codex/node_modules/.bin/codex`
- `PRISM_CODEX_FAST_TIMEOUT=120`

활성화 당시 US afternoon 배치는 이미 14:30 EDT에 변경 전 환경으로 시작한
상태였다. 중복 메시지·중간 작업 손실을 피하려고 재시작하지 않았으며, 그 실행은
기존 mcp-agent로 완료한다. 새 설정은 다음 정규 배치부터 적용된다.

## 14. 남은 단계

1. 기존 mcp-agent와 Codex가 같은 고정 MCP 응답을 받는 replay 평가를 만든다.
2. 매수 12건과 매도 realized-outcome 평가에서 JSON·판단·필수 도구 호출을 비교한다.
3. 첫 정규 배치에서 `[CODEX_FAST]` latency·MCP 호출·fallback 로그를 확인한다.
4. 나머지 production 직접 참조를 제거한 뒤 mcp-agent dependency/config를 삭제한다.

## 15. US 매수 후보 병렬 pre-pass

2026-08-22 US afternoon 회고에서 보고서 생성 3건은 이미 병렬이었지만, 추적 단계의
매수 시나리오 3건은 직렬로 실행돼 약 9분이 걸렸다. KR에 있던 구조를 US에도
포팅했다.

- `US_TRADING_ANALYSIS_CONCURRENCY=2` (최대 4)
- 공유 SQLite 조회만 `asyncio.Lock`으로 직렬화
- Codex/LLM 구간은 lock을 해제해 동시 실행
- 결과 적용, 보유 확인, 매수 주문은 기존 순서대로 직렬 유지
- Codex 실패 시 legacy MCPApp fallback은 별도 lock으로 직렬화

검증:

- 관련 회귀 테스트 25개 통과, 알려진 기존 score-6 테스트 1개 제외
- 서버 Codex+MCP 2개 동시 스모크: 각 21.97초/21.10초, 전체 21.97초
- US morning/afternoon 크론에 동시성 2 명시

매도 판단은 DB 갱신·주문 안전장치가 한 메서드에 함께 있어 즉시 병렬화하지 않았다.
향후 `판단 준비 → LLM 판단 병렬 → 실행 순차`의 3단계로 분리한 뒤 적용한다.
