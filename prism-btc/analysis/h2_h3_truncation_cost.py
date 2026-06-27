"""
H2 — Winner truncation  &  H3 — Cost decomposition.
Uses the round2/round3 trade-log CSVs already in backtest/results/ (per-position
lifecycle rows) + 30m klines from market.db to compute MFE/MAE in R units and
post-exit trend continuation.

Run from prism-btc/ package root:  python -m analysis.h2_h3_truncation_cost
Pure analysis; reads CSVs + market.db only.
"""
from __future__ import annotations
import sqlite3
from pathlib import Path
import pandas as pd
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "state" / "btc_market.db"
RESULTS = ROOT / "backtest" / "results"
# 3 clean periods (these CSVs are the authoritative round2/3 logs)
CSVS = ["2022-01-01_to_2022-12-31_trades.csv",
        "2023-01-01_to_2023-12-31_trades.csv",
        "2024-01-01_to_2025-12-31_trades.csv"]

def load_30m():
    conn = sqlite3.connect(DB)
    df = pd.read_sql_query(
        "SELECT open_time, high, low, close FROM klines "
        "WHERE timeframe='30m' AND confirmed=1 ORDER BY open_time ASC", conn)
    conn.close()
    df["dt"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    return df

def load_1d():
    conn = sqlite3.connect(DB)
    df = pd.read_sql_query(
        "SELECT open_time, close FROM klines "
        "WHERE timeframe='1d' AND confirmed=1 ORDER BY open_time ASC", conn)
    conn.close()
    df["dt"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    return df

def main():
    m30 = load_30m()
    d1  = load_1d()
    t30 = m30["dt"].values
    hi30 = m30["high"].values; lo30 = m30["low"].values; cl30 = m30["close"].values
    td1 = d1["dt"].values; cd1 = d1["close"].values

    frames = []
    for c in CSVS:
        p = RESULTS / c
        if p.exists():
            df = pd.read_csv(p)
            df["period"] = c.split("_")[0][:4]
            frames.append(df)
    tr = pd.concat(frames, ignore_index=True)
    tr["entry_time"] = pd.to_datetime(tr["entry_time"], utc=True)
    tr["exit_time"]  = pd.to_datetime(tr["exit_time"], utc=True)
    print(f"[trades] {len(tr)} positions from {len(frames)} periods")

    # initial risk in price terms = |entry - sl_initial|. CSV sl_price is FINAL sl
    # (post-trailing/BE), so reconstruct initial 1R from entry & r_multiple is not
    # possible directly; instead use entry->initial SL. We approximate initial risk
    # via the FIRST recorded sl distance is unavailable; use net r_multiple + price.
    # Robust path: derive 1R(price) from the relationship gross/r isn't stored per
    # trade. We instead compute MFE/MAE in PRICE %, then convert to R using the
    # per-trade initial risk = entry_price * (typical SL%). Better: use the SL at
    # entry == the logged sl_price ONLY for trades that never moved to BE/trailing
    # (exit_reason 'sl'). For all, we use |entry-sl_logged| as a LOWER BOUND on 1R
    # is wrong for trailed trades. So compute 1R from MAE-consistent method:
    # For exit_reason=='sl' (no BE), |entry-sl|/entry is the true 1R%. Take the
    # median of that as the canonical risk fraction R%.
    sl_trades = tr[tr.exit_reason=="sl"].copy()
    sl_trades["risk_frac"] = (sl_trades.entry_price - sl_trades.sl_price).abs()/sl_trades.entry_price
    R_FRAC = sl_trades["risk_frac"].median()
    print(f"[1R] canonical initial-risk fraction (median of SL exits) = {R_FRAC*100:.3f}% "
          f"(n_sl={len(sl_trades)})")

    mfe_R=[]; mae_R=[]; post3=[]; post7=[]
    for _,row in tr.iterrows():
        e = np.datetime64(row.entry_time); x = np.datetime64(row.exit_time)
        i = np.searchsorted(t30, e, side="left")
        j = np.searchsorted(t30, x, side="right")
        if j<=i or i>=len(t30):
            mfe_R.append(np.nan); mae_R.append(np.nan); post3.append(np.nan); post7.append(np.nan); continue
        seg_hi = hi30[i:j]; seg_lo = lo30[i:j]
        ep = row.entry_price
        if row.side=="long":
            mfe = (seg_hi.max()-ep)/ep; mae = (seg_lo.min()-ep)/ep
        else:
            mfe = (ep-seg_lo.min())/ep; mae = (ep-seg_hi.max())/ep
        mfe_R.append(mfe/R_FRAC); mae_R.append(mae/R_FRAC)
        # post-exit continuation: did price keep going in trade direction after exit?
        xp = row.exit_price
        kx = np.searchsorted(td1, x, side="left")
        sgn = 1.0 if row.side=="long" else -1.0
        for horizon,store in [(3,post3),(7,post7)]:
            kk = kx+horizon
            if kk < len(cd1):
                store.append(sgn*(cd1[kk]-xp)/xp)
            else:
                store.append(np.nan)
    tr["mfe_R"]=mfe_R; tr["mae_R"]=mae_R; tr["post3"]=post3; tr["post7"]=post7

    # ---- exit_reason breakdown ----
    print("\n=== exit_reason breakdown (net R authoritative) ===")
    print(f"{'reason':>14} {'n':>5} {'avg_netR':>9} {'med_netR':>9} {'avg_MFE_R':>10} {'avg_MAE_R':>10} {'win%':>6}")
    for r,g in tr.groupby("exit_reason"):
        win = (g.r_multiple>0).mean()*100
        print(f"{r:>14} {len(g):>5} {g.r_multiple.mean():>9.3f} {g.r_multiple.median():>9.3f} "
              f"{g.mfe_R.mean():>10.3f} {g.mae_R.mean():>10.3f} {win:>6.1f}")

    # ---- H2 key metric: MFE>=2R but ended netR<0.5R ----
    valid = tr.dropna(subset=["mfe_R"])
    reached2 = valid[valid.mfe_R>=2.0]
    truncated = reached2[reached2.r_multiple<0.5]
    print("\n=== H2 truncation metric ===")
    print(f"trades reaching MFE>=2R: {len(reached2)} / {len(valid)} ({len(reached2)/len(valid)*100:.1f}%)")
    if len(reached2):
        print(f"  of those, ended netR<0.5R: {len(truncated)} ({len(truncated)/len(reached2)*100:.1f}%)  <-- H2 indicator")
        print(f"  mean MFE_R of those reaching 2R: {reached2.mfe_R.mean():.2f}")
        print(f"  mean netR of those reaching 2R:  {reached2.r_multiple.mean():.2f}")
    # also 1R-not-captured
    reached1 = valid[valid.mfe_R>=1.0]
    trunc1 = reached1[reached1.r_multiple<0.5]
    print(f"trades reaching MFE>=1R: {len(reached1)} ({len(reached1)/len(valid)*100:.1f}%); "
          f"of those ended netR<0.5R: {len(trunc1)} ({len(trunc1)/len(reached1)*100:.1f}%)")

    # capture efficiency: netR / MFE_R for winners
    win = valid[valid.r_multiple>0]
    cap = (win.r_multiple/win.mfe_R.replace(0,np.nan)).dropna()
    print("\n=== capture efficiency (winners only) ===")
    print(f"median netR/MFE_R = {cap.median():.3f}  mean = {cap.mean():.3f}  (1.0 = captured the whole favorable move)")

    # ---- post-exit continuation ----
    print("\n=== post-exit trend continuation (signed, in trade direction) ===")
    for h,col in [("+3d","post3"),("+7d","post7")]:
        v = tr[col].dropna()
        cont = (v>0).mean()*100
        print(f"{h}: continued in trade dir {cont:.1f}%  mean move {v.mean()*100:+.2f}%  med {v.median()*100:+.2f}%  n={len(v)}")

    # only for trailing/be/signal exits (early exits we care about)
    early = tr[tr.exit_reason.isin(["be","signal_exit","tp1","tp2"])]
    for h,col in [("+3d","post3"),("+7d","post7")]:
        v = early[col].dropna()
        if len(v):
            print(f"  [early-exits only] {h}: continued {(v>0).mean()*100:.1f}%  mean {v.mean()*100:+.2f}%  n={len(v)}")

    # ---- H3 cost decomposition (per trade, R units) ----
    print("\n=== H3 cost decomposition (per-trade R units) ===")
    # net_pnl not in CSV; reconstruct: r_multiple is NET R. gross R unavailable in
    # CSV, but fee_paid & funding_paid ARE. Convert $ cost to R via initial_risk$.
    # initial_risk$ = equity*2%*tranche_frac is unknown per row, but cost_R =
    # cost$/initial_risk$. We can recover initial_risk$ only approximately. Instead
    # report cost as fraction of |net pnl| proxy: use fee+funding in $ and the
    # aggregate metrics JSON gross vs net (already known). Here: per-trade fee/funding
    # in $ and the ratio to the canonical 1R$ if we assume equity~10k & 2% risk.
    EQUITY=10000.0; RISK=0.02
    # tranche_frac unknown; assume full (1.0) as upper bound on 1R$ => lower bound on cost_R
    R_DOLLAR = EQUITY*RISK
    tr["fee_R"]=tr.fee_paid/R_DOLLAR
    tr["fund_R"]=tr.funding_paid/R_DOLLAR
    tr["cost_R"]=tr.fee_R+tr.fund_R
    tr["gross_R_est"]=tr.r_multiple+tr.cost_R  # net + cost = gross
    print(f"per-trade means (R):  gross={tr.gross_R_est.mean():+.3f}  fee=-{tr.fee_R.mean():.3f}  "
          f"funding=-{tr.fund_R.mean():.3f}  net={tr.r_multiple.mean():+.3f}")
    print(f"  (assumes 1R$={R_DOLLAR:.0f}; tranche_frac=1 => cost_R is a LOWER bound, gross is LOWER bound)")
    print("per-period:")
    for per,g in tr.groupby("period"):
        print(f"  {per}: gross={g.gross_R_est.mean():+.3f}  cost=-{g.cost_R.mean():.3f}  net={g.r_multiple.mean():+.3f}  n={len(g)}")

    tr.to_csv(Path(__file__).resolve().parent/"h2_h3_trades_enriched.csv", index=False)
    print("\n[saved] analysis/h2_h3_trades_enriched.csv")

if __name__=="__main__":
    main()
