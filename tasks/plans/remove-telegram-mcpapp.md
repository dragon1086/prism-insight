# Telegram report_generator MCPApp 제거 계획

## 목표

`telegram_ai_bot.py`의 평가·후속질문·저널·Firecrawl 응답 경로에서
`mcp-agent`의 전역 `MCPApp`, `Agent`, `AnthropicAugmentedLLM`, `RequestParams` 런타임을 제거한다.
이미 운영 검증된 `cores.llm` 포트와 `OpenAIAgentsBackend`를 재사용한다.

## 보존할 동작

- 7개 공개 async 함수의 인자와 반환형
- 함수별 프롬프트, MCP 서버 목록, 최대 토큰
- `clean_model_response()` 후처리
- 예외 발생 시 기존 사용자용 오류 문자열
- Telegram 봇의 command 등록, polling, background worker 수명주기
- `report_generator.py`의 동기식 KR/US 보고서 생성 경로

## 변경 순서

1. source/behavior contract 테스트로 현재 7개 함수와 서버·토큰 설정을 고정한다.
2. `report_generator.py`에 SDK-neutral 실행 helper를 추가한다.
3. 7개 함수의 전역 MCPApp/Agent/LLM 결합을 helper 호출로 치환한다.
4. `telegram_ai_bot.py`의 시작·종료 MCPApp 수명주기 코드를 삭제한다.
5. 대상 런타임 파일에 `mcp_agent`/`MCPApp` 잔재가 없는지 테스트한다.
6. 단위 테스트, import-without-mcp-agent, compile, Ruff, diff check를 실행한다.
7. 격리 app-server 환경에서 Telegram import와 실제 Terra+MCP smoke를 통과한 뒤 PR을 연다.

## 모델 및 롤백

- 기본: `TELEGRAM_ANALYSIS_MODEL=gpt-5.6-terra`
- 기본 effort: `TELEGRAM_ANALYSIS_EFFORT=medium`
- 운영 조정은 두 환경변수로 수행하며 코드 롤백 없이 모델을 바꿀 수 있게 한다.

## 제외 범위

- `cores/llm/backends/mcp_agent_backend.py` 및 archive 전용 코드의 최종 삭제
- legacy YAML credential의 환경변수 이전
- 프롬프트 문구 개선이나 응답 형식 개편
