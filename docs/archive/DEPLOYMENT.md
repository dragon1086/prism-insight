# Deployment

자체 인스턴스에서 PRISM Archive를 운영하기 위한 배포 가이드입니다.

두 가지 배포 모드를 지원합니다:

- **단일 서버 모드** (권장): 한 대의 서버가 분석, archive DB, 텔레그램 봇을 모두 담당.
- **양 서버 모드**: DB/배치 서버와 텔레그램/앱 서버를 분리하고 SSH 터널로 연결.

---

## 1. 시스템 요구사항

### 하드웨어 (최소 / 권장)

| 항목 | 최소 | 권장 |
|---|---|---|
| RAM | 2 GB | 4 GB (분석 파이프라인 포함 시) |
| CPU | 1 vCPU | 2 vCPU |
| 디스크 | 10 GB | 50 GB (1년치 리포트 + 가격 히스토리) |
| OS | Ubuntu 22.04+, Debian 12, Rocky 9 | 동일 |

### 소프트웨어

- Python 3.11+
- SQLite 3.38+ (FTS5 지원, 대부분의 최신 리눅스에 기본 포함)
- Node.js 18+ (firecrawl / perplexity MCP 용)
- `uv` / `uvx` (yahoo_finance / sec_edgar / time MCP 용)
- `openssh-client`, `autossh` (양 서버 모드 전용)

### 계정

- **OpenAI API 키** — 임베딩 + 주간 압축 / fact 증류 (`gpt-5.4-mini`)
- **Anthropic API 키** — InsightAgent (`claude-sonnet-4-6`)
- **Telegram Bot 토큰** — `@BotFather`로 생성
- (선택) Perplexity, Firecrawl API 키 — 외부 도구용

---

## 2. 환경변수

프로젝트 루트에 `.env` 생성:

### 필수

```bash
# Telegram
TELEGRAM_AI_BOT_TOKEN=<your-bot-token>
TELEGRAM_CHANNEL_ID=<channel-id-if-any>

# InsightAgent 쿼터 (KST 자정 리셋)
INSIGHT_DAILY_LIMIT=20
```

### 양 서버 모드 전용

db-server `.env`:
```bash
ARCHIVE_API_KEY=<openssl rand -hex 32>
ARCHIVE_API_HOST=127.0.0.1          # 외부 노출 금지, SSH 터널만 허용
ARCHIVE_API_PORT=8765
```

app-server `.env`:
```bash
ARCHIVE_API_URL=http://127.0.0.1:8765   # SSH 터널 경유
ARCHIVE_API_KEY=<db-server와 동일 값>
```

### API 키 (선택 파일)

`mcp_agent.secrets.yaml`:
```yaml
openai:
  api_key: sk-...
anthropic:
  api_key: sk-ant-...
```

Perplexity / Firecrawl 키는 `mcp_agent.config.yaml`의 각 서버 `env:` 블록에 넣습니다 (예시는 `mcp_agent.config.yaml.example` 참조).

### 전체 환경변수 레퍼런스

| 변수 | 기본값 | 설명 |
|---|---|---|
| `TELEGRAM_AI_BOT_TOKEN` | (필수) | Telegram 봇 토큰 |
| `TELEGRAM_CHANNEL_ID` | `0` | 채널 ID (구독 검증용, 선택) |
| `ARCHIVE_API_URL` | (없음) | 양 서버 모드 활성화. 미설정 시 단일 서버 모드 |
| `ARCHIVE_API_KEY` | (없음) | Bearer 토큰. `/health` 제외 모든 엔드포인트 보호 |
| `ARCHIVE_API_HOST` | `0.0.0.0` | FastAPI 바인드 주소. SSH 터널 시 `127.0.0.1` |
| `ARCHIVE_API_PORT` | `8765` | FastAPI 포트 |
| `INSIGHT_DAILY_LIMIT` | `20` | `/insight` 사용자당 일일 한도. `0` = 무제한 |

---

## 3. 단일 서버 모드 설치 (5단계)

### Step 1. 코드 & 의존성

```bash
git clone git@github.com:dragon1086/prism-insight.git
cd prism-insight
python3.11 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
npm install -g npx   # MCP 서버용
```

### Step 2. 설정 파일

```bash
cp .env.example .env
cp mcp_agent.config.yaml.example mcp_agent.config.yaml
cp mcp_agent.secrets.yaml.example mcp_agent.secrets.yaml
# 위 3개 파일 편집하여 토큰/키 채우기
```

### Step 3. DB 마이그레이션

