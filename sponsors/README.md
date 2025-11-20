# GitHub Sponsors Telegram Request System

텔레그램 채널에 GitHub Sponsors 후원 요청 메시지를 전송하는 시스템입니다.

## 📋 개요

"커피 한 잔" 전략을 기반으로 한 투명하고 압박 없는 후원 요청 시스템입니다.

### 핵심 원칙

✅ **투명성**: 실제 운영 비용 정직하게 공개
✅ **평등성**: 모든 기능은 후원 여부와 관계없이 무료
✅ **감사**: 후원은 "구매"가 아닌 "응원"
❌ **압박 금지**: 과도한 요청 금지
❌ **차별 금지**: 후원자/비후원자 기능 차이 없음

## ⚠️ 중요: 실행 주기

**권장 주기: 월 1-2회**

- ❌ **하루 1번**: 너무 자주 → 압박으로 느껴질 수 있음
- ⚠️ **주 1번**: 여전히 자주 → 신중하게 고려
- ✅ **월 1-2번**: 가장 적절 → 자연스러운 알림

## 📁 파일 구조

```
sponsors/
├── telegram_sponsor_request.py    # 메인 스크립트
├── README.md                       # 이 파일
├── sponsor_request.log            # 실행 로그 (자동 생성)
└── crontab.example                # crontab 설정 예제 (아래 참조)
```

## 🚀 사용 방법

### 1. 환경 변수 설정

`.env` 파일에 다음 변수가 설정되어 있어야 합니다:

```bash
# 메인 채널 (한국어)
TELEGRAM_BOT_TOKEN="your_bot_token_here"
TELEGRAM_CHANNEL_ID="-1001234567890"

# 다국어 채널 (선택사항)
TELEGRAM_CHANNEL_ID_EN="-1001234567891"
TELEGRAM_CHANNEL_ID_JA="-1001234567892"
TELEGRAM_CHANNEL_ID_ZH="-1001234567893"
```

### 2. 메시지 타입

#### Full (전체 상세 메시지)
**권장 주기**: 월 1회

운영 비용 상세 내역, 커뮤니티 현황, 후원 티어 등 모든 정보를 포함합니다.

```bash
python sponsors/telegram_sponsor_request.py --type full
```

#### Simple (간단한 리마인더)
**권장 주기**: 월 1-2회

간결한 후원 안내 메시지입니다. 4가지 템플릿이 랜덤으로 선택되어 반복을 피합니다.

```bash
python sponsors/telegram_sponsor_request.py --type simple
```

#### Monthly (월간 리포트)
**권장 주기**: 월 1회 (매월 1일)

당월 성과, 운영 현황, 후원 안내를 포함한 종합 리포트입니다.

```bash
python sponsors/telegram_sponsor_request.py --type monthly
```

### 3. 테스트 실행 (Dry Run)

실제로 전송하지 않고 메시지 미리보기:

```bash
python sponsors/telegram_sponsor_request.py --type simple --dry-run
```

### 4. 다국어 방송

여러 언어 채널에 동시 전송:

```bash
python sponsors/telegram_sponsor_request.py --type simple --broadcast-languages en,ja,zh
```

## 🕐 Crontab 설정

### 권장 설정 (월 1-2회)

#### 옵션 1: 월 1회 (가장 권장)

```cron
# 매월 1일 오전 10시에 월간 리포트 전송
0 10 1 * * cd /home/user/prism-insight && python3 sponsors/telegram_sponsor_request.py --type monthly >> sponsors/sponsor_request.log 2>&1
```

#### 옵션 2: 월 2회

```cron
# 매월 1일 오전 10시에 월간 리포트 전송
0 10 1 * * cd /home/user/prism-insight && python3 sponsors/telegram_sponsor_request.py --type monthly >> sponsors/sponsor_request.log 2>&1

# 매월 15일 오전 10시에 간단한 리마인더 전송
0 10 15 * * cd /home/user/prism-insight && python3 sponsors/telegram_sponsor_request.py --type simple >> sponsors/sponsor_request.log 2>&1
```

### 선택사항 (주 1회)

⚠️ 주의: 너무 자주 전송하면 압박으로 느껴질 수 있습니다.

```cron
# 매주 일요일 오전 10시에 간단한 리마인더 전송
0 10 * * 0 cd /home/user/prism-insight && python3 sponsors/telegram_sponsor_request.py --type simple >> sponsors/sponsor_request.log 2>&1
```

### Crontab 등록 방법

```bash
# Crontab 편집기 열기
crontab -e

# 위의 설정 중 하나를 복사하여 붙여넣기
# 저장하고 나가기 (:wq)

# 등록된 crontab 확인
crontab -l
```

## 📊 메시지 내용

### 투명한 운영 비용 공개

모든 메시지에는 실제 월간 운영 비용이 포함됩니다:

- **OpenAI API (GPT-4.1, GPT-5)**: ₩125,000/월
- **Anthropic API (Claude Sonnet 4)**: ₩42,000/월
- **서버 및 인프라**: ₩20,000/월
- **기타 (도메인, 모니터링 등)**: ₩13,000/월
- **총 비용**: ₩200,000/월

### 핵심 메시지

모든 메시지에서 강조되는 내용:

1. **모든 기능은 앞으로도 계속 무료입니다**
2. 후원은 서비스 지속을 위한 응원일 뿐
3. 기능 차이를 만들지 않음
4. 커피 한 잔($5)부터 시작 가능

### Simple 메시지 템플릿

4가지 템플릿이 랜덤으로 선택되어 반복을 피합니다:

1. **비용 중심**: 월간 운영 비용과 현재 후원자 수
2. **커뮤니티 중심**: 사용자 수와 함께하는 느낌
3. **성과 중심**: 누적 수익률과 서비스 품질
4. **개인 스토리**: 육아와 운영 병행 이야기

## 🔧 스크립트 커스터마이징

### 운영 비용 업데이트

`telegram_sponsor_request.py` 파일의 `MONTHLY_COSTS` 딕셔너리를 수정:

```python
MONTHLY_COSTS = {
    "openai_api": 125000,      # OpenAI GPT-4.1, GPT-5
    "anthropic_api": 42000,    # Anthropic Claude Sonnet 4
    "server_infra": 20000,     # 서버 및 인프라
    "misc": 13000,             # 기타
}
```

### 커뮤니티 현황 업데이트

메시지 생성 함수 내의 숫자들을 업데이트:

```python
message += "• 텔레그램 구독자: 약 450명\n"
message += "• 활성 사용자: 약 100명\n"
message += "• 현재 후원자: 8명\n\n"
```

### URL 업데이트

스크립트 상단의 상수들을 수정:

```python
GITHUB_SPONSORS_URL = "https://github.com/sponsors/dragon1086"
TELEGRAM_CHANNEL_URL = "https://t.me/stock_ai_agent"
GITHUB_REPO_URL = "https://github.com/dragon1086/prism-insight"
DASHBOARD_URL = "https://analysis.stocksimulation.kr/"
```

## 📝 로그 확인

실행 로그는 자동으로 기록됩니다:

```bash
# 최근 로그 확인
tail -f sponsors/sponsor_request.log

# 전체 로그 확인
cat sponsors/sponsor_request.log

# 오류만 필터링
grep ERROR sponsors/sponsor_request.log
```

## 🎯 베스트 프랙티스

### DO ✅

1. **투명하게**: 실제 비용 정직하게 공개
2. **감사하게**: 매번 진심으로 감사 표현
3. **평등하게**: 모든 사용자에게 동일한 서비스
4. **꾸준하게**: 월 1-2회 가볍게 언급
5. **진정성 있게**: 개인 스토리 솔직히 공유

### DON'T ❌

1. **압박하지 마세요**: "후원 안 하면..." 식 멘트 금지
2. **차별하지 마세요**: 후원자만 기능 제공 금지
3. **과다 약속하지 마세요**: 지킬 수 없는 로드맵 제시 금지
4. **너무 자주 언급하지 마세요**: 주 2회 이상 언급 금지
5. **개인정보 공개하지 마세요**: 후원자 동의 없이 정보 공개 금지

## 🔍 트러블슈팅

### 메시지가 전송되지 않음

```bash
# 환경 변수 확인
cat .env | grep TELEGRAM

# 수동 테스트
python sponsors/telegram_sponsor_request.py --type simple --dry-run

# 로그 확인
tail -f sponsors/sponsor_request.log
```

### Crontab이 실행되지 않음

```bash
# Crontab 목록 확인
crontab -l

# Cron 서비스 상태 확인
sudo systemctl status cron   # Ubuntu/Debian
sudo systemctl status crond  # CentOS/Rocky

# Cron 로그 확인
grep CRON /var/log/syslog   # Ubuntu/Debian
grep CRON /var/log/cron     # CentOS/Rocky
```

### Python 경로 오류

```bash
# Python 경로 확인
which python3

# Crontab에서 절대 경로 사용
0 10 1 * * cd /home/user/prism-insight && /usr/bin/python3 sponsors/telegram_sponsor_request.py --type monthly
```

## 📈 예상 효과

### 보수적 시나리오 (3개월)

- 텔레그램 구독자: 450명
- 전환율: 3%
- 후원자: 13명
- 월 수익: $95 (약 ₩133,000)
- 목표 달성: 67%

### 낙관적 시나리오 (6개월)

- 텔레그램 구독자: 600명
- 전환율: 4%
- 후원자: 24명
- 월 수익: $185 (약 ₩259,000)
- 목표 달성: 130%

## 📞 문의 및 피드백

- GitHub Issues: https://github.com/dragon1086/prism-insight/issues
- Telegram: @stock_ai_ko
- Email: (필요시 추가)

---

**작성일**: 2025-11-20
**버전**: 1.0
**작성자**: dragon1086 with Claude Code
