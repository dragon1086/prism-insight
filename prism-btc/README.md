# prism-btc — PRISM v3 BTC Futures Auto-Trading (D1–D2 Scaffold)

Self-contained package. Does **not** import anything from `cores/` or `prism-us/`.

## Module Structure

```
prism-btc/
├── collector/
│   ├── bybit_public.py   Bybit v5 public REST client (no API key)
│   ├── store.py          SQLite upsert layer (state/market.db)
│   ├── backfill.py       Historical backfill CLI (__main__)
│   └── update.py         Incremental update library (for daemon)
├── engine/
│   ├── config.py         All thresholds/weights (single source of truth)
│   ├── indicators.py     SMA(n), ATR(14) — pure pandas, no I/O
│   └── regime.py         Multi-TF regime tagging + alignment score
├── tests/
│   ├── test_indicators.py
│   ├── test_regime.py
│   └── test_store.py
├── state/
│   └── market.db         (auto-created on first run)
└── README.md
```

## Running Tests (offline, no network)

```bash
cd /Users/rocky/Downloads/prism-insight
.venv/bin/python -m pytest prism-btc/tests -x -q
```

## Backfill (all 6 timeframes, from 2022-01-01)

```bash
cd /Users/rocky/Downloads/prism-insight
.venv/bin/python -m prism-btc.collector.backfill
# or:
.venv/bin/python -c "
import sys; sys.path.insert(0, 'prism-btc')
from collector.backfill import backfill_all
backfill_all()
"
```

## Regime Snapshot (after backfill)

```python
import sys; sys.path.insert(0, 'prism-btc')
import sqlite3, pandas as pd
from collector.store import get_connection
from engine.regime import build_snapshot

conn = get_connection()
tfs = ["30m", "1h", "4h", "12h", "1d", "1w"]
tf_dfs = {}
for tf in tfs:
    rows = conn.execute(
        "SELECT open_time, open, high, low, close, volume, turnover "
        "FROM klines WHERE timeframe=? AND confirmed=1 ORDER BY open_time",
        (tf,)
    ).fetchall()
    tf_dfs[tf] = pd.DataFrame(rows, columns=["open_time","open","high","low","close","volume","turnover"])

snap = build_snapshot(tf_dfs)
print(snap.to_json())
```

## Alignment Score Interpretation

| Score | Meaning |
|-------|---------|
| ≥ 80  | Strong full-alignment — high leverage allowed (25–30x) |
| 60–80 | Good alignment (15–25x) |
| 40–60 | Weak alignment — entry caution (10–15x, 1 tranche only) |
| < 40  | No trade (sideways / conflicted) |
| < 0   | Short bias |
