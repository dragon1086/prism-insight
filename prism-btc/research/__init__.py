# research/ — 자동 연구공장 (자가개선 닫힌 루프의 검증·반영 레이어)
#
#   overrides.py : 튜너블 화이트리스트 + 챔피언 오버라이드 적용기
#   factory.py   : 가설 → 자동 백테스트 검증 → 합격 시 자동 활성 / 주간 재검증 은퇴
#
# 설계: tasks/btc_autoloop_design.md
# 원칙: 판정권은 데이터(train+OOS 이중 게이트). LLM 은 후보 생성까지만.
