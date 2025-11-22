# YouTube Event Fund Crawler - 전인구경제연구소 역발상 투자 분석

## 📋 개요

유튜브 채널 '전인구경제연구소'의 신규 영상을 모니터링하여 자동으로 분석하고, **역발상(Contrarian) 투자 전략**을 제안하는 시스템입니다.

### 핵심 아이디어

전인구의 시장 전망과 **반대 방향**으로 베팅하는 역발상 전략:
- 전인구가 상승을 전망하면 → 하락에 베팅 (인버스 ETF 추천)
- 전인구가 하락을 전망하면 → 상승에 베팅 (레버리지 ETF 추천)

---

## 🔄 워크플로우

```
1. RSS 피드에서 최신 영상 목록 가져오기
   ↓
2. 이전 영상 리스트와 비교하여 신규 영상 확인
   ↓
3. 신규 영상 발견 시:
   - yt-dlp로 오디오 추출
   - OpenAI Whisper API로 자막 생성
   ↓
4. AI Agent를 통한 콘텐츠 분석:
   - 전인구 본인 출연 영상인지 판별
   - 시장 기조 분석 (상승/하락/중립)
   - 역발상 투자 전략 제안
   ↓
5. 결과를 로그 및 파일로 출력
   (추후: 자동매매 연동 예정)
```

---

## 🛠️ 설치 및 설정

### 1. 필수 요구사항

#### Python 라이브러리 설치
```bash
pip install -r requirements.txt
```

새로 추가된 라이브러리:
- `yt-dlp`: YouTube 영상/오디오 다운로드
- `feedparser`: RSS 피드 파싱

#### FFmpeg 설치 (오디오 변환용)

**Ubuntu/Debian:**
```bash
sudo apt update
sudo apt install ffmpeg
```

**macOS:**
```bash
brew install ffmpeg
```

**Rocky Linux/CentOS:**
```bash
sudo dnf install ffmpeg
```

### 2. API 키 설정

`mcp_agent.secrets.yaml` 파일에 OpenAI API 키가 설정되어 있는지 확인:

```yaml
# mcp_agent.secrets.yaml
openai:
  api_key: "sk-..."

anthropic:
  api_key: "sk-ant-..."
```

파일이 없다면 example 파일을 복사하여 생성:
```bash
cp mcp_agent.secrets.yaml.example mcp_agent.secrets.yaml
# 그 후 파일을 편집하여 API 키 입력
```

### 3. 실행 권한 부여

```bash
chmod +x youtube_event_fund_crawler.py
```

---

## 🚀 사용법

### 기본 실행

```bash
python youtube_event_fund_crawler.py
```

### 동작 방식

#### 첫 실행
- 채널의 최신 영상 목록을 가져와서 `youtube_video_history.json`에 저장
- 새로운 영상이 없으므로 분석하지 않음

#### 두 번째 이후 실행
- 새로운 영상이 올라온 경우 자동으로 감지
- 오디오 추출 → 자막 생성 → AI 분석 수행
- 결과를 콘솔 및 파일로 출력

### 출력 파일

스크립트 실행 시 `events/` 디렉토리에 다음 파일들이 생성됩니다:

1. **`events/youtube_video_history.json`**
   - 채널의 영상 목록 히스토리
   - 다음 실행 시 비교 기준으로 사용

2. **`events/transcript_{video_id}_{timestamp}.txt`**
   - Whisper API로 생성된 자막 원문
   - 디버깅 및 검토용

3. **`events/analysis_{video_id}_{timestamp}.md`**
   - AI Agent의 분석 결과
   - 역발상 투자 전략 포함

4. **`events/youtube_crawler_{date}.log`**
   - 실행 로그 파일

5. **`events/temp_audio.mp3`**
   - 임시 오디오 파일 (처리 후 자동 삭제)

---

## 📊 분석 결과 예시

