"""Standalone P0 smoke checks for the four data pits.

Run from the project root: python tests/run_p0_smoke.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bridge.data import (
    DataContractError,
    attach_total_shares,
    load_kline_panel,
    normalize_panel,
    require_market_cap,
)
from bridge.ops import FactorEvaluationError, group_rank, sanitize, ts_corr


def expect_raises(label: str, fn, exc_type) -> None:
    try:
        fn()
    except exc_type:
        print(f"[ok] {label}: rejected as expected")
        return
    raise AssertionError(f"{label}: expected {exc_type.__name__}, but call succeeded")


def main() -> int:
    workspace = Path(__file__).resolve().parents[2]

    # ---- 1. vwap unit: real local row must pass after lots->shares -------
    frame = load_kline_panel(workspace / "a-share-kline-data", codes=["000001"], limit=1)
    row = frame.iloc[0]
    ratio = row["vwap"] / row["close"]
    assert 0.2 < ratio < 5.0, f"vwap unit check failed on real row: ratio={ratio}"
    print(f"[ok] vwap lots->shares: real 000001 row ratio={ratio:.3f}")

    # Wrong-unit path must be caught.
    raw = pd.DataFrame(
        {
            "code": ["000001"],
            "date": [pd.Timestamp("2025-06-10")],
            "open": [11.0],
            "high": [11.2],
            "low": [10.8],
            "close": [11.0],
            "volume": [100.0],  # kept in lots on purpose
            "amount": [1_100_000.0],
            "name": ["平安银行"],
        }
    )
    expect_raises("vwap wrong unit", lambda: normalize_panel(raw, volume_unit="shares"), DataContractError)

    # ---- 2. group_rank never silently degrades ---------------------------
    df = pd.DataFrame(
        {
            "date": pd.to_datetime(["2025-01-01"] * 4),
            "code": ["000001", "000002", "000003", "000004"],
            "x": [0.0, 0.1, 0.9, 1.0],
            "industry_code": [0, 0, 1, 1],
        }
    )
    expect_raises("group_rank missing industry", lambda: group_rank(df, "x", "industry_missing"), FactorEvaluationError)
    grouped = group_rank(df, "x", "industry_code")
    assert (grouped.iloc[:2].to_numpy() == np.array([0.5, 1.0])).all()
    assert (grouped.iloc[2:].to_numpy() == np.array([0.5, 1.0])).all()
    print("[ok] group_rank: grouped percentile ranks computed, no silent global fallback")

    # ---- 3. ts_corr zero variance -> NaN, never inf ----------------------
    ts = pd.DataFrame(
        {
            "code": ["000001"] * 5,
            "date": pd.to_datetime(["2025-01-01", "2025-01-02", "2025-01-03", "2025-01-06", "2025-01-07"]),
            "a": [1.0, 1.0, 1.0, 1.0, 1.0],
            "b": [1.0, 2.0, 1.0, 2.0, 1.0],
        }
    )
    corr = ts_corr(ts, "a", "b", 3)
    cleaned, report = sanitize(corr, "corr")
    assert not np.isinf(cleaned).any()
    assert cleaned.isna().any()
    assert report.n_inf == 0
    print(f"[ok] ts_corr zero-variance: NaN handled, report={report.as_dict()}")

    # ---- 4. market cap only from total shares ----------------------------
    expect_raises("market cap proxy", lambda: require_market_cap(df), DataContractError)
    shares = pd.DataFrame(
        {
            "code": ["000001"],
            "effective_date": pd.to_datetime(["2025-01-01"]),
            "total_shares": [10_000_000.0],
        }
    )
    with_cap = attach_total_shares(
        pd.DataFrame(
            {
                "code": ["000001"],
                "trade_date": pd.to_datetime(["2025-01-02"]),
                "close": [10.0],
            }
        ),
        shares,
    )
    assert with_cap.iloc[0]["market_cap"] == 100_000_000.0
    print("[ok] market_cap: close*total_shares only; close*volume proxy rejected")

    print("[ok] P0 four data pits: all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
