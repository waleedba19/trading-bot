#!/usr/bin/env python3
"""Walk-forward test: do smart-money-concepts (SMC) features beat the old warmup score?

Causal by construction: swings are used only once confirmed (SWING bars later),
FVGs only from the bar after they form, nothing reads Swept/Mitigated columns.
Selection uses TRAIN (< SPLIT) only; HOLDOUT (>= SPLIT) is judged once, on
non-overlapping horizons, net of round-trip cost.

    python research/smc_walkforward.py            # prints table, writes research/reports/smc_walkforward.json
"""
import json
import math
import os
import sys

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

SYMBOLS = ["BTC", "ETH", "SOL"]
SWING = 5                 # bars each side to confirm a swing
HORIZON = 6               # 6 x 4h = 24h forward return
COST_PCT = 0.24           # round trip, % (0.12% per side, research/config.py)
SPLIT = pd.Timestamp("2025-01-01", tz="UTC")
MIN_HOLDOUT_N = 100


def smc_features(df):
    """Per-bar SMC features, each in {-1,0,1}, knowable at the bar's close."""
    from smartmoneyconcepts import smc
    ohlc = df.set_index("date")[["open", "high", "low", "close", "volume"]]
    sh = smc.swing_highs_lows(ohlc, swing_length=SWING).reset_index(drop=True)
    n = len(df)
    hi, lo, cl = df["high"].values, df["low"].values, df["close"].values
    kind, lvl = sh["HighLow"].values, sh["Level"].values

    last_hi = np.full(n, np.nan)
    last_lo = np.full(n, np.nan)
    cur_hi = cur_lo = np.nan
    for t in range(n):
        c = t - SWING                       # swing at c is confirmed at t
        if c >= 0:
            if kind[c] == 1:
                cur_hi = lvl[c]
            elif kind[c] == -1:
                cur_lo = lvl[c]
        last_hi[t], last_lo[t] = cur_hi, cur_lo

    sweep = np.zeros(n)                     # wick takes liquidity, close rejects it
    sweep[(hi > last_hi) & (cl < last_hi)] = -1   # stop-run above, closed back below: bearish
    sweep[(lo < last_lo) & (cl > last_lo)] = 1    # stop-run below, closed back above: bullish
    structure = np.zeros(n)                 # close beyond last confirmed swing = break of structure
    structure[cl > last_hi] = 1
    structure[cl < last_lo] = -1

    fv = smc.fvg(ohlc)["FVG"].fillna(0).values   # gap at i is complete when bar i+1 closed
    fvg_net = pd.Series(np.r_[0, fv[:-1]]).rolling(10, min_periods=1).sum().clip(-1, 1).values
    return pd.DataFrame({"sweep": sweep, "structure": structure, "fvg": fvg_net})


def baseline_score(df, warm=60):
    """Old warmup micro_score (agents.feature_engine.extract) on a rolling window."""
    from agents import feature_engine as fe
    out = np.full(len(df), np.nan)
    for t in range(warm, len(df)):
        f = fe.extract(df.iloc[t - warm + 1:t + 1], "x", "4h")
        out[t] = f["micro_score"] if f else np.nan
    return np.sign(out)


def build(sym):
    df = pd.read_parquet(os.path.join(ROOT, "research", "data", f"{sym}_4h.parquet")).reset_index(drop=True)
    f = smc_features(df)
    f["old_score"] = baseline_score(df)
    f["combo"] = np.sign(f["sweep"] + f["structure"] + f["fvg"])
    f["date"] = df["date"].values
    f["fwd"] = (df["close"].shift(-HORIZON) / df["close"] - 1) * 100
    f["sym"] = sym
    return f.dropna(subset=["fwd"])


def evaluate(rows, feat):
    """Non-overlapping (per symbol) samples where the feature fires; BTC/ETH/SOL stay correlated. Return n, hit, net% per trade, t-stat."""
    fired = rows[rows[feat] != 0]
    r = pd.concat([g.iloc[::HORIZON] for _, g in fired.groupby("sym")])  # per symbol: >= HORIZON bars apart
    n = len(r)
    if n < 2:
        return {"n": n, "hit": None, "net_pct": None, "t": None}
    pnl = r[feat] * r["fwd"] - COST_PCT
    t = pnl.mean() / (pnl.std(ddof=1) / math.sqrt(n)) if pnl.std(ddof=1) > 0 else 0.0
    return {"n": n, "hit": round(float((r[feat] * r["fwd"] > 0).mean()), 3),
            "net_pct": round(float(pnl.mean()), 3), "t": round(float(t), 2)}


def passes(train, hold):
    """Keep a feature only if it earns money after costs in BOTH periods and holdout t > 2."""
    return bool(train["net_pct"] is not None and hold["n"] >= MIN_HOLDOUT_N and train["net_pct"] > 0
                and hold["net_pct"] > 0 and hold["t"] > 2)


def main():
    all_rows = pd.concat([build(s) for s in SYMBOLS]).sort_values("date")
    all_rows["date"] = pd.to_datetime(all_rows["date"], utc=True)
    train, hold = all_rows[all_rows["date"] < SPLIT], all_rows[all_rows["date"] >= SPLIT]
    always_long = {"train": round(float(train["fwd"].mean() - COST_PCT), 3),
                   "holdout": round(float(hold["fwd"].mean() - COST_PCT), 3)}
    report = {"horizon_bars": HORIZON, "cost_pct": COST_PCT, "split": str(SPLIT.date()),
              "always_long_net_pct_per_trade": always_long, "features": {}}
    print(f"{'feature':10s} {'train n/hit/net%/t':28s} {'holdout n/hit/net%/t':28s} keep")
    for feat in ("old_score", "sweep", "structure", "fvg", "combo"):
        tr, ho = evaluate(train, feat), evaluate(hold, feat)
        keep = passes(tr, ho)
        report["features"][feat] = {"train": tr, "holdout": ho, "keep": keep}
        fmt = lambda e: f"{e['n']}/{e['hit']}/{e['net_pct']}/{e['t']}"
        print(f"{feat:10s} {fmt(tr):28s} {fmt(ho):28s} {'KEEP' if keep else 'drop'}")
    print("always-long net % per 24h trade:", always_long)
    out = os.path.join(ROOT, "research", "reports", "smc_walkforward.json")
    with open(out, "w") as fh:
        json.dump(report, fh, indent=1)
    return report


if __name__ == "__main__":
    main()
