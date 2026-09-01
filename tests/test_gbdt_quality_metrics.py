import numpy as np
import pandas as pd
import pytest

from bridge.gbdt import (
    assert_target_gap_matches_horizon,
    fold_ic_summary,
    prediction_quality_summary,
)
from bridge.benchmark import ndcg_random_baseline


def _quality_frame(days: int = 60, stocks: int = 6) -> pd.DataFrame:
    rows = []
    for day_index, date in enumerate(pd.bdate_range("2024-01-01", periods=days)):
        for stock_index in range(stocks):
            target = float(stock_index) + day_index * 0.001
            rows.append(
                {
                    "date": date,
                    "code": f"{stock_index:06d}",
                    "prediction": target,
                    "EXRET20": target,
                }
            )
    return pd.DataFrame(rows)


def test_prediction_quality_uses_daily_cross_sectional_ic_and_ndcg():
    metrics = prediction_quality_summary(_quality_frame(), "prediction", "EXRET20")
    assert metrics["mean_daily_spearman_ic"] == 1.0
    assert metrics["ic_days"] == 60
    assert metrics["mean_daily_ndcg_at_5"] == 1.0
    assert metrics["mean_daily_ndcg_at_20"] == 1.0
    assert metrics["mean_daily_ndcg_delta_at_5"] > 0
    assert metrics["mean_daily_ndcg_random_at_5"] == pytest.approx(
        ndcg_random_baseline(pd.Series(range(6)), 5), abs=1e-6
    )


def test_fold_ic_summary_reports_sign_consistency_and_dispersion():
    folds = [
        {"quality": {"mean_daily_spearman_ic": 0.13}},
        {"quality": {"mean_daily_spearman_ic": 0.05}},
        {"quality": {"mean_daily_spearman_ic": -0.01}},
    ]
    summary = fold_ic_summary(folds)
    assert summary["fold_count"] == 3
    assert summary["positive_fold_rate"] == pytest.approx(2 / 3, abs=1e-6)
    assert summary["fold_ic_sign_consistency"] == pytest.approx(2 / 3, abs=1e-6)
    assert summary["fold_ic_std"] == pytest.approx(np.std([0.13, 0.05, -0.01], ddof=1), abs=1e-6)


def test_prediction_quality_empty_frame_is_explicit():
    metrics = prediction_quality_summary(
        pd.DataFrame(columns=["date", "prediction", "EXRET20"]),
        "prediction",
        "EXRET20",
    )
    assert metrics["ic_days"] == 0
    assert metrics["mean_daily_spearman_ic"] is None


def test_exret_target_horizon_must_match_purged_gap():
    assert assert_target_gap_matches_horizon("EXRET20", 20) == 20
    assert assert_target_gap_matches_horizon("EXRET60", 60) == 60
    with pytest.raises(ValueError, match="must equal"):
        assert_target_gap_matches_horizon("EXRET60", 20)
