# BUY/SELL Codex CLI를 OAuth 프록시 하나로 통일

## 왜

리포트(`cores/chatgpt_proxy`, `chatgpt_auth.json`)와 BUY/SELL(codex CLI, `/root/.codex-prism/auth.json`)이
**로그인 세션을 따로** 가지고 있었다. OAuth refresh 토큰은 1회용(rotation)이라 한 세션을 두 프로그램이
나눠 쓰면 한쪽 갱신 때 다른 쪽이 로그아웃된다. 그래서 계정을 바꿀 때마다 추가 로그인(브라우저/기기 코드)이
필요했고, 실제로 BUY/SELL과 리포트가 서로 다른 계정(munsangrok / dragon1086)을 쓰게 됐다.

## 구조 (2026-09-29부터)

```
codex CLI (BUY/SELL, gpt-6-astra, Fast)
  └─ model_provider = prism_proxy → http://127.0.0.1:18742/v1/codex/responses
       └─ prism-report-oauth-proxy.service (읽기 전용, 매 요청 chatgpt_auth.json 재읽기)
            └─ ChatGPT Codex 백엔드 (계정 = chatgpt_auth.json 하나)
```

- 로그인 파일 1개(`/root/.config/prism-insight/chatgpt_auth.json`), 갱신 주체는 기존과 같다
  (oauth_healthcheck 30분 cron·배치 프록시). 18742는 갱신하지 않는다.
- `/v1/codex/responses`는 SSE를 바이트 그대로 돌려준다. 모델명·`service_tier`(Fast=`priority`)·`include`를
  바꾸지 않는다. 기존 `/v1/responses`(리포트용, JSON 수집·모델 매핑)는 변경 없음.
- **쿼터**: 리포트와 BUY/SELL이 한 계정 한도를 함께 쓴다. `oauth_healthcheck --quota`(3시간)가 이 계정 하나를 본다.

## 서버 설정 (`/root/.codex-prism/config.toml`)

```toml
model = "gpt-5.6-sol"
model_provider = "prism_proxy"
service_tier = "fast"
cli_auth_credentials_store = "file"

[model_providers.prism_proxy]
name = "PRISM OAuth proxy"
base_url = "http://127.0.0.1:18742/v1/codex"
wire_api = "responses"
requires_openai_auth = false

[features]
fast_mode = true
```

기존 codex 전용 로그인(`auth.json`)은 쓰지 않도록 `auth.json.bak-<계정>-<날짜>`로 옮겨 둔다.

## 롤백

1. `config.toml.bak-*`를 `config.toml`로, `auth.json.bak-*`를 `auth.json`으로 되돌린다(codex 전용 로그인 복귀).
2. 코드 롤백은 필요 없다(새 경로를 안 쓰면 영향 없음).

## 계정 변경

이제 `chatgpt_auth.json` 하나만 바꾸면 리포트와 BUY/SELL이 함께 바뀐다
(`python -m cores.chatgpt_proxy.oauth_login --force`).

## 계정별 추론 강도 (2026-09-30)

`PRISM_CODEX_EFFORT_BY_ACCOUNT="dragon1086@naver.com=medium,munsangrok@gmail.com=xhigh"`를 매매 cron에 두면,
실행 시점에 `chatgpt_auth.json`의 활성 계정 이메일을 읽어 BUY/SELL effort를 고른다. 매핑에 없거나 파일을 읽을
수 없으면 기존 `PRISM_{BUY,SELL}_CODEX_EFFORT`를 쓴다. 계정만 바꾸면 강도가 따라 바뀐다.
