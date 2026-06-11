# live/ — prism-btc 라이브 트랙 (섀도우 → 데모 → 라이브)
#
# 1단계: 섀도우 모드 페이퍼 데몬.
#   - tracking.py : 저장소 루트 stock_tracking_db.sqlite 의 btc_* 테이블 기록 계층
#   - shadow.py   : 가상 계좌 집행 어댑터 (backtest/engine.py 와 동일 집행 의미론)
#   - runner.py   : 데몬 루프 CLI (python -m live.runner --once)
#
# 핵심 설계: 백테스트 어댑터(backtest/engine.py)와 동일한 core 결정 로직
# (core.exits / core.entries / core.risk) + 동일한 비용 상수 / Action 적용
# 순서 / 4h 하드캡 / 쿨다운 / post-only 체결 판정을 그대로 재사용한다.
