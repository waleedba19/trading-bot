"""Multi-asset universe: crypto majors, US large-cap equities, FX majors.

Purpose: widen what the system *considers* (scan, shortlist, deliberation)
without bloating the repository or depending on a PC.

Data layout (all git-ignored):
    research/data/scan/      crypto frames   (<BASE>_<tf>.parquet)
    research/data/universe/  equity + FX     (<SYMBOL>_<tf>.parquet)

``agents.common.load_candles`` falls back to both directories, so the widening
automatically reaches the scan, the market agent and deliberation.

Sources:
    crypto  -> ccxt chain (kraken, okx, kucoin, binance, coinbase, bybit);
               Bybit/Binance geo-block GitHub runners, so the chain matters.
    equity  -> Yahoo Finance via research.data.fetch_equity_candles
    forex   -> Yahoo Finance FX tickers (EURUSD=X ...)

Usage:
    python -m agents.universe --refresh                 # all classes
    python -m agents.universe --refresh --classes crypto,equity --max 20
    python -m agents.universe --json                    # snapshot + cache state
"""
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from research import config

CRYPTO_BASES = [
    "BTC", "ETH", "SOL", "BNB", "XRP", "ADA", "DOGE", "AVAX", "LINK", "DOT",
    "LTC", "BCH", "UNI", "ATOM", "XLM", "NEAR", "ALGO", "FIL", "ICP", "APT",
    "ARB", "OP", "INJ", "TIA",
]
EQUITY_SYMBOLS = [
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META",
    "TSLA", "AMD", "NFLX", "AVGO", "SPY", "QQQ",
]
FOREX_YF = {
    "EURUSD": "EURUSD=X", "XAUUSD": "XAUUSD=X", "GBPUSD": "GBPUSD=X",
    "USDJPY": "USDJPY=X", "AUDUSD": "AUDUSD=X", "USDCAD": "USDCAD=X",
    "USDCHF": "USDCHF=X", "NZDUSD": "NZDUSD=X", "XAGUSD": "XAGUSD=X",
}
# Yahoo has no spot XAU/XAG symbols; COMEX futures are the usable free proxy.
YAHOO_FALLBACKS = {
    "XAUUSD": ("XAUUSD=X", "GC=F"),
    "XAGUSD": ("XAGUSD=X", "SI=F"),
}
CLASSES = ("crypto", "equity", "forex")
DEFAULT_TF = {"crypto": "4h", "equity": "1d", "forex": "1d"}
FETCH_DAYS = {"crypto": 365, "equity": 900, "forex": 900}
MIN_BARS = 20

CRYPTO_DIR = Path(config.DATA_DIR) / "scan"
UNIVERSE_DIR = Path(config.DATA_DIR) / "universe"

SCAN_EXCHANGES = (
    ("kraken", "{base}/USD"),
    ("okx", "{base}/USDT"),
    ("kucoin", "{base}/USDT"),
    ("binance", "{base}/USDT"),
    ("coinbase", "{base}-USD"),
    ("bybit", "{base}/USDT"),
)


# ---------------------------------------------------------------------------
# symbol handling
# ---------------------------------------------------------------------------
def _base(symbol):
    text = str(symbol).upper().strip()
    text = text.split("=")[0].split("/")[0].split("-")[0].split("_")[0]
    for quote in ("USDT", "USDC"):
        if text.endswith(quote) and len(text) > len(quote):
            return text[: -len(quote)]
    return text


def classify(symbol):
    """'crypto' | 'equity' | 'forex' | None (unknown -> caller must skip)."""
    base = _base(symbol)
    if base in FOREX_YF:
        return "forex"
    if base in CRYPTO_BASES or base in set(config.ASSETS):
        return "crypto"
    if base in EQUITY_SYMBOLS or base in set(config.EQUITY_ASSETS):
        return "equity"
    return None


def display_symbol(symbol):
    """Canonical name used in artifacts: BTC/USD, AAPL, EURUSD."""
    base = _base(symbol)
    return f"{base}/USD" if classify(symbol) == "crypto" else base