`init_db()`는 idempotent — 몇 번을 호출해도 안전.

```bash
python -c "import asyncio; from cores.archive.archive_db import init_db; asyncio.run(init_db())"
```

확인:
```bash
sqlite3 archive.db ".tables" | tr ' ' '\n' | grep -iE 'insight|weekly|quota'
# 기대 출력:
# insight_cost_daily
# insight_feedback
# insight_tool_usage
# persistent_insights
# persistent_insights_fts
# ticker_semantic_facts
# user_insight_quota
# weekly_insight_summary
```

### Step 4. 데이터 시드 (리포트 있으면)

```bash
# 기존 분석 리포트 인제스트
python -m cores.archive.ingest --dir reports/ --market kr --dry-run
python -m cores.archive.ingest --dir reports/ --market kr

python -m cores.archive.ingest --dir prism-us/reports/ --market us --dry-run
python -m cores.archive.ingest --dir prism-us/reports/ --market us

# 장기 가격 히스토리 백필 (20~40분 소요)
python update_current_prices.py --concurrency 2
```

SEASON2_START(`2025-09-29`) 이전 리포트는 자동 스킵.

### Step 5. 봇 기동

```bash
mkdir -p logs
nohup python telegram_ai_bot.py > logs/telegram_ai_bot_$(date +%Y%m%d_%H%M%S).log 2>&1 &
```

기동 로그 확인:
```
INFO  Registered 14 bot commands (scope=default)
INFO  Registered 14 bot commands (scope=private)
INFO  Registered 14 bot commands (scope=group)
INFO  Registered 14 bot commands (scope=group_admin)
INFO  Telegram AI conversational bot has started.
```

4개 scope 모두 등록되어야 개인 채팅 + 그룹 채팅 모두에서 `/` 메뉴가 보입니다.

### Step 6. cron 등록

```bash
crontab -e
```

```cron
# 일일 인사이트 (매일 02시)
0 2 * * * cd /home/prism/prism-insight && source venv/bin/activate && python -m cores.archive.auto_insight --type daily --market both >> logs/auto_insight.log 2>&1

# 주간 압축 + 시맨틱 fact 증류 (매주 월 03시)
0 3 * * 1 cd /home/prism/prism-insight && source venv/bin/activate && python -m cores.archive.auto_insight --type all --narrative >> logs/auto_insight.log 2>&1

# 장기 가격 업데이트 (매주 월 04시)
0 4 * * 1 cd /home/prism/prism-insight && source venv/bin/activate && python update_current_prices.py --concurrency 2 >> logs/price_update.log 2>&1
```

---

## 4. 양 서버 모드 (DB/배치 서버 + 앱 서버)

PRISM-INSIGHT 본 배포가 양 서버를 쓸 때 사용. archive DB의 디스크/CPU 부하와 텔레그램 봇의 I/O를 격리할 수 있습니다.

### 아키텍처

```
[사용자] ──TLS──► [app-server 텔레그램 봇]
                       │
                       ▼ (SSH 터널 :8765)
                  [db-server archive_api + archive.db + cron]
```

### 4-1. db-server 설정

Step 1~4는 단일 서버 모드와 동일. 이후:

```bash
# .env에 추가
cat >> .env <<EOF
ARCHIVE_API_KEY=$(openssl rand -hex 32)
ARCHIVE_API_HOST=127.0.0.1
ARCHIVE_API_PORT=8765
EOF

# archive_api 상주 실행 (단일 서버 모드에서는 텔레그램 봇이 직접 import하므로 불필요)
nohup python archive_api.py >> logs/archive_api.log 2>&1 &

# 헬스 체크
curl -s http://127.0.0.1:8765/health
# {"status":"ok",...}

# 외부 바인드 확인 (127.0.0.1만 노출되어야 함)
ss -tln | grep 8765
# LISTEN 0  2048  127.0.0.1:8765  0.0.0.0:*
```

### 4-2. app-server 설정

