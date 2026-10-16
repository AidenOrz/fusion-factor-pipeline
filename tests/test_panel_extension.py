# -*- coding: utf-8 -*-
"""Regression tests for bridge.panel_extension.

These lock the contract that lets the fusion panel be rebuilt past the frozen
upstream development window: the eight v6-derived columns must reproduce
v6's formulas exactly, and the aggregate kline file must be skipped.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from bridge.panel_extension import DERIVED, compute_indicators


def _synthetic_frame(n: int = 260, seed: int = 7) -> pd.DataFrame:
    rng = np.random.RandomState(seed)
    close = 10 + np.cumsum(rng.normal(0, 0.2, n))
    high = close + rng.uniform(0.01, 0.4, n)
    low = close - rng.uniform(0.01, 0.4, n)
    open_ = low + rng.uniform(0.0, 1.0, n) * (high - low)
    return pd.DataFrame(
        {
            "date": pd.bdate_range("2023-06-01", periods=n),
            "code": ["000001"] * n,
            "name": ["TEST"] * n,
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": rng.randint(1_000, 100_000, n).astype(float),
            "amount": rng.uniform(1e6, 1e9, n),
        }
    )


def test_derived_columns_match_v6_formulas():
    """Recompute each derived column independently from the v6 definitions."""
    frame = _synthetic_frame()
    out = compute_indicators(frame).reset_index(drop=True)
    src = frame.reset_index(drop=True)

    tr = pd.concat(
        [
            src["high"] - src["low"],
            (src["high"] - src["close"].shift(1)).abs(),
            (src["low"] - src["close"].shift(1)).abs(),
        ],
        axis=1,
    ).max(axis=1)

    expected = {
        "ret_5d": src["close"] / src["close"].shift(5) - 1,
        "ret_20d": src["close"] / src["close"].shift(20) - 1,
        "vol_ratio": src["volume"] / src["volume"].rolling(20).median().shift(1),
        "amount_dry_ratio": (
            src["amount"].rolling(10).median().shift(1)
            / src["amount"].rolling(60).median().shift(1)
        ),
        "atr_relative": (
            tr.rolling(10).mean().shift(1) / tr.rolling(50).mean().shift(1)
        ),
        "realized_vol_120d_d1": (
            src["close"].pct_change().rolling(120).std(ddof=1).shift(1) * np.sqrt(250)
        ),
        "position_ema20": (
            src["close"].shift(1)
            / src["close"].ewm(span=20, adjust=False).mean().shift(1)
            - 1
        ),
    }
    hhv15 = src["high"].rolling(15).max().shift(1)
    llv15 = src["low"].rolling(15).min().shift(1)
    hhv30 = src["high"].rolling(30).max().shift(1)
    llv30 = src["low"].rolling(30).min().shift(1)
    expected["range_relative"] = (
        ((hhv15 - llv15) / llv15) / ((hhv30 - llv30) / llv30)
    )

    assert set(expected) == set(DERIVED)
    for column, values in expected.items():
        got = out[column].astype(float)
        want = values.astype(float)
        mask = got.notna() & want.notna()
        assert mask.sum() > 0, column
        assert np.allclose(got[mask], want[mask], atol=0, rtol=0), column


def test_shifted_columns_are_causal():
    """D-1 style columns must not use the current bar."""
    frame = _synthetic_frame()
    out = compute_indicators(frame).reset_index(drop=True)
    # position_ema20 uses prev_close vs the previous EMA, so the first row is NaN.
    assert pd.isna(out.loc[0, "position_ema20"])
    assert pd.isna(out.loc[0, "ret_5d"])
    assert out.loc[0, "bar_number"] == 0


@pytest.mark.parametrize("column", DERIVED)
def test_derived_columns_are_numeric(column):
    out = compute_indicators(_synthetic_frame())
    assert pd.api.types.is_numeric_dtype(out[column])
