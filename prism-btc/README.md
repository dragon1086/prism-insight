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
cd /path/to/prism-insight
.venv/bin/python -m pytest prism-btc/tests -x -q
```

## Backfill (all 6 timeframes, from 2022-01-01)

```bash
cd /path/to/prism-insight
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

## ⚠️ Local Environment — pandas rolling bug on Python 3.14

루트 `.venv`(Python 3.14)에서는 pandas 2.x의 `rolling()` 계열이 **약 2.1만 행
이상 시리즈에서 전부 NaN**을 반환한다 (2026-07 확인; 30m/1h TF가 해당 →
지표 전-NaN → 신호 0으로 백테스트/분석이 조용히 오염됨). pandas 2.x는
cp314 wheel이 있어도 windowed aggregation이 깨져 있고 pandas 3.0에서만
정상이다.

**맥 로컬에서 백테스트/분석 실행 시 반드시 `.venv-bt` 사용** (Python 3.12 +
pandas 2.2.3 — db-server와 동일 버전, 결과 일치 검증됨):

```bash
cd /path/to/prism-insight
.venv-bt/bin/python -m pytest prism-btc/tests -q          # 275 passed
cd prism-btc && ../.venv-bt/bin/python -m analysis.round5_gate_cross
```

없으면 재생성:

```bash
/opt/homebrew/bin/python3.12 -m venv .venv-bt
.venv-bt/bin/pip install "pandas==2.2.3" "numpy==2.2.6" pytest requests python-dotenv
```

`analysis/round5_gate_cross.py` 에는 전-NaN 즉시-실패 가드가 있다.
루트 `.venv` 자체의 근본 수리는 Python 3.12 재구축이 정답이나, 라이브
subscriber(tmux)가 사용 중이므로 장 마감 유지보수 창에서만 수행할 것.
