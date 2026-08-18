"""Standalone regression checks for the factor-pipeline bug fixes.

Run from the project root: python tests/run_bugfix_smoke.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bridge.aurumq_factors import _series_dict_to_pandas
from bridge.gbdt import GAP_TRADING_DAYS, make_purged_splits
from bridge.llm_miner import _response_content
from bridge.ops import evaluate_builtin, group_rank


def expect_raises(label: str, fn, exc_type: type[Exception]) -> None:
    try:
        fn()
    except exc_type:
        print(f"[ok] {label}: rejected as expected")
        return
    raise AssertionError(f"{label}: expected {exc_type.__name__}, but call succeeded")


def main() -> int:
    dates = pd.Series(pd.bdate_range("2025-01-02", periods=80))
    splits = make_purged_splits(dates, n_splits=4)
    train_dates, test_dates = splits[2]
    positions = {date: index for index, date in enumerate(dates)}
    assert GAP_TRADING_DAYS == 20
    assert not splits[1][0], "a training window no longer than the gap must be fully purged"
    assert positions[test_dates[0]] - positions[train_dates[-1]] == GAP_TRADING_DAYS + 1
    print("[ok] purged split: EXRET20 horizon is fully excluded before each test block")

    ts_frame = pd.DataFrame(
        {
            "code": ["000001"] * 5,
            "date": pd.bdate_range("2025-01-02", periods=5),
            "left": [1.0, 2.0, 4.0, 8.0, 16.0],
            "right": [2.0, 1.0, 3.0, 5.0, 9.0],
        }
    )
    original_columns = ts_frame.columns.copy()
    corr, _ = evaluate_builtin(ts_frame, "ts_corr(ts_mean(left, 2), ts_mean(right, 2), 2)")
    assert len(corr) == len(ts_frame)
    assert ts_frame.columns.equals(original_columns)
    assert not any(column.startswith("__corr_arg_") for column in ts_frame.columns)
    print("[ok] ts_corr: nested inputs do not mutate the caller frame")

    ranks = group_rank(
        pd.DataFrame(
            {
                "date": pd.to_datetime(["2025-01-02"] * 4),
                "value": [1.0, 2.0, 100.0, 200.0],
                "industry_code": [0, 0, -1, -1],
            }
        ),
        "value",
        "industry_code",
    )
    assert np.allclose(ranks.iloc[:2], [0.5, 1.0])
    assert ranks.iloc[2:].isna().all()
    print("[ok] group_rank: unknown industries return NaN instead of a synthetic group rank")

    base = pl.DataFrame(
        {
            "stock_code": ["000001", "000002"],
            "trade_date": ["2025-01-02", "2025-01-03"],
        }
    )
    expect_raises(
        "AurumQ positional row mismatch",
        lambda: _series_dict_to_pandas(base, {"alpha": pl.Series("alpha", [1.0])}),
        ValueError,
    )

    assert _response_content(
        {"content": "", "raw": {"choices": [{"message": {"reasoning_content": "[{}]"}}]}}
    ) == "[{}]"
    assert _response_content({"content": "", "raw": {"content": [{"type": "text", "text": ""}]}}) == ""
    print("[ok] LLM fallback: Anthropic-shaped empty content is handled without choices access")

    print("[ok] factor-pipeline bug-fix regressions: all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
