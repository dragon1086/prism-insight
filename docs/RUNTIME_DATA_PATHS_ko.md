# 런타임 데이터 경로

저장소에 추적되는 JSON은 새 설치와 장애 복구를 위한 **seed**입니다. 운영 중
갱신되는 데이터는 저장소 루트의 `runtime/`에 기록하며, 이 디렉터리는 Git이
무시합니다. 따라서 정상적인 배치·거래 실행만으로 배포 서버의 working tree가
dirty 상태가 되어서는 안 됩니다.

## 한국 종목명·코드 맵

- tracked seed: `stock_map.json`
- 기본 runtime 파일: `runtime/stock_map.json`
- 갱신: `python update_stock_data.py`
- 명시적 출력: `python update_stock_data.py --output /path/to/stock_map.json`
- 환경 변수: `PRISM_STOCK_MAP_PATH`
- 이전 Kakao 설정 호환: `KAKAO_STOCK_MAP_PATH`

reader는 환경 변수 경로가 존재하면 그 파일을 사용하고, 그렇지 않으면 기본
runtime 파일, tracked seed 순으로 읽습니다. 갱신 스크립트는 `--output`, 환경
변수, 기본 runtime 파일 순으로 쓰기 대상을 정합니다.

## 미국 거래소 캐시

- tracked seed: `prism-us/trading/data/exchange_cache.json`
- 기본 runtime 파일: `runtime/us_exchange_cache.json`
- 환경 변수: `PRISM_US_EXCHANGE_CACHE_PATH`

프로세스 시작 시 환경 변수 파일, 기본 runtime 파일, tracked seed 순으로
읽습니다. 이후 자동으로 찾은 거래소 코드를 저장할 때는 환경 변수 파일 또는
기본 runtime 파일에만 씁니다. 환경 변수로 지정한 파일이 없을 때도 tracked
seed를 직접 수정하지 않으며, 첫 저장부터 지정한 runtime 파일을 만듭니다.

## 미국 완료 일봉 캐시

- 기본 경로: `runtime/us_daily_ohlcv_cache/` (Git ignored).
- 경로 override: `PRISM_US_DAILY_CACHE_DIR`.
- 비활성화: `US_DAILY_CACHE_ENABLED=false`. 기본값 true.
- yfinance 조정 일봉의 종목/거래일/요청시각/설정/버전과 같은 응답의 과거 기준 봉을 묶어 저장합니다.
- 마감 여부는 요청 시작시각 기준입니다. 마감 전 시작한 조회를 마감 후 완료됐다는 이유로 완료 일봉으로 저장하지 않습니다.
- 실제 NYSE 마감(조기폐장 포함) 이후 관측한 원본만 저장하며 repair 자료를 배제합니다. 공급자 사후 정정 가능성은 남습니다.
- 새 유효 원본이 우선이고, 캐시는 동일 날짜의 정상 과거 기준 봉이 현재 응답과 모두 일치할 때만 재사용합니다. 확인 불가/변경/오염은 결측으로 남깁니다.
- 결측만 최대 10종목/15초 안에서 단일 조회를 시도합니다. 개별 호출 timeout은 5초이며 실제 in-flight 요청 종료까지의 절대 벽시계 상한은 아닙니다. 늦은 응답은 채택하지 않습니다.
- 영속 캐시는 과거에 저장하지 못한 자료를 만들어내지 않습니다. 최초 운영에서는 저장 축적 전 결측이 계속될 수 있습니다.

## 운영 점검 명령

```bash
git status --short
python update_stock_data.py
git status --short
```

두 번째 `git status`에서 `stock_map.json`이나
`prism-us/trading/data/exchange_cache.json`이 변경되면 안 됩니다. 기존 운영
서버에서 seed가 이미 바뀌어 있다면 내용을 먼저 보존한 뒤 runtime 파일로
복사하고, tracked seed는 Git 버전으로 복구합니다.

운영 서버의 pull·backup·rollback 절차는
[`SERVER_GIT_OPERATIONS_ko.md`](SERVER_GIT_OPERATIONS_ko.md)를 따릅니다.