```bash
# 1. 앱 서버에 prism 유저 SSH 키 있는지 확인 (없으면 생성)
sudo -u prism ssh-keygen -t ed25519 -f ~prism/.ssh/id_ed25519 -N ""

# 2. prism 공개 키를 db-server에 등록 (포트 포워드 전용 권한)
PRISM_PUB=$(sudo cat /home/prism/.ssh/id_ed25519.pub)
ssh root@DB_SERVER_IP "echo 'command=\"echo tunnel-only\",no-pty,no-X11-forwarding,no-agent-forwarding,no-user-rc,permitopen=\"127.0.0.1:8765\" $PRISM_PUB' >> ~/.ssh/authorized_keys"

# 3. SSH 터널 wrapper 스크립트
sudo -u prism tee /home/prism/prism-insight/bin/archive_tunnel.sh <<'EOF'
#!/bin/bash
# Persistent SSH tunnel: app-server -> db-server:8765
while true; do
  ssh -N -o ServerAliveInterval=30 -o ServerAliveCountMax=3 \
      -o ExitOnForwardFailure=yes -o StrictHostKeyChecking=accept-new \
      -L 127.0.0.1:8765:127.0.0.1:8765 \
      root@DB_SERVER_IP
  echo "tunnel exited at $(date), reconnecting in 5s" >&2
  sleep 5
done
EOF
sudo chmod +x /home/prism/prism-insight/bin/archive_tunnel.sh
sudo chown prism:prism /home/prism/prism-insight/bin/archive_tunnel.sh

# 4. 터널 기동
sudo -u prism nohup /home/prism/prism-insight/bin/archive_tunnel.sh \
  >> /home/prism/prism-insight/logs/archive_tunnel.log 2>&1 &

# 5. .env에 app-server 설정 추가
sudo -u prism tee -a /home/prism/prism-insight/.env <<EOF
ARCHIVE_API_URL=http://127.0.0.1:8765
ARCHIVE_API_KEY=<db-server와 동일 값>
INSIGHT_DAILY_LIMIT=20
EOF

# 6. 터널 통해 응답 확인
sudo -u prism curl -s http://127.0.0.1:8765/health
```

### 4-3. app-server 봇 실행 (login shell PATH 필수)

```bash
sudo -iu prism bash -c '
  cd /home/prism/prism-insight && 
  nohup /home/prism/.pyenv/versions/3.11.6/bin/python telegram_ai_bot.py \
    > logs/telegram_ai_bot_$(date +%Y%m%d_%H%M%S).log 2>&1 &
'
```

> 🚨 **주의**: `sudo -u prism bash -c`(non-login)으로 기동하면 `~/.local/bin`이 PATH에서 빠져 `uvx` 기반 MCP 서버(yahoo_finance / time / sec_edgar)가 초기화 실패합니다. 반드시 `-iu` 플래그로 login shell 사용.

### 4-4. 프로덕션 추천 — systemd 서비스

`/etc/systemd/system/prism-archive-api.service` (db-server):
```ini
[Unit]
Description=PRISM Archive API
After=network.target

[Service]
User=root
WorkingDirectory=/root/prism-insight
EnvironmentFile=/root/prism-insight/.env
ExecStart=/root/prism-insight/bin/python archive_api.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

`/etc/systemd/system/archive-tunnel.service` (app-server):
```ini
[Unit]
Description=SSH tunnel to archive-api on db-server
After=network-online.target

