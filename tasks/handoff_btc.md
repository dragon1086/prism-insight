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

## 2. 주요 커밋 (feature/prism-btc-v3)
`7b465f2f` 라운드4 첫 전구간 합격 → `03a43caa` 라운드6 TP사다리 제거(RR 2.29) →
`8d984273` 실펀딩 모델 → `cc957ba0` core 추출 리팩토링 → `c4d8b418` 섀도우 데몬 →
`81108fce` 리스크 프론티어 → `ef34da72`/`088aca57` 자율루프 기각 기록.

## 3. 다음 작업 (우선순위순)
1. **매매일지+부검 자가개선 파이프라인** (Rocky 승인됨, 착수 직전이었음):
   - 섀도우 트레이드 종결 시 자동 부검(LLM): 백테스트 기대 vs 실제 (체결, MFE 경로, 슬리피지)
   - btc_journal / btc_lessons 테이블 (주식 시스템 trading_journal/intuitions 패턴 포팅)
   - 주간 기억압축 → **가설 백로그** → 백테스트 검증 통과한 것만 룰 반영 (교훈이 동결 룰을 직접 바꾸는 것 금지)
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

## 5. Rocky 컨텍스트
- 목표 월 1~10% — risk 4~6%+오버레이로 도달권이라고 설명됨. 빈도 욕구 있었으나 데이터로 납득
  (게이트 완화 = 수익 동일·MDD 2.5배 실측). 용어는 트레이더 친화로 (CAGR/MDD/승률/손익비/비중).
- 멀티에셋은 보류 카드 (BTC only 결정). 모의계좌(데모) 신청 예정.
