# 대시보드 설정 가이드

이 문서는 대시보드 프론트엔드의 설치 및 실행 방법을 안내합니다.

## 사전 요구사항

- Node.js 및 npm 설치
- Python 환경 구성
- 프로젝트 루트 디렉토리 접근 권한
- PM2 설치 (`npm install -g pm2`)

## 포트 정보

- **대시보드**: 포트 3000 (Next.js 기본 포트)
- **Streamlit 앱**: 포트 8501 (Streamlit 기본 포트)

> 두 서비스는 서로 다른 포트를 사용하므로 **포트 충돌 없이** 동시 실행 가능합니다.

## 대시보드 도메인에서 Stance 운영

대시보드 app server가 `https://analysis.stocksimulation.kr/api/stance/v1`을 공개한다.
허용된 Stance 경로만 내부 단일 워커로 전달하며, 원장 서버는
`127.0.0.1:8800` 밖으로 노출하지 않는다.

```bash
cp .env.example .env.production

# .env.production
STANCE_INTERNAL_URL=http://127.0.0.1:8800
STANCE_PUBLIC_REGISTRATION=true
STANCE_REGISTRATION_TOKEN=<내부-Stance-서버와-같은-긴-무작위-비밀값>
```

등록 비밀값은 서버끼리만 공유한다. `NEXT_PUBLIC_` 접두사를 붙이거나 API 응답·브라우저
코드에 넣으면 안 된다. 사용자는 Stance 탭에서 등록 정보를 입력하고 `stk_...` 연동 키를
한 번만 확인한다.

공개 경로:

- `/?tab=stance` — 전략 등록·리더보드
- `/api/stance/v1/stances` — 판단 선언
- `/api/stance/v1/portfolio` — 인증된 전략 스냅샷
- `/api/stance/v1/keys/rotate` — 연동 키 교체
- `/api/stance/v1/leaderboard`, `/markets`, `/health` — 공개 조회

내부 Stance 서버는 반드시 루프백 주소와 단일 워커를 유지한다.
`/api/stance/v1/strategies`에는 CDN이나 리버스 프록시의 요청 제한도 추가한다.
app server 자체에도 프로세스 단위의 소규모 등록 제한이 들어 있다.

### 포트 변경이 필요한 경우

만약 포트 3000이 이미 사용 중이라면 다음과 같이 포트를 변경할 수 있습니다:

```bash
# 개발 모드에서 포트 변경
PORT=3001 npm run dev

# 프로덕션 모드에서 포트 변경
PORT=3001 npm start

# PM2에서 포트 변경
PORT=3001 pm2 start npm --name "dashboard" -- start
```

## 설정 방법

### 1. 데이터 자동 갱신 설정 (Crontab)

대시보드 데이터를 주기적으로 갱신하기 위해 crontab에 다음 내용을 추가합니다:

```bash
# crontab 편집
crontab -e

# 아래 내용 추가
# 매일 오전 11시 05분 대시보드 데이터 갱신
05 11 * * * cd /project-root/examples && python generate_dashboard_json.py >> /project-root/logs/generate_dashboard_json.log 2>&1

# 매일 오후 05시 05분 대시보드 데이터 갱신
05 17 * * * cd /project-root/examples && python generate_dashboard_json.py >> /project-root/logs/generate_dashboard_json.log 2>&1
```

> **참고**: `/project-root`를 실제 프로젝트의 절대 경로로 변경해야 합니다.

### 2. 대시보드 디렉토리로 이동

```bash
cd examples/dashboard
```

### 3. 의존성 설치

```bash
npm install react-is --legacy-peer-deps
```

### 4. 프로젝트 빌드

```bash
npm run build
```

### 5. PM2로 대시보드 실행

```bash
# PM2로 애플리케이션 시작 (기본 포트 3000)
pm2 start npm --name "dashboard" -- start

# 특정 포트로 시작하려면
PORT=3001 pm2 start npm --name "dashboard" -- start

# PM2 프로세스 목록 확인
pm2 list

# 로그 확인
pm2 logs dashboard

# 서버 재부팅 시 자동 시작 설정
pm2 startup
pm2 save
```

## PM2 주요 명령어

```bash
# 대시보드 상태 확인
pm2 status

# 대시보드 재시작
pm2 restart dashboard

# 대시보드 중지
pm2 stop dashboard

# 대시보드 삭제
pm2 delete dashboard

# 실시간 로그 보기
pm2 logs dashboard --lines 100

# 모니터링
pm2 monit
```

## 문제 해결

- 의존성 설치 중 오류가 발생하면 `--legacy-peer-deps` 플래그를 사용하세요.
- crontab 설정 후 로그 파일(`/project-root/logs/generate_dashboard_json.log`)을 확인하여 스크립트가 정상적으로 실행되는지 확인하세요.
- 로그 디렉토리(`/project-root/logs`)가 존재하는지 확인하세요. 없다면 생성해야 합니다:
  ```bash
  mkdir -p /project-root/logs
  ```
- PM2가 설치되지 않았다면 다음 명령어로 설치하세요:
  ```bash
  npm install -g pm2
  ```
- 포트 3000이 이미 사용 중이라면:
  ```bash
  # 포트 사용 확인
  lsof -i :3000
  
  # 또는 다른 포트로 실행
  PORT=3001 pm2 start npm --name "dashboard" -- start
  ```

## 실행 확인

대시보드가 정상적으로 실행되면 브라우저에서 다음 주소로 접속하여 확인할 수 있습니다:

- 기본 포트: `http://localhost:3000`
- 포트 변경 시: `http://localhost:{변경한_포트}`

PM2 대시보드에서 프로세스 상태를 확인할 수도 있습니다:
```bash
pm2 status
```

## 서비스 구조

```
프로젝트
├── examples/
│   ├── dashboard/          # Next.js 대시보드 (포트 3000)
│   └── streamlit/          # Streamlit 앱 (포트 8501)
└── ...
```

두 서비스는 독립적으로 실행되며 포트 충돌 없이 동시 운영이 가능합니다.
