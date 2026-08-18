"""Standalone regressions for CODE_REVIEW_FIXES_2026-08-17.

Run from the project root: python tests/run_review_smoke.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bridge.factor_selection import (
    build_ic_stability_table,
    build_rank_correlation,
    build_rolling_ic_by_family,
    choose_threshold,
    ic_ir_hac,
    rolling_ic_series,
    split_selection_panel,
)
from bridge.gbdt import apply_feature_selection_cutoff
from bridge.llm_miner import _build_validation_dummy, deduplicate_semantic, validate_expressions
from bridge.ops import cross_rank, evaluate_builtin, ts_mean


def _spec(identifier: str, expression: str) -> dict[str, str]:
    return {
        "id": identifier,
        "expression": expression,
        "direction": "long",
        "hypothesis": "smoke test",
    }


def _ic_panel(n_days: int = 200) -> pd.DataFrame:
    rows = []
    for day_index, date in enumerate(pd.bdate_range("2024-01-01", periods=n_days)):
        for stock_index in range(6):
            target = float(stock_index)
            rows.append(
                {
                    "date": date,
                    "code": f"{stock_index:06d}",
                    "factor": target if day_index < n_days - 120 else -target,
                    "EXRET20": target,
                }
            )
    return pd.DataFrame(rows)


def main() -> int:
    dummy = _build_validation_dummy()
    assert dummy.shape == (60 * 20, 10)
    assert dummy.loc[dummy["stock_code"] == "100019.SZ", "close"].nunique() == 1
    validated = validate_expressions(
        [_spec("shift20", "ts_shift(close,20)"), _spec("all_nan", "ts_shift(close,100)")],
        dummy,
    )
    assert [spec["id"] for spec in validated] == ["shift20"]
    semantic = deduplicate_semantic([_spec("close", "close"), _spec("negative", "-close")], dummy)
    assert [spec["id"] for spec in semantic] == ["close"]
    print("[ok] LLM validation dummy rejects empty outputs and semantic duplicates")

    frame = pd.DataFrame(
        {
            "code": ["000001", "000002"] * 4,
            "date": pd.to_datetime(
                ["2025-01-01"] * 2
                + ["2025-01-02"] * 2
                + ["2025-01-03"] * 2
                + ["2025-01-06"] * 2
            ),
            "volume": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0],
            "close": [10.0, 20.0, 11.0, 21.0, 12.0, 22.0, 13.0, 23.0],
        }
    )
    product, _ = evaluate_builtin(frame, "ts_mean(volume, 2) * rank(close)")
    expected_product = ts_mean(frame, "volume", 2) * cross_rank(frame, "close")
    assert np.allclose(product, expected_product, equal_nan=True)
    ratio, _ = evaluate_builtin(frame, "(close + volume) / (volume + 1)")
    assert np.allclose(ratio, (frame["close"] + frame["volume"]) / (frame["volume"] + 1), equal_nan=True)
    print("[ok] builtin arithmetic handles multiplication and parenthesized division")

    persistent_ics = 0.1 + 0.05 * np.sin(np.arange(240) / 20.0)
    unadjusted_ir = float(persistent_ics.mean() / persistent_ics.std(ddof=1))
    hac_ir = ic_ir_hac(persistent_ics)
    assert hac_ir is not None and abs(hac_ir) < abs(unadjusted_ir)

    sparse = pd.DataFrame(
        {
            "date": pd.to_datetime(["2025-01-01"] * 5 + ["2025-01-02"] * 5),
            "left": [1.0, 2.0, 3.0, 4.0, 5.0] + [np.nan] * 5,
            "right": [np.nan] * 5 + [1.0, 2.0, 3.0, 4.0, 5.0],
        }
    )
    sparse_corr = build_rank_correlation(sparse, ["left", "right"])
    assert pd.isna(sparse_corr.loc["left", "right"])
    threshold, selected = choose_threshold(
        pd.DataFrame([[1.0, 0.9], [0.9, 1.0]], index=["a", "b"], columns=["a", "b"]),
        pd.DataFrame(
            {
                "factor": ["a", "b"],
                "ic_mean": [0.1, 0.05],
                "coverage": [1.0, 1.0],
            }
        ),
    )
    assert threshold == 0.80 and selected == ["a"]
    print("[ok] factor selection uses HAC IC, pairwise correlations, and a fixed threshold")

    panel = _ic_panel()
    selection_panel, cutoff, evaluation_start = split_selection_panel(panel)
    evaluation_panel = apply_feature_selection_cutoff(panel, cutoff)
    assert selection_panel["date"].max() == cutoff
    assert evaluation_panel["date"].min() == evaluation_start
    assert selection_panel["date"].max() < evaluation_panel["date"].min()
    rolling = rolling_ic_series(panel, "factor", window=60, min_periods=20)
    assert rolling.iloc[-1] < 0
    stability = build_ic_stability_table(panel, ["factor"])
    assert bool(stability.iloc[0]["recently_reversed"])
    by_family = build_rolling_ic_by_family(panel, ["factor"])
    assert not by_family.empty and {"date", "family", "rolling_ic", "factor_count"} == set(by_family.columns)
    print("[ok] feature selection is chronologically isolated and reports factor reversal")

    print("[ok] code-review regressions: all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
