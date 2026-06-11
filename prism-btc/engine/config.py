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
# Minimum |alignment_score| required to open a new position. Raised 40 → 55 per
# D4 audit to throttle signal frequency. This is the ONLY tuning of this value;
# no parameter sweep. (Leverage bands in sizing.py are independent and unchanged.)
ENTRY_SCORE_MIN: float = 55.0

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
BACKFILL_START_MS: int = 1640995200000

# SQLite path (relative to prism-btc/ package root)
DB_RELATIVE_PATH: str = "state/market.db"