[Service]
User=prism
Environment="AUTOSSH_GATETIME=0"
ExecStart=/usr/bin/autossh -M 0 -N \
  -o ServerAliveInterval=30 -o ServerAliveCountMax=3 \
  -o ExitOnForwardFailure=yes -o StrictHostKeyChecking=accept-new \
  -L 127.0.0.1:8765:127.0.0.1:8765 \
  root@DB_SERVER_IP
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
systemctl daemon-reload
systemctl enable --now prism-archive-api           # db-server
systemctl enable --now archive-tunnel              # app-server
```

---

## 5. 보안 체크리스트

- [ ] `ARCHIVE_API_KEY` 32자 이상의 무작위 토큰 (`openssl rand -hex 32`)
- [ ] `ARCHIVE_API_HOST=127.0.0.1` — 공용 인터넷 노출 금지
- [ ] `ss -tln | grep 8765`로 바인드 확인
- [ ] db-server `authorized_keys`에서 app-server 키에 `command="echo tunnel-only", permitopen="127.0.0.1:8765"` 제한 적용
- [ ] Telegram 봇 토큰 `.env`에만 저장 (git ignored)
- [ ] `mcp_agent.secrets.yaml` git ignored
- [ ] `.gitignore`에 `archive.db*`, `*.db`, `logs/`, `.env` 포함
- [ ] cron 로그 주기적 로테이션 (logrotate)

---

## 6. 트러블슈팅

### 봇이 `/insight` 응답 없음

- **단일 서버 모드**: `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` 확인. `python archive_query.py --insight-stats`가 에러 없으면 DB는 정상.
- **양 서버 모드**: 봇 로그에서 `Archive API returned` 에러 확인. 터널 살아있는지:
  ```bash
  pgrep -af archive_tunnel.sh
  pgrep -af "ssh.*8765:127.0.0.1:8765"
  curl -s http://127.0.0.1:8765/health    # app-server에서
  ```

### `uvx: command not found` 오류

bot / archive_api 프로세스의 PATH에 `~/.local/bin`이 빠짐:
```bash
BOTPID=$(pgrep -f telegram_ai_bot | head -1)
cat /proc/$BOTPID/environ | tr '\0' '\n' | grep ^PATH=
```

`/home/<user>/.local/bin` 누락이면 login shell로 재시작:
```bash
sudo -iu prism bash -c '...'   # -i 플래그가 .bash_profile 로드
```

### BotFather 메뉴에 `/insight` 안 보임

- 로그에 `Registered 14 bot commands (scope=default/private/group/group_admin)` 4줄 있는지 확인
- Telegram 클라이언트 캐시 — 1~2분 대기 후 새로고침
- 그룹 채팅에서 `/`도 안 보이면 `scope=group` 등록 실패 — 봇이 그룹의 멤버인지 확인

### `/insight` 답변이 2025년 데이터 사용

시스템 프롬프트에 오늘 날짜 주입 기능이 정상 동작하지 않음. `cores/archive/insight_agent.py`의 `_build_retrieval_context` 근처 `today_kst` 로직 확인. 서버 시간대 설정 (`date -u`, `timedatectl`) 점검.

### `confidence_score` 컬럼 없다는 오류

오래된 DB. `init_db()` 재실행:
```bash
python -c "
import asyncio
from cores.archive.archive_db import init_db, _initialized_paths
_initialized_paths.clear()
asyncio.run(init_db())
"
```

`_migrate_persistent_insights_columns()`가 idempotent로 컬럼 추가.

### 비용 폭증 대응

```bash
# 일 누적 확인
sqlite3 archive.db "SELECT * FROM insight_cost_daily ORDER BY date DESC LIMIT 7"

# perplexity/firecrawl 호출 급증이면 시스템 프롬프트 가드레일 강화 또는
# INSIGHT_DAILY_LIMIT 축소
```

시스템 프롬프트 (`cores/archive/insight_prompts.py`)에 `각 도구 1회 이하 권장` 조항이 있으나 LLM 지시 준수 여부는 결정적이지 않음. 관찰이 중요.

### DB 백업

```bash
# hot backup (SQLite 기본 툴)
sqlite3 archive.db ".backup backup_$(date +%Y%m%d).db"

# 복구
cp backup_20260422.db archive.db
```

WAL 모드 사용 중이므로 `archive.db-wal`, `archive.db-shm` 파일도 함께 있을 수 있습니다 (정상).

---

## 7. 업그레이드 / 마이그레이션

신규 버전으로 업그레이드:
```bash
git pull origin main
pip install -r requirements.txt
# 스키마 변경 자동 적용 (init_db는 idempotent + ALTER TABLE 마이그레이션 포함)
python -c "
import asyncio
from cores.archive.archive_db import init_db, _initialized_paths
_initialized_paths.clear()
asyncio.run(init_db())
"
# 서비스 재시작
systemctl restart prism-archive-api
systemctl restart telegram-ai-bot    # or pkill + nohup
```

### 롤백

```sql
-- 모든 Phase B 테이블 제거 (archive 테이블은 유지)
DROP TABLE IF EXISTS insight_feedback;
DROP TABLE IF EXISTS ticker_semantic_facts;
DROP TABLE IF EXISTS persistent_insights;
DROP TABLE IF EXISTS persistent_insights_fts;
DROP TABLE IF EXISTS weekly_insight_summary;
DROP TABLE IF EXISTS insight_tool_usage;
DROP TABLE IF EXISTS user_insight_quota;
DROP TABLE IF EXISTS insight_cost_daily;
DROP VIEW IF EXISTS insight_metrics_daily;
ALTER TABLE persistent_insights DROP COLUMN confidence_score;   -- SQLite 3.35+
```

`report_archive` / `report_enrichment` / `ticker_price_history`는 핵심 데이터이므로 보존.

---

## 8. 참고

- [ARCHITECTURE.md](ARCHITECTURE.md) — 시스템이 어떻게 돌아가는지
- [USAGE.md](USAGE.md) — 사용자 인터페이스 전체
- 설계 의사결정 원본: `docs/superpowers/specs/2026-04-21-persistent-insight-agent-design.md`
