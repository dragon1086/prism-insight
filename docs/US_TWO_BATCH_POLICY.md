# US 2회 분석 배치 정책 및 검증 런북

> 최종 갱신: 2026-07-13. 이 문서는 v2.18.0의 Market Pulse 정책을 운영 관찰 결과에 맞춰 후속 정리한 것이다.

## 정책

US 분석 배치는 `morning`과 `afternoon` 두 개만 사용한다. 기존 장중 분석 배치는 제거한다.

- `UPTREND`: 오전 + 오후 실행
- `UNDER_PRESSURE`: 오전 + 오후 실행
- `CORRECTION`: 오전 휴식, 오후만 실행
- 알 수 없는 Pulse 상태: fail-open으로 해당 배치 실행

`UNDER_PRESSURE`에서 오전을 쉬면 CORRECTION과 같은 하루 1회가 되어 상태별 실행 효과가 중복된다. 따라서 이 상태는 매수 품질 게이트의 신호로만 유지하고 분석 빈도를 줄이지 않는다.

## 리스크 대응 범위

분석 빈도 축소는 신규 후보 탐색에만 적용한다. 아래 매도/주문 관리 루프는 Market Pulse 상태와 무관하게 기존 스케줄을 유지한다.

- hardstop: 약 10분 주기
- trend-exit: 약 10분 주기와 마감 구간
- fill-chaser: 약 2분 주기

이 루프는 장중 하락 감지 지연을 줄이지만, 다음 정규장 시초가의 갭 변동을 사전에 없애지는 못한다. KR과 US 모두 동일한 제한이므로, CORRECTION의 오후 확인 원칙을 두 시장에 동일하게 적용한다.

## 코드와 cron 계약

- `prism-us/us_stock_analysis_orchestrator.py --mode`는 `morning`, `afternoon`, `both`만 받는다.
- `docker/crontab` 및 `utils/setup_us_crontab.sh`는 US 분석 두 줄만 생성한다.
- 운영 db-server의 실제 crontab은 별도 배포 단계에서 기존 장중 실행 한 줄을 삭제해야 한다. 이 저장소 변경만으로 이미 설치된 crontab은 바뀌지 않는다.
- 실제 db-server의 현재 cron은 오전 10:15 ET, 오후 15:10 ET다. `utils/setup_us_crontab.sh`의
  범용 설치 템플릿은 오후 16:30 ET를 사용하므로, 새 설치나 재설치 시에는 대상 서버의 cron 시간을
  별도로 검토해야 한다. DST에 따라 KST 시각만 달라진다.

## 검증 계획

### 로컬 변경 전후 검증

1. `tests/test_regime_policy.py`로 상태 × 시장 × 배치 정책을 검증한다.
   - US `CORRECTION/morning=False`, `CORRECTION/afternoon=True`
   - US `UNDER_PRESSURE/morning=True`, `UNDER_PRESSURE/afternoon=True`
2. `prism-us/tests/test_phase7_orchestrator.py`로 분석 파이프라인의 tracking 실패 로그가 여전히 정확한지 확인한다.
3. `python prism-us/us_stock_analysis_orchestrator.py --help` 출력에서 두 개의 개별 배치 모드만 허용되는지 확인한다.
4. `bash -n utils/setup_us_crontab.sh`, `git diff --check`, 그리고 저장소 검색으로 제거된 장중 실행 경로가 남지 않았는지 확인한다.

### 배포 직후 읽기 전용 검증

1. db-server에서 `git branch --show-current`이 `main`이고, `git rev-parse HEAD`가 배포 커밋과 일치하는지 확인한다.
2. `crontab -l`에서 US 분석은 `--mode morning`, `--mode afternoon` 두 줄만 남고, 제거된 장중 명령·로그 파일명은 없는지 확인한다.
3. `.env`의 `MARKET_PULSE_MODE=live`를 확인한 뒤, 다음 US 오전/오후 실행 로그에서 `[MARKET_PULSE]` 결정과 모드가 일치하는지 확인한다.
4. 다음 CORRECTION 및 UNDER_PRESSURE 관측 시에는 정책 로그·휴식 공지·실행 횟수를 위 표와 대조한다. 매도 루프 로그가 계속 생성되는지도 별도로 확인한다.

## 롤백

문제가 생기면 코드와 crontab을 직전 `main` 커밋으로 함께 되돌린다. 코드만 되돌리고 장중 cron을 남기면 새 CLI가 인수를 거부하므로, 두 변경은 한 단위로 롤백한다.
