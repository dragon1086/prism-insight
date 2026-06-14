# PRISM-BTC v3 핸드오프 (다음 세션 시작점) — 2026-06-12

> 다음 세션은 이 파일만 읽으면 바로 이어갈 수 있다. 상세 이력은 `tasks/v3_backtest_clean.md`(라운드별 전 과정),
> 스펙은 `tasks/v3_strategy_report_v3.md`. 브랜치 `feature/prism-btc-v3`. **e51 등 옛 세션 resume 금지.**

---

## 0.4 운영 배포 완료 (2026-06-15) — db-server 가 단독 운영 호스트
- **db-server(/root/prism-insight) 에서 데모 가동 중.** PR #324 main 머지 → 서버 git pull(525b303d).
  pybit 설치(pyenv `/root/.pyenv/shims/python`), btc_market.db rsync(19MB), 데모키 서버 .env 추가,
  텔레그램 토큰/채널은 서버 기존값(운영채널 -1002373898534) 재사용.
- **서버 cron (crontab 마커 `# PRISM-BTC-START~END`, 기존 54줄 보존)**:
  `1,31 * * * *` shadow / `2,32 * * * *` demo (병행) / `0 18 * * *` telegram(일 18시) /
  `5 18 * * 0` journal --weekly + factory --run. 각 줄 `cd prism-btc && PYTHONPATH= PY -m ...`.
  로그 /root/prism-insight/logs/btc_*.log.
- **로컬 맥 BTC 데몬 전부 정지**(이중 주문 방지): LaunchAgent 4개 bootout +
  ~/Library/LaunchAgents/disabled-btc/ 로 이동. 로컬은 개발/테스트용만, 운영은 서버.
- 검증: 서버 데모 틱 1회 수동 OK(거래소 reconcile equity 9995.09, 에러 0), 텔레그램 DM 전송 200 OK.
- ⚠ 텔레그램은 일 18시 **운영(주식) 채널**로 발송 — 구독자에게 BTC 시범운용 메시지 노출(Rocky 승인).
  빈도/채널 조정 원하면 서버 crontab + .env BTC_TELEGRAM_CHANNEL_ID.
