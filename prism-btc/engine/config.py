# engine/config.py — Central constants for regime engine
# All thresholds and weights here; tweak without touching logic files.

# --- Timeframe weights for alignment score ---
TF_WEIGHTS: dict[str, int] = {
    "30m": 5,
    "1h": 10,
    "4h": 20,
    "12h": 20,
    "1d": 30,
    "1w": 15,
}
MAX_WEIGHT_SUM: int = sum(TF_WEIGHTS.values())  # 100

# --- Trend detection ---
# If |MA10 - MA35| / close < FLAT_THRESHOLD → flat
FLAT_THRESHOLD: float = 0.0015  # 0.15%

# --- Candle position: MA touch tolerance ---
# If low ≤ MA × (1 + TOUCH_TOL) and high ≥ MA × (1 - TOUCH_TOL) → candle touched MA
TOUCH_TOL: float = 0.001  # 0.10%

# --- Alignment score candle-position bonus ---
# When candle position aligns with trend, add this fraction of the TF weight as bonus
CANDLE_BONUS_FRAC: float = 0.20  # up to ±20% of each TF weight

# --- Entry gating (P1-1: 거래 엄선) ---
# Minimum |alignment_score| required to open a new position.
# 40 → 55 (D4 audit) → 70 (라운드4, tasks/v3_edge_diagnosis.md §1).
# Evidence: the 55–70 bucket has proven NEGATIVE 3–7d forward edge (n=198) — cut it.
# NOTE: 85 was tried first (the diagnosis's literal reading) and FALSIFIED in
# realized trading (first-gate-crossing entries land at trend saturation; 21 trades,
# avg -0.5R). 70 keeps the 70–85 bucket (14d +1.78%) and passed 2026H1 OOS.
# See analysis/round4_attribution.py — attribution cells only, no grid sweep.
# 라운드5 재검증 (2026-07, 라이브 20일 무매매 관찰 후 게이트 완화 가설 검토):
# 2020-11~2026-06 전체 표본(n=5,073)의 score×ts_4h 교차셀에서 현행 셀
# (|score|>=70 & ts>=2.0)은 14d +5.10%/hit 65% (n=793)로 3개 분리 기간 모두 유효.
# 완화 후보 55-70×ts>=2 (14d -0.05%), 조기레인 55-70×ts>=3.5 (n=10, 3d 음수) 모두
# 기각 — 게이트 유지. See analysis/round5_gate_cross.py, tasks/btc_round5_gate_review.md.
ENTRY_SCORE_MIN: float = 70.0

# --- Chop filter: trend-strength gate (라운드2 구조개선 #1) ---
# trend_strength = |MA10 - MA35| / ATR14, computed per TF.
# A new entry requires BOTH the 4h AND 1d TFs to have trend_strength >= TS_MIN.
# This structurally suppresses trades during choppy/sideways regimes (e.g. 2023)
# while leaving open-position management untouched.
# 1.0 → 2.0 (라운드4, tasks/v3_edge_diagnosis.md §1): 4h ts 1–2 is a proven
# anti-edge bucket (7d hit 41.0%, mean -0.58%, p=0.0001, n=504); the edge only
# exists at ts 2+ (2–3.5: 14d hit 64.9%; 3.5+: 77.4%). No parameter sweep.
# 라운드5 (2026-07): 2020-2026 교차셀에서도 ts<2 는 score 85+ 조차 무엣지
# (n=1,046, 14d +1.37%/49%) — 게이트 유지 재확인. analysis/round5_gate_cross.py.
TS_MIN: float = 2.0
# TFs that must clear TS_MIN before a new entry is allowed.
# 라운드4: ("4h","1d") → ("4h",). The H1 study measured the *4h* ts buckets;
# requiring 1d>=2.0 simultaneously was an extra assumption never tested by the
# study and it collapsed trade count to ~20/4yr in attribution runs.
TS_GATE_TFS: tuple[str, ...] = ("4h",)

# --- Bybit API ---
BYBIT_BASE_URL: str = "https://api.bybit.com"
BYBIT_KLINE_ENDPOINT: str = "/v5/market/kline"
BYBIT_SYMBOL: str = "BTCUSDT"
BYBIT_CATEGORY: str = "linear"

# interval string → human label
TF_INTERVAL_MAP: dict[str, str] = {
    "30m": "30",
    "1h": "60",
    "4h": "240",
    "12h": "720",
    "1d": "D",
    "1w": "W",
}

# Bybit returns max 1000 candles per request
BYBIT_MAX_LIMIT: int = 1000

# Rate limit: stay under 10 req/s
BYBIT_SLEEP_BETWEEN_REQUESTS: float = 0.12  # seconds

# Backfill start (Unix ms) — 2022-01-01 00:00:00 UTC
# 표본 확장 (Rocky 요청): 2022-01-01 → 2020-01-01. Bybit BTCUSDT 선물은
# 2020-03경 상장이라 API가 주는 만큼 받는다 (2020 코로나 폭락, 2020-21 메가불,
# 2021 더블탑 레짐 추가 — 검증 표본 ~110건 → ~180건+).
BACKFILL_START_MS: int = 1577836800000  # 2020-01-01 00:00:00 UTC

# SQLite path (relative to prism-btc/ package root)
# 비트코인 시세 원본 DB (거래/일지 장부인 루트 stock_tracking_db.sqlite 와 구분).
DB_RELATIVE_PATH: str = "state/btc_market.db"
