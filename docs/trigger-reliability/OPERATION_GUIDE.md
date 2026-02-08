# 트리거 신뢰도 운영 가이드

## 주간 리포트 (weekly_insight_report.py)

### 개요
매주 일요일 아침에 자동으로 트리거 신뢰도 주간 리포트를 텔레그램 채널에 발송하는 스크립트입니다.

### 사전 요구사항
- Python 3.10+
- `python-telegram-bot` 패키지 설치
- `.env` 파일에 텔레그램 설정 완료:
  ```
  TELEGRAM_BOT_TOKEN=your_bot_token
  TELEGRAM_CHANNEL_ID=your_channel_id
  ```
- `stock_tracking_db.sqlite` DB 파일 (프로젝트 루트)

### 수동 실행

```bash
# 메시지 미리보기 (텔레그램 발송 안 함)
python3 weekly_insight_report.py --dry-run

# 실제 텔레그램 발송
python3 weekly_insight_report.py
```

### crontab 설정

매주 일요일 10:00 KST에 자동 실행:

```bash
# crontab 편집
crontab -e

# 아래 줄 추가 (경로를 실제 프로젝트 경로로 변경)
0 10 * * 0 cd /path/to/prism-insight && /path/to/python3 weekly_insight_report.py >> /path/to/prism-insight/logs/weekly_report.log 2>&1
```

**GCP VM 예시:**
```bash
0 10 * * 0 cd /home/user/prism-insight && /home/user/.pyenv/shims/python3 weekly_insight_report.py >> /home/user/prism-insight/logs/weekly_report.log 2>&1
```

**참고:** 서버 시간대가 UTC인 경우 KST는 UTC+9이므로:
```bash
# UTC 기준 일요일 01:00 = KST 일요일 10:00
0 1 * * 0 cd /path/to/prism-insight && python3 weekly_insight_report.py >> logs/weekly_report.log 2>&1
```

### 로그 확인

```bash
# 최근 로그 확인
tail -50 logs/weekly_report.log

# 에러만 확인
grep -i error logs/weekly_report.log
```

### 문제 해결

| 증상 | 원인 | 해결 |
|------|------|------|
| "TELEGRAM_BOT_TOKEN not set" | .env 파일 누락 또는 미설정 | `.env` 파일 확인 |
| "No data" 표시 | DB에 데이터 부족 | `stock_tracking_db.sqlite` 확인 |
| crontab 미실행 | 경로 오류 또는 권한 | 절대 경로 사용, `which python3` 확인 |
| 메시지 잘림 | 텔레그램 4096자 제한 | 정상 — 자동 분할 발송 |

## 텔레그램 봇 /triggers 명령어

### 개요
텔레그램 봇에서 `/triggers` 명령어로 실시간 트리거 신뢰도 리포트를 조회할 수 있습니다.

### 사용법
텔레그램에서 봇에게 `/triggers` 입력

### 동작 방식
- `stock_tracking_db.sqlite`에서 KR/US 양쪽 데이터 실시간 조회
- 분석 정확도 + 실매매 성과 기반 A/B/C/D 등급 표시
- 최고 신뢰 트리거 자동 선택

## 대시보드 연동

### 인사이트 탭
`/dashboard?tab=insights` → 트리거 신뢰도 카드에서 상세 데이터 확인

### 메인 탭 미니 배지
대시보드 메인 탭 상단에 "최고 신뢰 트리거: OOO (X등급)" 배지 표시
- 클릭 시 인사이트 탭으로 이동