```markdown
# 전인구경제연구소 역발상 투자 분석

## 📺 영상 정보
- **제목**: 📉 코스피 2,400 붕괴 임박! 지금 매도하세요
- **게시일**: 2025-11-22T09:00:00Z
- **URL**: https://www.youtube.com/watch?v=...

## 1️⃣ 콘텐츠 유형 판별
전인구 본인 의견

## 2️⃣ 시장 기조 분석
**판단**: 하락

**근거**:
- "코스피가 2,400선을 깨는 것은 시간문제입니다"
- "당분간 시장은 하락 압력을 받을 것으로 보입니다"
- "지금은 현금 비중을 높이고 관망하는 것이 유리합니다"

## 3️⃣ 영상 내용 요약
- 미국 금리 인상 우려로 외국인 자금 유출 가속화
- 국내 반도체 업종의 실적 부진 전망
- 환율 상승으로 수입 물가 부담 증가

## 4️⃣ 역발상 투자 전략
### 추천 포지션: 매수 (상승 베팅)

### 추천 종목/상품
1. **KODEX 레버리지 (122630)**
   - 유형: ETF
   - 이유: 코스피 상승 시 2배 수익

2. **TIGER 레버리지 (233740)**
   - 유형: ETF
   - 이유: 유동성 높은 레버리지 상품

3. **삼성전자 (005930)**
   - 유형: 개별주
   - 이유: 반도체 실적 부진 우려 과도, 반등 가능성

### 진입 전략
- 타이밍: 단기 조정 시 분할 매수
- 분할매수 권장: 3회 분할 (1/3씩)

## 5️⃣ 리스크 관리
- ⚠️ 손절매: -7% 도달 시 무조건 청산
- ⚠️ 포지션 크기: 전체 자산의 10% 이하로 제한
- ⚠️ 전인구 의견이 맞을 경우 예상 손실: 레버리지 상품 특성상 -14% 이상 가능
```

---

## 🔁 자동화 설정 (Crontab)

매일 자동으로 실행하려면 crontab에 등록:

```bash
crontab -e
```

다음 라인 추가 (매일 오전 9시 실행):
```cron
0 9 * * * cd /home/user/prism-insight && /usr/bin/python3 youtube_event_fund_crawler.py >> youtube_crawler_cron.log 2>&1
```

---

## 🎯 향후 계획

### Phase 1: 모니터링 및 분석 (현재)
- ✅ RSS 피드 모니터링
- ✅ 자막 자동 생성
- ✅ AI 기반 콘텐츠 분석
- ✅ 역발상 투자 전략 제안

### Phase 2: 자동매매 연동 (예정)
- 🔲 KIS API 연동
- 🔲 추천 종목 자동 매수/매도
- 🔲 포지션 크기 자동 조절
- 🔲 손절매 자동 실행

### Phase 3: 백테스팅 및 최적화 (예정)
- 🔲 과거 영상 데이터로 전략 검증
- 🔲 승률 및 수익률 분석
- 🔲 최적 진입/청산 타이밍 연구

### Phase 4: Telegram 알림 (예정)
- 🔲 신규 영상 감지 시 알림
- 🔲 분석 결과 자동 전송
- 🔲 매매 체결 알림

---

## ⚠️ 면책 조항

본 시스템은 **교육 및 연구 목적**으로 제작되었습니다.

- 역발상 투자는 **고위험** 전략입니다
- 레버리지/인버스 상품은 손실 확대 가능성이 있습니다
- 분석 결과는 참고용이며, 투자 권유가 아닙니다
- 투자 결정 및 손실에 대한 책임은 투자자 본인에게 있습니다

**실전 투자 전 반드시**:
1. 충분한 백테스팅 수행
2. 소액으로 전략 검증
3. 리스크 관리 규칙 준수

---

## 🛠️ 트러블슈팅

### 문제 1: FFmpeg not found 에러

**증상**:
```
Error: ffmpeg not found
```

**해결**:
```bash
# Ubuntu/Debian
sudo apt install ffmpeg

# macOS
brew install ffmpeg

# Rocky Linux/CentOS
sudo dnf install ffmpeg
```

### 문제 2: OpenAI API 에러

**증상**:
```
Error: OPENAI_API_KEY not found or not configured in mcp_agent.secrets.yaml
```

**해결**:
`mcp_agent.secrets.yaml` 파일에 API 키 설정:
```yaml
openai:
  api_key: "sk-..."
```

파일이 없다면:
```bash
cp mcp_agent.secrets.yaml.example mcp_agent.secrets.yaml
# 파일을 편집하여 실제 API 키 입력
```

### 문제 3: RSS 피드 파싱 실패

**증상**:
```
Error fetching RSS feed
```

**해결**:
- 인터넷 연결 확인
- YouTube 채널 URL 확인
- 방화벽 설정 확인

### 문제 4: 오디오 추출 실패

**증상**:
```
Error extracting audio
```

**해결**:
- FFmpeg 설치 확인
- 디스크 공간 확인
- YouTube 영상 접근 가능 여부 확인

---

## 📞 지원

- **GitHub Issues**: [문제 보고](https://github.com/dragon1086/prism-insight/issues)
- **Telegram**: @stock_ai_ko
- **문서**: [CLAUDE.md](CLAUDE.md)

---

**Last Updated**: 2025-11-22
**Version**: 1.0
**Author**: PRISM-INSIGHT Development Team
