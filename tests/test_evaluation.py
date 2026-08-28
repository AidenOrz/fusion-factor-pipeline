from __future__ import annotations

import pandas as pd

from bridge.evaluation import _block_stability, _date_range


def test_date_range_parses_registered_bounds():
    start, end = _date_range("2024-10-22..2025-12-31")
    assert start == pd.Timestamp("2024-10-22")
    assert end == pd.Timestamp("2025-12-31")


def test_block_stability_requires_all_blocks_to_have_the_same_nonzero_sign():
    rows = []
    # The project daily-IC contract requires at least 50 dates per block.
    for date_index, date in enumerate(pd.bdate_range("2024-01-02", periods=180)):
        for stock in range(10):
            rows.append(
                {
                    "date": date,
                    "code": f"{stock:06d}",
                    "target": float(stock),
                    "prediction": float(stock) + date_index * 0.001,
                }
            )
    result = _block_stability(pd.DataFrame(rows), "target")
    assert result["block_count"] == 3
    assert result["block_ic_sign_consistency"] == 1.0
