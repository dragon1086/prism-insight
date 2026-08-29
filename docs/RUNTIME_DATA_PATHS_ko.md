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

## 운영 점검

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
