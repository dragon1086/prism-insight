# PRISM-BTC v3 핸드오프 (다음 세션 시작점) — 2026-06-12

> 다음 세션은 이 파일만 읽으면 바로 이어갈 수 있다. 상세 이력은 `tasks/v3_backtest_clean.md`(라운드별 전 과정),
> 스펙은 `tasks/v3_strategy_report_v3.md`. 브랜치 `feature/prism-btc-v3`. **e51 등 옛 세션 resume 금지.**

---

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
- **데이터**: prism-btc/state/market.db (klines 6TF 2020.3~ + funding 6,806건). 증분갱신은 데몬이 수행.
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
   5%=19.6/17.1, 6%=23.0/20.2 (전구간 청산접근 0). 섀도우는 현재 E4(2%/1%) 가동중
   — 검증 데이터라 유지, 데모 전환 시 결정.
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