def decision_symbol(symbol):
    """Name a deliberation decision would use: BTC/USDT, AAPL, EURUSD."""
    base = _base(symbol)
    return f"{base}/USDT" if classify(symbol) == "crypto" else base


def watchlist(asset_class, limit=None):
    if asset_class == "crypto":
        names = CRYPTO_BASES
    elif asset_class == "equity":
        names = EQUITY_SYMBOLS
    elif asset_class == "forex":
        names = list(FOREX_YF)
    else:
        raise ValueError(f"unknown asset class: {asset_class}")
    return names[:limit] if limit else list(names)


def cache_path(symbol, tf):
    cls = classify(symbol)
    directory = CRYPTO_DIR if cls == "crypto" else UNIVERSE_DIR
    return Path(directory) / f"{_base(symbol)}_{tf}.parquet"


# ---------------------------------------------------------------------------
# fetch + cache
# ---------------------------------------------------------------------------
def _normalise_frame(df):
    if df is None or len(df) == 0 or "date" not in df.columns:
        return None
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"], utc=True)
    for col in ("open", "high", "low", "close", "volume"):
        if col not in df.columns:
            df[col] = 0.0
    return (
        df[["date", "open", "high", "low", "close", "volume"]]
        .dropna(subset=["close"])
        .drop_duplicates(subset=["date"])
        .sort_values("date")
        .reset_index(drop=True)
    )


def _fetch_crypto(base, tf, days):
    import ccxt

    errors = []
    since = int(pd.Timestamp.now("UTC").timestamp() * 1000) - int(days * 86400 * 1000)
    for exchange_name, fmt in SCAN_EXCHANGES:
        try:
            ex = getattr(ccxt, exchange_name)({"enableRateLimit": True})
            rows = []
            cursor = since
            while cursor < ex.milliseconds():
                batch = ex.fetch_ohlcv(fmt.format(base=base), timeframe=tf, since=cursor, limit=1000)
                if not batch:
                    break
                rows.extend(batch)
                last = batch[-1][0]
                if last <= cursor or len(rows) > 20000:
                    break
                cursor = last + 1
                time.sleep(ex.rateLimit / 1000)
            if not rows:
                raise RuntimeError("no rows")
            frame = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])
            frame["date"] = pd.to_datetime(frame["ts"], unit="ms", utc=True)
            return _normalise_frame(frame.drop(columns=["ts"]))
        except Exception as e:
            errors.append(f"{exchange_name}:{type(e).__name__}")
    raise RuntimeError("all exchanges failed (" + ", ".join(errors) + ")")


def _yahoo_tickers(symbol):
    """Candidate Yahoo tickers, best first (spot FX, else free futures proxy)."""
    base = _base(symbol)
    if base in YAHOO_FALLBACKS:
        return YAHOO_FALLBACKS[base]
    return (FOREX_YF.get(base, base),)


def _fetch_yahoo(symbol, tf, days):
    import warnings

    from research import data as rdata
    last_error = None
    for ticker in _yahoo_tickers(symbol):
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")  # yfinance/pandas deprecation noise
                frame = rdata.fetch_equity_candles(ticker, tf, days_back=days)
        except Exception as e:
            last_error = e
            continue
        normalised = _normalise_frame(frame)
        if normalised is not None and len(normalised) >= MIN_BARS:
            return normalised
    suffix = f": {type(last_error).__name__}" if last_error else ""
    raise RuntimeError(f"yahoo failed for {_yahoo_tickers(symbol)}{suffix}")


def fetch(symbol, tf=None, days=None):
    cls = classify(symbol)
    if cls is None:
        raise ValueError(f"unknown asset class for {symbol}")
    tf = tf or DEFAULT_TF[cls]
    days = days or FETCH_DAYS[cls]
    return _fetch_crypto(_base(symbol), tf, days) if cls == "crypto" else _fetch_yahoo(symbol, tf, days)


def load(symbol, tf=None):
    cls = classify(symbol)
    if cls is None:
        return None
    tf = tf or DEFAULT_TF[cls]
    path = cache_path(symbol, tf)
    if not path.is_file():
        return None
    try:
        return _normalise_frame(pd.read_parquet(path))
    except Exception:
        return None


