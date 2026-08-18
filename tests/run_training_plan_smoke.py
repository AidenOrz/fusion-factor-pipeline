"""Regression checks for the P2 factor-processing and Huber training plan.

Run from the project root: ``python tests/run_training_plan_smoke.py``.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bridge.benchmark import evaluate_scorers_vs_benchmark
from bridge.factor_selection import (
    build_direction_review,
    select_top_abs_ic_factors,
    split_selection_panel,
)
from bridge.feature_engineering import (
    add_cross_sectional_features,
    add_industry_neutral_features,
    add_pairwise_cross_features,
)
from bridge.gbdt import ENSEMBLE_SEEDS, apply_feature_selection_cutoff


def _panel() -> pd.DataFrame:
    rows = []
    for date_index, date in enumerate(pd.bdate_range("2024-01-01", periods=80)):
        for stock_index in range(6):
            value = float(stock_index)
            rows.append(
                {
                    "date": date,
                    "code": f"{stock_index:06d}",
                    "factor": value if date_index < 40 else -value,
                    "other": float(6 - stock_index),
                    "industry_code": [0, 0, 1, 1, -1, np.nan][stock_index],
                    "EXRET20": value,
                }
            )
    return pd.DataFrame(rows)


def main() -> int:
    panel = _panel()
    transformed, cs_columns = add_cross_sectional_features(panel, ["factor", "other"])
    assert set(cs_columns) == {
        "factor_cs_rank",
        "factor_cs_z",
        "factor_cs_winsor",
        "other_cs_rank",
        "other_cs_z",
        "other_cs_winsor",
    }
    assert transformed["factor_cs_rank"].between(0, 1).all()

    neutral, neutral_columns = add_industry_neutral_features(panel, ["factor"], min_observations=4)
    assert neutral_columns == ["factor_ind_neutral"]
    unknown = neutral["industry_code"].eq(-1) | neutral["industry_code"].isna()
    assert neutral.loc[unknown, "factor_ind_neutral"].isna().all()

    cross_input = panel.assign(
        f3=panel["factor"] + 1,
        f4=panel["other"] + 1,
        f5=panel["factor"] - panel["other"],
    )
    crossed, cross_columns = add_pairwise_cross_features(
        cross_input, ["factor", "other", "f3", "f4", "f5"]
    )
    assert len(cross_columns) == 20
    assert crossed[cross_columns].replace([np.inf, -np.inf], np.nan).notna().any().all()
    print("[ok] cross-sectional, industry-neutral, and 20 pairwise features")

    selection, cutoff, evaluation_start = split_selection_panel(panel)
    evaluation = apply_feature_selection_cutoff(panel, cutoff)
    assert evaluation["date"].min() == evaluation_start
    assert (evaluation_start - cutoff).days >= 20
    review = build_direction_review(selection, ["factor"], {"factor": "long"})
    assert {"market_style_change", "frequent_direction_switch", "direction_transform_required"} <= set(review.columns)
    print("[ok] selection/evaluation labels have a 20-trading-day net gap and direction review flags")
    ic_table = pd.DataFrame({"factor": ["a", "b", "c"], "ic_mean": [0.01, -0.08, 0.05]})
    assert select_top_abs_ic_factors(ic_table, top_n=2) == ["b", "c"]
    assert select_top_abs_ic_factors(ic_table, top_n=5, minimum_abs_ic=0.02) == ["b", "c"]

    returns = pd.DataFrame(
        {
            "date": pd.to_datetime(["2025-01-02"] * 3),
            "code": ["000001", "000002", "000003"],
            "fwd_ret": [0.10, 0.05, -0.02],
            "index_fwd_ret": [0.01, 0.01, 0.01],
            "excess_vs_sse": [0.09, 0.04, -0.03],
        }
    )
    scores = returns[["date", "code"]].assign(prediction=[3.0, 2.0, 1.0])
    benchmark = evaluate_scorers_vs_benchmark(scores, returns, ["prediction"], top_ks=(1,))
    metrics = benchmark["scorers"]["prediction"][0]
    assert metrics["ndcg"] == 1.0 and metrics["annualized_excess"] > 0
    assert ENSEMBLE_SEEDS == (42, 43, 44)
    print("[ok] benchmark reports NDCG and annualised excess for the Huber ensemble")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
