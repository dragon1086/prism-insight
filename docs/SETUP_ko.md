# PRISM-INSIGHT 설치 가이드

> 한국·미국 주식 분석, PDF 생성, 선택적 텔레그램 전송과 자동매매까지 구성하는 현행 설치 안내서입니다.

**언어**: [English](SETUP.md) | [한국어](SETUP_ko.md)
**최종 검증 기준**: 2026-07-29, PR #493 적용 후보와 현재 `main` 대조

---

## 1. 먼저 선택할 실행 방식

| 목적 | 권장 경로 | 필요한 인증 |
|---|---|---|
| 미국 종목 1개를 가장 빨리 체험 | [`quickstart.sh`](#2-60초-미국-주식-체험) | OpenAI API 키 |
| API 요금 없이 로컬 분석 | [ChatGPT OAuth](#3-chatgpt-pluspro-oauth) | ChatGPT Plus/Pro |
| 개발·디버깅 | [Python 수동 설치](#4-python-수동-설치) | API 키 또는 OAuth |
| cron 포함 상시 운영 | [Docker](#5-docker-운영) | API 키 또는 OAuth |

한국 시장의 전체 후보 선별에는 KRX 로그인이 필요합니다. 미국 단일 종목 데모는 KRX나 텔레그램 설정 없이 실행할 수 있습니다.

## 2. 60초 미국 주식 체험

### 요구사항

- Python 3.10 이상
- `pip` 또는 `uv`
- OpenAI API 키

```bash
git clone https://github.com/dragon1086/prism-insight.git
cd prism-insight
./quickstart.sh YOUR_OPENAI_API_KEY
```

스크립트는 의존성 및 Playwright Chromium을 설치하고 예제 설정을 만든 뒤 `AAPL` 보고서를 생성합니다.

```bash
python3 demo.py MSFT
python3 demo.py NVDA
python3 demo.py TSLA --language ko
```

미국 PDF는 `prism-us/pdf_reports/`에 저장됩니다. 전체 배치를 시험하려면 다음 명령을 사용합니다.

```bash
python3 prism-us/us_stock_analysis_orchestrator.py --mode morning --no-telegram
```

## 3. ChatGPT Plus/Pro OAuth

OpenAI API 키 대신 ChatGPT 구독 인증을 사용할 수 있습니다.

```bash
python3 -m cores.chatgpt_proxy.oauth_login

# 계정 변경 또는 강제 재인증
python3 -m cores.chatgpt_proxy.oauth_login --force

PRISM_OPENAI_AUTH_MODE=chatgpt_oauth \
  python3 stock_analysis_orchestrator.py --mode morning --no-telegram
```

토큰은 로컬에서 관리되고 자동 갱신됩니다. 서버나 Docker처럼 브라우저 접근이 제한된 환경에서는 먼저 호스트에서 로그인한 뒤 인증 저장소를 해당 실행 환경에 제공해야 합니다.

## 4. Python 수동 설치

### 4.1 요구사항

| 구성요소 | 최소 버전 | 용도 |
|---|---:|---|
| Python | 3.10 | 코어 런타임 |
| Node.js | 18 | Firecrawl·Perplexity MCP |
| `pip` 또는 `uv` | 최신 안정판 | Python 패키지 및 일부 MCP 실행 |
| Chromium | Playwright 설치판 | PDF 생성 |

### 4.2 저장소와 Python 패키지

```bash
git clone https://github.com/dragon1086/prism-insight.git
cd prism-insight
python3 -m pip install -r requirements.txt
python3 -m playwright install chromium
```

`requirements.txt`는 `openai-agents==0.7.0`과 검증된 조합을 유지하기 위해 `openai>=2.9,<2.45`를 사용합니다. `openai>=2.45`에서는 usage 모델의 `cache_write_tokens` 검증 오류가 발생할 수 있습니다. 이 제한은 [PR #493](https://github.com/dragon1086/prism-insight/pull/493)의 런타임 재현 결과와 연결되어 있습니다.

### 4.3 설정 파일 생성

```bash
cp mcp_agent.config.yaml.example mcp_agent.config.yaml
cp mcp_agent.secrets.yaml.example mcp_agent.secrets.yaml

# 선택 기능
cp .env.example .env
cp trading/config/kis_devlp.yaml.example trading/config/kis_devlp.yaml
```

`mcp_agent.secrets.yaml`에는 비밀값만 둡니다.

```yaml
openai:
  api_key: "sk-..."

anthropic:
  api_key: "sk-ant-..."
```

ChatGPT OAuth만 사용할 때는 OpenAI API 키가 없어도 됩니다. 실제 시크릿 파일, `.env`, KIS 설정은 커밋하지 마십시오.

### 4.4 MCP 설정

`mcp_agent.config.yaml.example`을 복사한 뒤 필요한 서버만 활성화합니다.

```yaml
mcp:
  servers:
    firecrawl:
      command: "npx"
      args: ["-y", "firecrawl-mcp@3.17.0"]
      env:
        FIRECRAWL_API_KEY: "fc-..."

    perplexity:
      command: "npx"
      args: ["-y", "@perplexity-ai/mcp-server"]
      env:
        PERPLEXITY_API_KEY: "pplx-..."

    kospi_kosdaq:
      command: "python3"
      args: ["-m", "kospi_kosdaq_stock_server"]
      env:
        KRX_ID: "your_krx_id"
        KRX_PW: "your_krx_password"
        KRX_LOGIN_METHOD: "krx"

    sqlite:
      command: "uv"
      args: ["--directory", "sqlite", "run", "mcp-server-sqlite", "--db-path", "stock_tracking_db"]

    time:
      command: "uvx"
      args: ["mcp-server-time"]
```

중요한 운영 기준:

- Firecrawl은 도구가 사라지는 손상된 `npx` 캐시 회귀를 피하기 위해 `3.17.0`으로 고정합니다.
- Perplexity는 저장소에 없는 `perplexity-ask/dist/index.js`를 직접 실행하지 않고 `npx -y @perplexity-ai/mcp-server`를 사용합니다.
- 한국 시장 인증은 KRX 직접 로그인을 권장합니다. 카카오 로그인을 쓸 때만 `KAKAO_ID`, `KAKAO_PW`, `KRX_LOGIN_METHOD: "kakao"`로 바꿉니다.
- 미국 시장의 `yahoo_finance`, `sec_edgar` 설정은 예제 파일에 포함되어 있습니다.

### 4.5 선택 API 및 환경 변수

| 서비스 | 필요한 기능 | 설정 위치 |
|---|---|---|
| Firecrawl | 웹 문서 수집 | `mcp_agent.config.yaml` |
| Perplexity | 최신 뉴스·시장 검색 | `mcp_agent.config.yaml` |
| Anthropic | 호환·선택 경로 | `mcp_agent.secrets.yaml` |
| Telegram | 알림·상담 | `.env` |
| 한국투자증권 KIS | 주문 실행 | `trading/config/kis_devlp.yaml` |
| Redis 또는 GCP Pub/Sub | 이벤트 기반 신호 | `.env` |

텔레그램 없이 검증할 때는 항상 `--no-telegram`을 붙입니다.

## 5. Docker 운영

### 5.1 준비

```bash
git clone https://github.com/dragon1086/prism-insight.git
cd prism-insight
cp mcp_agent.config.yaml.example mcp_agent.config.yaml
cp mcp_agent.secrets.yaml.example mcp_agent.secrets.yaml
cp .env.example .env
touch stock_tracking_db.sqlite
mkdir -p reports pdf_reports html_reports charts telegram_messages logs
```

설정을 입력한 뒤 Compose v2로 시작합니다.

```bash
docker compose up --build -d
docker compose ps
docker compose logs -f prism-insight
```

수동 안전 실행:

```bash
docker exec prism-insight-container \
  python3 stock_analysis_orchestrator.py --mode morning --no-telegram
```

운영 명령:

```bash
docker compose down
docker compose up --build -d
docker exec -it prism-insight-container /bin/bash
```

### 5.2 Docker cron

`docker/entrypoint.sh`가 컨테이너 시작 시 호스트의 `docker/crontab`을 설치합니다. `ENABLE_CRON=false`로 비활성화할 수 있습니다.

메모리 압축은 일요일 03:00 KST에 루트 `compress_trading_memory.py`를 한 번 실행합니다. 이 스크립트가 `run_us_compression()`까지 호출하므로, 삭제된 `prism-us/compress_us_trading_memory.py`를 04:00에 다시 예약하면 안 됩니다.

```bash
docker exec prism-insight-container crontab -l
docker exec prism-insight-container service cron status
```

세부 Docker 일정과 볼륨은 [README_DOCKER_ko.md](../README_DOCKER_ko.md)를 참조하십시오.

## 6. 플랫폼별 추가 단계

### macOS

```bash
python3 -m playwright install chromium
```

기본 한글 폰트를 사용할 수 있습니다.

### Ubuntu / Debian

```bash
python3 -m playwright install --with-deps chromium
./cores/ubuntu_font_installer.py
sudo fc-cache -fv
```

### Rocky Linux / RHEL 계열

```bash
python3 -m playwright install chromium
cd utils
chmod +x setup_playwright.sh
./setup_playwright.sh
sudo dnf install google-nanum-fonts
sudo fc-cache -fv
```

자세한 패키지 목록은 [PLAYWRIGHT_SETUP_ko.md](../utils/PLAYWRIGHT_SETUP_ko.md)를 참조하십시오.

### Windows

```powershell
python -m pip install -r requirements.txt
python -m playwright install chromium
```

cron 스크립트는 Unix 계열을 대상으로 합니다. Windows에서는 작업 스케줄러나 Docker를 사용하십시오.

## 7. 선택 기능

### 로컬 crontab

```bash
chmod +x utils/setup_crontab.sh
utils/setup_crontab.sh

# US 배치 일정 추가
chmod +x utils/setup_us_crontab.sh
utils/setup_us_crontab.sh
```

US 스크립트는 분석 일정만 추가합니다. 메모리 압축은 루트 주간 작업이 KR과 US를 함께 처리합니다.

### 자동매매

`trading/config/kis_devlp.yaml`에서 기본값을 먼저 모의투자로 유지합니다.

```yaml
auto_trading: true
default_mode: demo
kis_app_key: "YOUR_APP_KEY"
kis_app_secret: "YOUR_APP_SECRET"
kis_account_number: "12345678-01"
kis_account_code: "01"
```

실계좌 전환 전에는 별도의 모의계좌 검증이 필요합니다.

### 이벤트 기반 신호

```dotenv
UPSTASH_REDIS_REST_URL=https://example.upstash.io
UPSTASH_REDIS_REST_TOKEN=...

# 또는 GCP Pub/Sub
GCP_PROJECT_ID=...
GCP_PUBSUB_SUBSCRIPTION_ID=...
GCP_CREDENTIALS_PATH=/path/to/service-account.json
```

## 8. 설치 검증

### 8.1 정적 확인

```bash
python3 -m pip check
bash -n utils/setup_us_crontab.sh
python3 -c "from cores.llm.config_loader import load_mcp_registry; print(sorted(load_mcp_registry('cores/llm/mcp_servers.yaml').names()))"
```

### 8.2 OpenAI Agents 왕복

API 키 경로:

```bash
python3 tools/verify_openai_agents_live.py \
  --auth api --model gpt-5.4-mini --reasoning none
```

OAuth 프록시 경로:

```bash
python3 tools/verify_openai_agents_live.py \
  --auth proxy --model gpt-5.6-terra --reasoning medium
```

이 검증은 실제 네트워크 호출과 사용량을 발생시킬 수 있습니다.

### 8.3 안전한 기능 확인

```bash
# 단일 미국 종목
python3 demo.py AAPL --language ko

# 한국 오전 배치, 텔레그램 비활성
python3 stock_analysis_orchestrator.py --mode morning --no-telegram

# 메모리 압축 변경 예정 내용만 확인
python3 compress_trading_memory.py --dry-run
```

예상 산출물:

- `reports/`, `prism-us/reports/`: Markdown 보고서
- `pdf_reports/`, `prism-us/pdf_reports/`: PDF 보고서
- `charts/`: 차트
- `stock_tracking_db.sqlite`: 포트폴리오·저널 데이터

## 9. 문제 해결

| 증상 | 확인 순서 |
|---|---|
| `InputTokensDetails.cache_write_tokens` 오류 | `openai<2.45`, `openai-agents==0.7.0`인지 확인 |
| Firecrawl 도구가 보이지 않음 | `firecrawl-mcp@3.17.0` 확인 후 손상된 `npx` 캐시 정리 |
| Perplexity가 로컬 JS 파일을 찾지 못함 | `npx -y @perplexity-ai/mcp-server`로 변경 |
| Playwright가 Chromium을 찾지 못함 | `python3 -m playwright install chromium` |
| PDF 한글이 깨짐 | 한글 폰트 설치 후 `fc-cache -fv` |
| KRX 로그인 실패 | `KRX_LOGIN_METHOD`와 직접/카카오 자격 증명 조합 확인 |
| Docker cron이 실행되지 않음 | `ENABLE_CRON`, `service cron status`, `crontab -l` 확인 |
| US 압축 스크립트를 찾지 못함 | 오래된 04:00 US 전용 cron 제거; 루트 압축만 사용 |

로그는 `logs/`, Docker 로그는 `docker compose logs`에서 확인합니다. 해결되지 않으면 [GitHub Issues](https://github.com/dragon1086/prism-insight/issues)에 실행 환경, 명령, 민감정보를 제거한 로그를 첨부하십시오.

## 10. 다음 문서

- [AI 에이전트 시스템](CLAUDE_AGENTS_ko.md)
- [후보 선별·배치 알고리즘](TRIGGER_BATCH_ALGORITHMS.md)
- [매매 저널·메모리](TRADING_JOURNAL.md)
