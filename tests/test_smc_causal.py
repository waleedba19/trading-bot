"""SMC features must be causal: the value at bar t may not change when later bars are removed."""
import os
import sys

import numpy as np
import pandas as pd
import pytest

pytest.importorskip("smartmoneyconcepts")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "research"))
import smc_walkforward as w  # noqa: E402


def _candles(n=400, seed=3):
    rng = np.random.default_rng(seed)
    close = 100 + np.cumsum(rng.normal(0, 1, n))
    open_ = np.r_[close[0], close[:-1]]
    high = np.maximum(open_, close) + rng.random(n)
    low = np.minimum(open_, close) - rng.random(n)
    return pd.DataFrame({"date": pd.date_range("2024-01-01", periods=n, freq="4h", tz="UTC"),
                         "open": open_, "high": high, "low": low, "close": close, "volume": rng.random(n) * 10})


def test_features_do_not_look_ahead():
    df = _candles()
    full = w.smc_features(df)
    for cut in (150, 250, 330):
        part = w.smc_features(df.iloc[:cut].reset_index(drop=True))
        pd.testing.assert_frame_equal(full.iloc[:cut - 1].reset_index(drop=True),
                                      part.iloc[:cut - 1].reset_index(drop=True), check_dtype=False)
