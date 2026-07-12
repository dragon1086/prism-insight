# US 2회 분석 배치 — 검증·배포 체크리스트

> 작성일: 2026-07-13 · 범위: US 분석 `morning` + `afternoon` 2회, Market Pulse 정책 정렬

## 1. 로컬 게이트 (배포 전 필수)

- [ ] `python -m pytest tests/test_regime_policy.py -q` 통과
  - US `CORRECTION`: morning 휴식, afternoon 실행
  - US `UNDER_PRESSURE`: morning/afternoon 모두 실행
- [ ] `python -m pytest prism-us/tests/test_phase7_orchestrator.py -q` 통과
- [ ] `python prism-us/us_stock_analysis_orchestrator.py --force --help`가
  `morning, afternoon, both`만 표시
- [ ] 제거된 모드 인수는 argparse가 거부(exit 2)
- [ ] `bash -n utils/setup_us_crontab.sh`, `python -m compileall` 및
  `git diff --check` 통과
- [ ] `docker/crontab` 및 설치 스크립트에 US 분석 명령이 정확히 두 줄인지 확인

## 2. db-server 사전점검 (읽기 전용)

- [ ] SSH 후 `/root/prism-insight`에서 `git branch --show-current` = `main`
- [ ] `git status --short`를 기록하고, 의도하지 않은 변경이 있으면 pull/cron 변경을 중단
- [ ] 배포 대상 커밋과 `git log -1 --format=%H`를 기록
- [ ] `crontab -l`을 백업하고 US 분석·hardstop·trend-exit·fill-chaser 행을 별도 보관
- [ ] `.env`의 `MARKET_PULSE_MODE`와 고빈도 루프 enable/live 플래그를 읽기 전용으로 확인

## 3. 배포 및 cron 변경

- [ ] 병합된 `main`에서만 `git pull --ff-only origin main`
- [ ] US 장중 분석 cron 한 줄과 해당 로그 경로만 제거
- [ ] US morning/afternoon 분석 cron 두 줄은 유지
- [ ] hardstop, trend-exit, fill-chaser cron 행은 byte-for-byte 변경하지 않음
- [ ] 변경 후 `crontab -l`에서 `--mode morning` 1줄, `--mode afternoon` 1줄,
  제거된 모드 0줄인지 확인
- [ ] `git branch --show-current`, `git rev-parse HEAD`, `git status --short`를 다시 확인

## 4. 첫 실행 관찰

- [ ] 다음 US 오전 실행 로그에 `[MARKET_PULSE]`와 `batch=morning`이 기록되는지 확인
- [ ] 다음 US 오후 실행 로그에 `[MARKET_PULSE]`와 `batch=afternoon`이 기록되는지 확인
- [ ] UNDER_PRESSURE일 때 두 분석이 실행되고, CORRECTION일 때 오전만 LIVE 휴식하는지 확인
- [ ] 같은 거래일에 hardstop/trend-exit/fill-chaser 로그가 계속 생성되는지 확인
- [ ] argparse 오류, 신규 Telegram 휴식 공지 오류, 누락된 결과 파일이 없는지 확인

## 5. 롤백 조건과 절차

즉시 롤백: cron에 제거된 모드가 남아 CLI 오류가 반복되거나, morning/afternoon 중 하나가
누락되거나, 매도 루프 행이 변경된 경우.

1. 코드와 cron을 같은 직전 `main` 상태로 되돌린다.
2. 이전 장중 cron을 복원해야 하는 구 코드라면 해당 cron도 함께 복원한다.
3. `crontab -l`, `git branch --show-current`, `git rev-parse HEAD` 및 다음 실행 로그로
   복구를 확인한다.