def refresh(symbols=None, classes=CLASSES, limit=None, days=None):
    """Fetch + cache symbols. Returns per-symbol status (never raises per symbol)."""
    if symbols is None:
        symbols = [s for cls in classes for s in watchlist(cls, limit)]
    status = {}
    for symbol in symbols:
        cls = classify(symbol)
        if cls is None:
            status[str(symbol)] = {"ok": False, "reason": "unknown asset class"}
            continue
        tf = DEFAULT_TF[cls]
        try:
            frame = fetch(symbol, tf, days)
        except Exception as e:
            status[f"{_base(symbol)}"] = {"ok": False, "reason": f"{type(e).__name__}: {e}"[:160]}
            continue
        if frame is None or len(frame) < MIN_BARS:
            status[_base(symbol)] = {"ok": False, "reason": "insufficient bars"}
            continue
        path = cache_path(symbol, tf)
        path.parent.mkdir(parents=True, exist_ok=True)
        frame.to_parquet(path, index=False, engine="pyarrow")
        status[_base(symbol)] = {"ok": True, "rows": int(len(frame)), "tf": tf, "path": str(path)}
    return status


# ---------------------------------------------------------------------------
# scoring (same formula as asset_scanner.run for crypto, shared for all classes)
# ---------------------------------------------------------------------------
def score(symbol, tf=None, fetch_missing=False):
    """Return (record, skip_reason); exactly one is None."""
    from agents import feature_engine

    cls = classify(symbol)
    if cls is None:
        return None, f"unknown asset class: {symbol}"
    tf = tf or DEFAULT_TF[cls]
    frame = load(symbol, tf)
    if frame is None and fetch_missing:
        try:
            refresh([symbol], classes=(cls,), days=None)
        except Exception as e:
            return None, f"fetch failed ({type(e).__name__}: {e})"
        frame = load(symbol, tf)
    if frame is None:
        return None, f"no cached {tf} candles"
    if len(frame) < MIN_BARS:
        return None, f"only {len(frame)} {tf} bars (<{MIN_BARS})"

    feats = feature_engine.extract(frame, display_symbol(symbol), tf)
    if not feats:
        return None, "feature extraction returned nothing"
    mom = feats["momentum_3_5_10_21"]
    micro = feats["micro_score"]
    vol = feats["vol_regime"]
    vspike = feats["volume_spike"]
    pos = feats["candle_position"]
    profit = mom * 2.0 + micro * 3.0 + vol * 1.5 + vspike * 2.0 + pos * 2.0
    return {
        "pair": display_symbol(symbol),
        "asset_class": cls,
        "tf": tf,
        "close": feats["last_close"],
        "momentum": round(mom, 4),
        "micro_score": round(micro, 4),
        "vol_regime": round(vol, 3),
        "volume_spike": round(vspike, 3),
        "candle_position": round(pos, 3),
        "n_bars": feats["n_bars"],
        "profit_score": round(profit, 4),
    }, None


def snapshot():
    """Cache state per class (what is ready to score right now)."""
    out = {}
    for cls in CLASSES:
        tf = DEFAULT_TF[cls]
        ready = [s for s in watchlist(cls) if cache_path(s, tf).is_file()]
        out[cls] = {"tf": tf, "cached": ready, "count": len(ready)}
    return {"generated_at": datetime.now(timezone.utc).isoformat(), "classes": out}


def main(argv=None):
    import argparse

    parser = argparse.ArgumentParser(prog="python -m agents.universe")
    parser.add_argument("--refresh", action="store_true", help="fetch + cache the universe")
    parser.add_argument("--classes", default="crypto,equity,forex")
    parser.add_argument("--max", type=int, default=None, help="max symbols per class")
    parser.add_argument("--days", type=int, default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    classes = tuple(c.strip() for c in args.classes.split(",") if c.strip())
    unknown = [c for c in classes if c not in CLASSES]
    if unknown:
        parser.error(f"unknown classes: {unknown}")

    payload = {}
    if args.refresh:
        payload["refresh"] = refresh(classes=classes, limit=args.max, days=args.days)
    payload["snapshot"] = snapshot()
    print(json.dumps(payload, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
