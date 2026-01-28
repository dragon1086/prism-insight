# PRISM-INSIGHT 설치 가이드

> PRISM-INSIGHT의 완전한 설치 및 설정 가이드

**언어**: [English](SETUP.md) | [한국어](SETUP_ko.md)

---

## 목차

1. [사전 요구사항](#사전-요구사항)
2. [Docker로 빠른 시작](#docker로-빠른-시작)
3. [수동 설치](#수동-설치)
4. [설정 파일](#설정-파일)
5. [플랫폼별 설치](#플랫폼별-설치)
6. [선택 구성요소](#선택-구성요소)
7. [설치 확인](#설치-확인)
8. [문제 해결](#문제-해결)

---

## 사전 요구사항

### 필수

| 구성요소 | 버전 | 용도 |
|----------|------|------|
| Python | 3.10+ | 코어 런타임 |
| Node.js | 18+ | MCP 서버 (perplexity-ask) |
| pip | 최신 | 패키지 관리 |

### API 키 (전체 기능에 필요)

| 서비스 | 용도 | 키 발급 |
|--------|------|---------|
| OpenAI | 분석 및 트레이딩용 GPT-5 | [platform.openai.com](https://platform.openai.com/api-keys) |
| Anthropic | 텔레그램 봇용 Claude | [console.anthropic.com](https://console.anthropic.com/) |
| Firecrawl | 웹 크롤링 MCP | [firecrawl.dev](https://www.firecrawl.dev/) |
| Perplexity | 웹 검색 MCP | [perplexity.ai](https://www.perplexity.ai/) |

### API 키 (선택)

| 서비스 | 용도 | 키 발급 |
|--------|------|---------|
| 텔레그램 봇 | 채널 메시징 | [BotFather](https://t.me/botfather) |
| 한국투자증권 | 자동매매 | [KIS Developers](https://apiportal.koreainvestment.com/) |
| Finnhub | 미국 주식 뉴스 | [finnhub.io](https://finnhub.io/) |

---

## Docker로 빠른 시작

Docker는 프로덕션 환경에서 PRISM-INSIGHT를 실행하는 권장 방법입니다.

### 1단계: 저장소 클론

```bash
git clone https://github.com/dragon1086/prism-insight.git
cd prism-insight
```

### 2단계: 설정 파일 준비

```bash
# 핵심 설정 (필수)
cp mcp_agent.config.yaml.example mcp_agent.config.yaml
cp mcp_agent.secrets.yaml.example mcp_agent.secrets.yaml

# 설정 파일에 API 키 입력
# - mcp_agent.secrets.yaml: OpenAI API 키
# - mcp_agent.config.yaml: KRX 인증 정보 (카카오 계정)
```

### 3단계: 빌드 및 실행

```bash
# 컨테이너 빌드 및 시작
docker-compose up -d

# 컨테이너 상태 확인
docker ps

# 로그 확인
docker-compose logs -f
```

### 4단계: 분석 실행

```bash
# 수동으로 오전 분석 실행
docker exec prism-insight-container python3 stock_analysis_orchestrator.py --mode morning --no-telegram

# 텔레그램과 함께 실행 (설정된 경우)
docker exec prism-insight-container python3 stock_analysis_orchestrator.py --mode morning
```

### Docker 명령어 참조

```bash
# 컨테이너 중지
docker-compose down

# 코드 변경 후 재빌드
docker-compose up -d --build

# 실시간 로그 보기
docker-compose logs -f prism-insight

# 컨테이너 셸 접속
docker exec -it prism-insight-container /bin/bash
```

> **참고**: Docker 컨테이너에는 자동 일일 분석을 위한 예약된 cron 작업이 포함되어 있습니다. 스케줄 설정은 `docker/entrypoint.sh`를 참조하세요.

---

## 수동 설치

개발 또는 커스텀 환경을 위해 다음 단계를 따라 수동 설치합니다.

### 1단계: 저장소 클론

```bash
git clone https://github.com/dragon1086/prism-insight.git
cd prism-insight
```

### 2단계: Python 의존성 설치

```bash
pip install -r requirements.txt
```

### 3단계: 설정 파일 준비

예시 파일을 복사하여 설정 파일을 생성합니다:

```bash
# 핵심 설정 (필수)
cp mcp_agent.config.yaml.example mcp_agent.config.yaml
cp mcp_agent.secrets.yaml.example mcp_agent.secrets.yaml

# 환경 변수 (선택 - 텔레그램용)
cp .env.example .env

# Streamlit 대시보드 (선택)
cp ./examples/streamlit/config.py.example ./examples/streamlit/config.py

# 트레이딩 설정 (선택 - 자동매매용)
cp ./trading/config/kis_devlp.yaml.example ./trading/config/kis_devlp.yaml
```

### 4단계: API 키 설정

`mcp_agent.secrets.yaml`에 API 키를 입력합니다:

```yaml
# 필수
OPENAI_API_KEY: "sk-..."

# 선택 (전체 기능용)
ANTHROPIC_API_KEY: "sk-ant-..."
FIRECRAWL_API_KEY: "fc-..."
PERPLEXITY_API_KEY: "pplx-..."
```

### 5단계: MCP 서버 설정

`mcp_agent.config.yaml`을 편집합니다:

```yaml
execution_engine: asyncio

mcp:
  servers:
    kospi_kosdaq:
      command: "python3"
      args: ["-m", "kospi_kosdaq_stock_server"]
      env:
        KAKAO_ID: "your_kakao_email@example.com"
        KAKAO_PW: "your_kakao_password"

    firecrawl: firecrawl-mcp
    perplexity: node perplexity-ask/dist/index.js
    sqlite: uv run mcp-server-sqlite --directory sqlite stock_tracking_db.sqlite
    time: uvx mcp-server-time

openai:
  default_model: gpt-5
  reasoning_effort: medium
```

> **참고**: 한국 시장 데이터(KRX 데이터 마켓플레이스 인증)를 위해 카카오 계정 정보가 필요합니다.
>
> **2단계 인증 사용자**: 카카오 2단계 인증이 설정되어 있으면 매 분석시마다 앱에서 확인이 필요합니다. 비활성화하려면: 카카오앱 > 전체 설정 > 카카오계정 > 계정 보안 > 2단계 인증 '사용 안함'.

### 6단계: Playwright 설치 (PDF 생성용)

```bash
# 패키지 설치 (requirements.txt에 포함됨)
pip install playwright

# Chromium 브라우저 다운로드
python3 -m playwright install chromium
```

자세한 내용은 [플랫폼별 설치](#플랫폼별-설치)를 참조하세요.

### 7단계: perplexity-ask MCP 서버 설치

```bash
cd perplexity-ask
npm install
cd ..
```

### 8단계: 한글 폰트 설치 (Linux만 해당)

차트의 한글 표시를 위해 필요합니다. [플랫폼별 설치](#플랫폼별-설치)를 참조하세요.

---

## 설정 파일

### 핵심 설정 (필수)

| 파일 | 용도 |
|------|------|
| `mcp_agent.config.yaml` | MCP 서버 설정 |
| `mcp_agent.secrets.yaml` | API 키 및 시크릿 |

### 텔레그램 설정 (선택)

| 파일 | 용도 |
|------|------|
| `.env` | 텔레그램 채널 ID, 봇 토큰 |

```bash
# .env 파일
TELEGRAM_CHANNEL_ID="-1001234567890"
TELEGRAM_BOT_TOKEN="1234567890:ABCdefGHIjklMNOpqrsTUVwxyz"

# 다국어 브로드캐스팅 (선택)
TELEGRAM_CHANNEL_ID_EN="-1001234567891"
TELEGRAM_CHANNEL_ID_JA="-1001234567892"
TELEGRAM_CHANNEL_ID_ZH="-1001234567893"
```

> **팁**: `--no-telegram` 옵션을 사용하면 텔레그램 설정 없이 실행 가능합니다!

### 트레이딩 설정 (선택)

| 파일 | 용도 |
|------|------|
| `trading/config/kis_devlp.yaml` | 한국투자증권 API |

```yaml
# kis_devlp.yaml
default_unit_amount: 10000     # 종목당 매수 금액 (KRW)
auto_trading: true
default_mode: demo             # "demo" 또는 "real"

kis_app_key: "YOUR_APP_KEY"
kis_app_secret: "YOUR_APP_SECRET"
kis_account_number: "12345678-01"
kis_account_code: "01"
```

### 웹 인터페이스 설정 (선택)

| 파일 | 용도 |
|------|------|
| `examples/streamlit/config.py` | Streamlit 대시보드 API 키 |

---

## 플랫폼별 설치

### macOS

```bash
# Playwright
pip3 install playwright
python3 -m playwright install chromium

# 한글 폰트: 기본 지원, 별도 설치 불필요
```

### Ubuntu / Debian

```bash
# Playwright 및 의존성
pip install playwright
python3 -m playwright install --with-deps chromium

# 한글 폰트
./cores/ubuntu_font_installer.py

# 폰트 캐시 갱신
sudo fc-cache -fv
python3 -c "import matplotlib.font_manager as fm; fm.fontManager.rebuild()"
```

### Rocky Linux 8 / CentOS / RHEL

```bash
# Playwright
pip3 install playwright
playwright install chromium

# --with-deps가 동작하지 않으면 수동으로 의존성 설치:
dnf install -y epel-release
dnf install -y nss nspr atk at-spi2-atk cups-libs libdrm \
    libxkbcommon libXcomposite libXdamage libXfixes \
    libXrandr mesa-libgbm alsa-lib pango cairo

# 또는 설치 스크립트 사용
cd utils
chmod +x setup_playwright.sh
./setup_playwright.sh

# 한글 폰트
sudo dnf install google-nanum-fonts

# 폰트 캐시 갱신
sudo fc-cache -fv
python3 -c "import matplotlib.font_manager as fm; fm.fontManager.rebuild()"
```

### Windows

```bash
# Playwright
pip install playwright
python -m playwright install chromium

# 한글 폰트: 기본 지원, 별도 설치 불필요
```

Playwright 설치에 대한 자세한 내용은 [utils/PLAYWRIGHT_SETUP_ko.md](../utils/PLAYWRIGHT_SETUP_ko.md)를 참조하세요.

---

## 선택 구성요소

### 자동 스케줄링 (Crontab)

자동 실행을 설정합니다:

```bash
# 간편 설정 (권장)
chmod +x utils/setup_crontab_simple.sh
utils/setup_crontab_simple.sh

# 또는 고급 설정
chmod +x utils/setup_crontab.sh
utils/setup_crontab.sh
```

자세한 내용은 [utils/CRONTAB_SETUP.md](../utils/CRONTAB_SETUP.md)를 참조하세요.

### 미국 주식 모듈

미국 시장 분석 (NYSE, NASDAQ):

```bash
# 추가 API 키 (선택, 뉴스 강화용)
# .env에 추가:
FINNHUB_API_KEY="your_finnhub_key"

# 미국 주식 분석 실행
python prism-us/us_stock_analysis_orchestrator.py --mode morning --no-telegram
```

### 이벤트 기반 트레이딩 시그널

Redis/Upstash 또는 GCP Pub/Sub 통합:

```bash
# .env 파일
UPSTASH_REDIS_REST_URL="https://xxx.upstash.io"
UPSTASH_REDIS_REST_TOKEN="your-token"

# 또는 GCP용
GCP_PROJECT_ID="your-gcp-project"
GCP_PUBSUB_SUBSCRIPTION_ID="your-subscription"
GCP_CREDENTIALS_PATH="/path/to/service-account.json"
```

---

## 설치 확인

### 빠른 테스트 (텔레그램 없이)

```bash
# 텔레그램 없이 오전 분석 실행
python stock_analysis_orchestrator.py --mode morning --no-telegram
```

### 개별 구성요소 테스트

```bash
# 1. 급등주 포착 테스트
python trigger_batch.py morning INFO --output trigger_results.json

# 2. PDF 변환 테스트
python pdf_converter.py sample.md sample.pdf

# 3. MCP 서버 연결 테스트
python cores/main.py
```

### 예상 출력

성공적인 실행 시 생성되는 파일:
- `trigger_results_*.json` - 포착된 급등주
- `reports/*.md` - Markdown 분석 리포트
- `pdf_reports/*.pdf` - PDF 버전 리포트
- `stock_tracking_db.sqlite` - 매매 시뮬레이션 데이터베이스

---

## 문제 해결

### 일반적인 문제

| 문제 | 해결 방법 |
|------|----------|
| Playwright PDF 실패 | `python3 -m playwright install chromium` 실행 |
| 한글 폰트 누락 | 폰트 설치 후 `fc-cache -fv` 실행 |
| MCP 서버 실패 | `mcp_agent.secrets.yaml`의 API 키 확인 |
| 카카오 인증 실패 | 2단계 인증 비활성화 또는 앱에서 확인 |
| JSON 파싱 오류 | 라이브러리가 자동 복구; 로그에서 상세 내용 확인 |

### 디버그 모드

상세 로깅 활성화:

```bash
# 코드 또는 환경 변수에서 로그 레벨 설정
export LOG_LEVEL=DEBUG
python stock_analysis_orchestrator.py --mode morning --no-telegram
```

### 로그 파일

오류 확인:

```bash
# 최근 로그 파일 목록
ls -la *.log

# 특정 로그 보기
tail -f stock_analysis_*.log
```

### 도움 받기

- **문서**: [docs/](../docs/)
- **GitHub Issues**: [버그 신고](https://github.com/dragon1086/prism-insight/issues)
- **텔레그램**: [@stock_ai_agent](https://t.me/stock_ai_agent)
- **디스커션**: [GitHub Discussions](https://github.com/dragon1086/prism-insight/discussions)

---

## 다음 단계

설치 완료 후:

1. **빠른 시작 테스트**: `python stock_analysis_orchestrator.py --mode morning --no-telegram` 실행
2. **대시보드 탐색**: [analysis.stocksimulation.kr](https://analysis.stocksimulation.kr/) 방문
3. **커뮤니티 참여**: [텔레그램 채널](https://t.me/stock_ai_agent) 구독
4. **커스터마이징**: `cores/agents/` 디렉토리의 에이전트 수정

---

**문서 버전**: 1.0
**최종 업데이트**: 2026-01-28
