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

## KIS 뉴스 제목 저장소 (KR)

- 기본 경로: `runtime/kr_news_titles.sqlite` (Git ignored). 경로 override: `PRISM_KR_NEWS_DB`.
- 내용: KIS 국내 종합 시황/공시 제목 피드(`FHKST01011800`) 전체 시장분. 제목·제공처·시각·KIS 태그 종목만 있고 본문·URL은 없습니다.
- `stock_tracking_db.sqlite`에 넣지 않습니다. 그 DB는 하루 여러 번 다른 서버로 복사됩니다.
- 수집: `tools/collect_kr_news_titles.py` (db-server cron, 읽기 전용 KIS 호출)
  - `live`: 5분마다 최신 페이지부터 저장된 시각까지 (보통 1~3회 호출)
  - `backfill --until YYYYMMDD`: 저장된 가장 오래된 제목에서 과거로. 거래일 하루 약 8천 건, 약 200회 호출·2분
  - `prune --keep-days 400`: 오래된 제목 삭제 후 VACUUM
- 규모(2026-10-02 실측): 거래일 하루 약 8천 건, 약 2.3MB. 1년 약 0.6~0.8GB.
- 조회: `prism_core.kr_news_store.search()` (키워드·태그 종목·기간).

## KIS 뉴스 제목 저장소 (US)

- 기본 경로: `runtime/us_news_titles.sqlite` (Git ignored). 경로 override: `PRISM_US_NEWS_DB`. 스키마는 KR과 같습니다.
- 내용: KIS 해외뉴스종합(제목) 피드(`HHPSTH60100C1`). 연합미국·글로벌ETF·한국투자증권·연합차이나 등 한국어 제목,
  기사당 태그 종목 1개(심볼·한글 종목명), `provider_code` = 국가 코드(US, CN 등), `category` = 분류(종목리포트·특징주·시황·ETF 등).
- 과거 조회는 약 2025-10부터 가능합니다. 한 번에 약 10건, 하루 약 90~160건(10~18회 호출).
- 수집: `tools/collect_kr_news_titles.py --market us live|backfill|prune` (KR과 같은 수집기, 읽기 전용 KIS 호출)
- 조회: `prism_core.kr_news_store.search(store.connect(store.db_path("US")), ...)`.

## US 세부 테마 지도 (참고 자료)

- 경로: `runtime/us_theme_map_v3.json`(시총 상위 1000 ∪ 거래대금 상위 500, 현재본), `runtime/us_theme_map_v2.json`(시총 상위 1000), `runtime/us_theme_map_v1.json`(상위 500, 첫 빌드). KR `runtime/kr_theme_map_v1.json`과 같은 `{meta, themes}` 모양이고, 검토표는 `runtime/us_theme_map_<버전>_review.md`입니다 (Git ignored).
- 만들기: `tools/build_us_theme_map.py --top 1000 --top-value 500 --map-version v3` (시총 상위 N ∪ 60거래일 평균 거래대금 상위 M(시총 10억 달러 이상) → 1년 일간 종가의 SPY 제거 잔차 상관 → 평균 연결 군집 → AI 이름·배정 → 기사 종목 → 사용자 수정 → US 뉴스 제목 근거). 중간 파일과 이어하기 파일은 `--workdir`에 둡니다. 모델 단계 캐시가 있으면 다시 돌려도 모델을 부르지 않습니다.
- 사용자 수정: `prism_core/data/us_theme_overrides.json`(Git 추적). 매 빌드의 마지막 단계로 적용합니다. `themes`는 대상 테마 정의(`anchor` 종목이 든 테마 → 같은 이름 테마 → 새로 만듦), `assign`은 종목의 테마를 목록으로 바꾸고, `add`는 테마를 더합니다. 수정된 소속은 종목당 3개 제한에서 빠지지 않고 자동 소속이 대신 빠집니다. 목록 밖 종목은 건너뛰고 `meta.overrides`에 기록합니다. `--overrides ''`로 끌 수 있습니다.
- 테마 필드: `lines` = 묶음이 시장과 따로 함께 움직인 날 수, `active_days` = 그날들과 평균 등락률, `mean_corr` = 평균 잔차 상관, 종목 `corr`·`share`·`role`(core = 가격 묶음, ai, news, override = 사용자 수정), `evidence` = 관련 제목 수와 표본.
- 설명 자료로만 씁니다. 스크리닝·점수·매매 판단에 넣지 않습니다.

## US 테마 흐름 (얼럿용)

- 경로: `runtime/us_theme_flow.json` (Git ignored). `tools/build_us_theme_flow.py --mode morning|afternoon`이 US 배치 몇 분 전
  (db-server cron, 뉴욕 시간 10:09·14:24) 테마 지도(`runtime/us_theme_map_vN.json` 최신판) 구성 종목 시세를 yfinance로 받아
  테마별 중앙값 등락·상승 비율·대표 종목·관련 제목 1건을 저장합니다(약 3분, 모델 호출 없음).
- US 시그널 얼럿은 이 파일이 같은 거래일·30분 이내면 "오늘 테마 흐름"을 붙이고, 아니면 업종 ETF 한 줄로 대신합니다.

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