- **실시간 매매 시그널 알림 (PR #325, 3aa150a6)**: live/notifier.py — 진입/비중추가/청산 발생 시
  매 데모틱(30분)에 즉시 텔레그램 알림(ID 마커 멱등, 콜드스타트 가드). runner tick 훅(demo/live만).
  하루 1회 스냅샷(telegram_reporter)과 별개 — 이벤트 기반 즉시 알림. 서버 배포·콜드스타트 마커 0 확인.

## 0.5 배포 토폴로지 (Rocky 확정 2026-06-15 — 반복설명 금지)
- **스케줄링/데몬 베이스 = db-server** (`~/Downloads/vultr_ssh/db-server.sh` 로 접속).
  그 안 `prism-insight` 디렉토리에서 git pull 후 스케줄링·데몬 실행이 기본. BTC 운영은 여기.
- **대시보드 UI = app-server** (`~/Downloads/vultr_ssh/app-server.sh` 접속 → `su - prism` →
  prism-insight 진입 → git pull → `examples/dashboard` 하위 프론트엔드 엔진 실행. 기존 프로세스
  먼저 내림. 프로세스 매니저는 pm2 추정/미확인). 대시보드 데이터 생성은 crontab 으로 도는
  기존 스크립트 참고해 필요시 추가.
- BTC 대시보드(미래)는 한국/미국 주식처럼 examples/dashboard 에 붙인다 (지금은 텔레그램 현황만).
- 현재 BTC 데몬 4개는 아직 **로컬 맥**에서 가동 중 — 운영(db-server) 배포는 안정성+피라미딩 검증 후.

## 0. 절대 규칙 (먹통 사고 3회의 교훈)
- **crontab 명령 금지** (읽기 포함 — macOS에서 stdin/TCC 행 → 봇 전체 먹통). 주기 실행은 LaunchAgent.
- **상주 프로세스를 세션에서 직접 띄우지 말 것.** 데몬은 LaunchAgent `--once` 방식.
- 백테스트는 전부 수 초 내 종료됨. 긴 blocking 명령 금지, timeout 항상.
- venv pandas 버그: 6.5만행+ Series의 rolling 집계가 전부 NaN → numpy sliding_window_view로 우회.

## 1. 현재 상태 (모두 완료·검증·커밋됨)
- **전략 (동결)**: BTCUSDT 단일 스윙 추세추종. score≥70 + 4h ts≥2.0 게이트, 롱/숏 양방향,
  3트랜치(40/30/30) 피라미딩, TP1(1R, 1/3)→BE/트레일 1.5R 활성→**12h MA10 트레일 무제한**(TP2/3 없음),
  레버 8~12x + 변동성천장 lev≤1/(12×ATR_1h), 실펀딩 sign-aware 모델.
- **성적 (6년, 실펀딩)**: risk 4% 기준 CAGR 15.2% / MDD 10.9% / PF 2.46 / RR 2.29 / 승률 54% / 월 1~2회.
  운용 권고 = E4 오버레이 (기본 5%, 계좌 DD>5%시 2.5%, 신고점 복원) → 연 ~18% / MDD ~9%.
  2026 H1 OOS PASS (PF 8.27). ETH/SOL 무수정 교차검증 통과(일반성 증거 — 배포는 BTC만).
- **아키텍처**: `prism-btc/core/`(순수 결정: actions/exits/entries/risk) ← `backtest/`(어댑터1) + `live/`(어댑터2).
  리팩토링 전후 바이트 동일 검증. **169 tests** (`python -m pytest tests -q`, prism-btc/에서).
- **섀도우 페이퍼 데몬 가동 중**: LaunchAgent `com.prism.btc-shadow` (매시 01/31분,
  `python -m live.runner --once`). 가상계좌 $10k, risk 2%. 기록 = 루트 `stock_tracking_db.sqlite`의
  btc_* 테이블 (positions/trading_history/equity_curve/events/meta). 로그 /tmp/btc_shadow.log.
  상태 점검: `sqlite3 stock_tracking_db.sqlite "SELECT ts,kind,message FROM btc_events ORDER BY id DESC LIMIT 5"`
- **데이터**: prism-btc/state/btc_market.db (klines 6TF 2020.3~ + funding 6,806건). 증분갱신은 데몬이 수행.
- **매매일지+부검 파이프라인 가동** (`d089a45a`, 2026-06-12): 트레이드 종결 시 tick 끝에서
  자동 부검. `live/journal.py`(결정적 facts: R분해 자가검증/MFE·MAE in R/스냅샷 재구성/백테스트
  백분위) + `live/postmortem.py`(LLM 게이트웨이: claude CLI, 타임아웃 180s, 실패시 보류·재시도 3회).
  기록 = btc_journal/btc_lessons. 교훈 수명주기 observation→hypothesis→validated — LLM 은 주문경로
  밖, 룰 자동변경 불가. 점검: `python -m live.journal --show 5`. 주간압축: `--weekly` (수동, 아직 미스케줄).
  설계: tasks/btc_journal_design.md. ⚠ 전략 브리프는 engine/config 동적 생성 —
  하드코딩 금지 (부정확 브리프 = 가짜 이상징후 교훈, E2E 실증).
- **자가개선 자동 루프 가동** (`10476138`, 2026-06-12, Rocky 지시 "손 안대고 개선"):
  부검 가설(손잡이 메뉴 {param,value} 구조화: ENTRY_SCORE_MIN/TS_MIN/BE_TRAIL_ACTIVATE_R/
  TRAILING_TF, 범위 울타리) → LaunchAgent `com.prism.btc-research`(일 18:05) 가
  train(2020~24)+OOS(2025~) 이중 게이트로 자동 판정 → 합격시 btc_overrides 자동 활성(슬롯 2)
  → 데몬 tick 시작마다 apply_active 로 실매매 반영 → 주간 재검증 실패시 자동 은퇴(롤백).
  판정 100% 결정적(LLM 무관여), 기각 메모리로 동일 가설 재검증 방지. 챔피언 = 동결 코드 +
  btc_overrides(active). 점검: `python -m research.factory --status`. 테스트 219개.
  설계: tasks/btc_autoloop_design.md. E2E: TS_MIN 2.5 / TRAILING_TF 1d 모두 데이터로 정당 기각 확인.
  메뉴 밖 가설(구조 변경)은 observation 으로 격리 = 유일한 사람 리뷰 지점 (자동 반영 절대 불가).

## 1.8 데모 실주문 가동 (2026-06-14, Rocky 데모키 발급 완료)
- **Bybit 데모 실주문 어댑터 LIVE**: `live/demo.py` DemoAdapter — 섀도우와 **병행** 가동
  (섀도우=이론 가상체결, 데모=거래소 실체결 → 괴리 측정). 거래소가 진실(reconcile):
  get_wallet_balance/positions/executions 로 동기화, mode='demo' 로 btc_* 기록.
  접속 `HTTP(demo=True)` (★ testnet 아님, api-demo.bybit.com), 키 .env BYBIT_DEMO_API_KEY/SECRET
  ($10k USDT 충전됨, 출금권한 없음). 키 로드는 demo.py 가 .env 직접 load_dotenv.
- **운영 LaunchAgent 4개 가동**: com.prism.btc-shadow(01/31분, 가상) +
  com.prism.btc-demo(02/32분, 실주문) + com.prism.btc-research(일 18:05, 자가개선) +
  com.prism.btc-telegram(4시간마다, 현황). 전부 --once, crontab 금지 준수.
- **데모 피라미딩 완료 (2026-06-15)**: ① reconcile 안정성 점검 OK(연속 틱 에러 0) ②
  3트랜치(40/30/30) 피라미딩 데모 추가 — 거래소는 통합 단일포지션(평균단가), 트랜치는
  로컬 장부(btc_positions[demo] 트랜치별 행)로 관리, size 증분으로 체결 감지, SL/TP 전체
  수량 기준 재발행 ③ 테스트 241개 통과(다중트랜치 7건 포함). **남은 것: 운영서버(db-server)
  배포 — git pull 후 LaunchAgent 가동.** 배포는 §0.5 절차. 아직 로컬에서만 가동 중.
- **텔레그램 현황 리포터**: live/telegram_reporter.py — 일반인 한국어(롱/숏/R/PF/섀도우 용어
  전부 제거, "상승베팅/하락베팅·배수·이익/손실"로 풀어씀). 채널은 기존 주식 채널 그대로 사용
  (Rocky 결정). 주식 운영 채널 ID = -1002373898534, 상록 DM = 7726642089.
  **배포 모델 (Rocky 확인)**: 로컬 .env = 테스트 채널 + placeholder 토큰 (그래서 로컬에선
  전송 안 됨, 정상). 진짜 운영 토큰/채널은 운영서버에 설정됨. 리포터는 환경별로
  TELEGRAM_BOT_TOKEN + 채널(BTC_TELEGRAM_CHANNEL_ID > TELEGRAM_CHANNEL_ID)을 자동으로 집으므로,
  운영서버에 배포되면 그 서버의 실제 자격증명으로 자동 전송됨. 로컬은 테스트 채널로 안전 검증.
  운영 채널 ID = -1002373898534 (주식 운영채널 그대로 사용 — Rocky 결정). 코드 변경 불요.
  **로컬 테스트 전송 가동(2026-06-15)**: 주식 봇 토큰(~/prism-docs/prism-daily-card.py 하드코딩,
  TELEGRAM_BOT_TOKEN) 을 로컬 .env(gitignore됨)에 복사 + BTC_TELEGRAM_CHANNEL_ID=7726642089(상록 DM,
  안전 테스트 타깃, 구독자 채널 아님). 리포터 수동 전송 200 OK 확인 — DM 수신됨.
  com.prism.btc-telegram(4h)가 이제 DM 으로 자동 전송. 운영 배포 시 db-server 는 채널을
  -1002373898534 로(BTC_TELEGRAM_CHANNEL_ID 또는 TELEGRAM_CHANNEL) 설정. (참고: TelegramSender
  루트모듈 임포트는 prism-btc cwd 에서 실패 → 직접 Bot.send_message 폴백으로 정상 전송, 무해.)
- 테스트 234개 (FakeExchange 모킹, 네트워크 0, "출금호출 0" assert 포함). 스펙: tasks/btc_demo_adapter_spec.md.

## 1.9 데이터 인벤토리 (운영 감사/개선의 원천 — 전부 루트 stock_tracking_db.sqlite)
btc_trading_history(종결 전체) / btc_positions(현재) / btc_equity_curve / btc_events(틱·에러·주문·부검·연구 전수) /
btc_meta(트래커+code_version) / btc_journal(facts+LLM부검) / btc_lessons(교훈 수명주기) /
btc_overrides(챔피언 채택·은퇴+증거) / btc_research_runs(공장 판정 전수) /
**btc_signal_log(4h 신호평가 전수 — 기각 포함, 2026-06-13 추가)**.
코드 버전은 변경 시 btc_events kind='version' 기록. /tmp 로그는 휘발 — 영구 기록은 DB가 정본.
"운영로그와 DB 보고 개선" 세션은 이 테이블들 + tasks/*.md 만 읽으면 전체 재구성 가능.

## 2. 주요 커밋 (feature/prism-btc-v3)
`7b465f2f` 라운드4 첫 전구간 합격 → `03a43caa` 라운드6 TP사다리 제거(RR 2.29) →
`8d984273` 실펀딩 모델 → `cc957ba0` core 추출 리팩토링 → `c4d8b418` 섀도우 데몬 →
`81108fce` 리스크 프론티어 → `ef34da72`/`088aca57` 자율루프 기각 기록.

## 3. 다음 작업 (우선순위순)
1. ~~매매일지+부검 자가개선 파이프라인~~ ✅ **완료** (`d089a45a` — §1 참조). 남은 후속:
   - 첫 실 섀도우 트레이드 종결 후 부검 품질 확인 (`--show`)
   - 가설 백로그(btc_lessons status='hypothesis') → 백테스트 검증 루프 (연구공장)
   - `--weekly` 주간압축 LaunchAgent 등록 (수동 1회 검증 후)
1.5 **⚠ E4 오버레이 재검토 필요** (2026-06-13 발견): 전체 재시뮬(evaluate_entry 래핑,
   진입시점 실현 equity 기준 peak)에서 E4 가 고정 리스크보다 크게 열위 —
   4%+E4(2%): CAGR 7.8/MDD 10.7 vs 고정 4%: 16.0/13.9. 원인: risk≥4%에선 손실
   1~2회만에 DD>5% 트리거 → 회복 구간 대부분을 반토막 리스크로 주행 (추세전략의
   큰 승리는 손실 직후에 옴). §1 "E4→연~18%/MDD~9%" 권고는 재산정 방법 차이로
   추정 — 라이브 전 반드시 재결정. 고정 리스크 프론티어(신뢰): 4%=16.0/13.9,
   5%=19.6/17.1, 6%=23.0/20.2 (전구간 청산접근 0). **→ 결정 완료 (Rocky, 2026-06-13):
   섀도우 E4 비활성 (SHADOW_REDUCED_RISK=base, 고정 2%)** — 섀도우 2%/1% 실측도
   열위 확인 (CAGR 8.3→6.0, MDD 7.15→6.1). 검증 데이터가 라이브 의도 설정(고정
   리스크)과 일치하도록 종결 트레이드 0건인 지금 전환. 라이브 계획: 고정 3% 시작
   → 검증 후 고정 5%.
2. **이벤트 리스크 게이트 + 보유 중 위협 감시** (Rocky 관심 확인):
   - 1단계: FOMC/CPI 정기 이벤트 블랙아웃을 과거 캘린더로 백테스트 (룰로 검증 가능, LLM 불요)
   - 2단계: firecrawl/perplexity(주식 시스템 인프라 재사용)로 비정기 이벤트 — LLM 판단은 3개월 섀도우 수집 후 검증
   - 보유 중 시간당 뉴스 스캔 → 비상청산 권고는 텔레그램 원터치 승인 구조
3. **Bybit 데모 실주문 어댑터**: Rocky가 데모 API 키 주면 (가입 예정이라 했음, 신분증 필요).
   shadow.py 가상체결 → ExecutionAdapter 스왑. post-only fill-chaser + SL만 시장가. equity는 거래소 잔고 복원.
4. 괴리 감시자 (섀도우 데이터 2~3주 후): 실적 vs 백테스트 기대분포 비교, 드리프트 경보.
5. 전략 리포트 v3는 전송 완료. 페이퍼 4주 후 라이브 소액 (risk 2~3% → 검증 후 4%+E4).

## 4. 기각 가설 (재시도 금지 — 전부 데이터로 도태됨)
인트라데이 30m 돌파 / 오닐 1d 와이드스탑(S/N<1: 14d 엣지 2.3% < 일노이즈 3.5%) / score 85·55 /
ts 1.0·양TF 게이트 / 레버 12~18x / 양방향 헤지(Rocky 폐기) / 트랜치 재배분 30/30/40 /
트레일 2.0R 활성 / 숏 펀딩수취 가설 / 펀딩 극단값 알파(비단조) / 게이트 완화 전반(빈도↑=MDD↑ 수익→).
LLM을 주문 경로의 진입/청산 판단에 넣는 것도 금지 (검증 불가) — LLM은 사이즈 게이트/부검/연구공장 역할만.
진입평가 30m 상시화(2026-06-12, Rocky 빈도 직감 재검증): 트레이드 158→200,
CAGR 16.0→13.8, MDD 13.9→26.4 — 미확정 4h봉 중간 스파이크 추격 = 노이즈 진입.
재현: backtest.engine.ENTRY_EVAL_EVERY_BAR=True (연구 훅, 기본 False=동결과 동일).

## 5. Rocky 컨텍스트
- 목표 월 1~10% — risk 4~6%+오버레이로 도달권이라고 설명됨. 빈도 욕구 있었으나 데이터로 납득
  (게이트 완화 = 수익 동일·MDD 2.5배 실측). 용어는 트레이더 친화로 (CAGR/MDD/승률/손익비/비중).
- 멀티에셋은 보류 카드 (BTC only 결정). 모의계좌(데모) 신청 예정.
