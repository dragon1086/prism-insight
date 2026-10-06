"""Today's top KR gainers from the screening snapshot, for the theme brief.

Observation only: the list is written to trigger metadata and never feeds
candidate selection.
"""

import pandas as pd

MIN_MARKET_CAP = 100_000_000_000   # 1,000억 — keeps out penny stocks and SPACs
MIN_TRADE_VALUE = 5_000_000_000    # 50억 — a move nobody traded is not a theme
TOP_N = 30


def market_movers(snapshot, prev_snapshot, cap_df=None, name_map=None, *, top_n=TOP_N):
    common = snapshot.index.intersection(prev_snapshot.index)
    snap = snapshot.loc[common, ["Close", "Amount"]].copy()
    prev_close = pd.to_numeric(prev_snapshot.loc[common, "Close"], errors="coerce")
    snap["change_rate"] = (pd.to_numeric(snap["Close"], errors="coerce") / prev_close - 1) * 100
    if cap_df is not None and not cap_df.empty and "시가총액" in cap_df.columns:
        snap = snap.join(cap_df[["시가총액"]], how="inner")
        snap = snap[snap["시가총액"] >= MIN_MARKET_CAP]
    snap = snap[(pd.to_numeric(snap["Amount"], errors="coerce") >= MIN_TRADE_VALUE) & (snap["change_rate"] > 0)]
    snap = snap.replace([float("inf"), float("-inf")], pd.NA).dropna(subset=["change_rate"])
    rows = []
    for code, row in snap.sort_values("change_rate", ascending=False).head(top_n).iterrows():
        code = str(code).zfill(6)
        rows.append({
            "code": code,
            "name": (name_map or {}).get(code, code),
            "change_rate": round(float(row["change_rate"]), 2),
            "trade_value_eok": round(float(row["Amount"]) / 1e8),
            **({"market_cap_eok": round(float(row["시가총액"]) / 1e8)} if "시가총액" in row else {}),
        })
    return rows
