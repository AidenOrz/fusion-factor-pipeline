import pandas as pd
import pytest

from bridge.temporal import (
    assert_selection_evaluation_label_gap,
    first_label_safe_evaluation_date,
)


def test_label_safe_date_requires_horizon_plus_one_positions():
    dates = pd.bdate_range("2024-09-12", periods=30)
    assert first_label_safe_evaluation_date(
        dates, dates[0], label_horizon_trading_days=20
    ) == dates[21]
    assert_selection_evaluation_label_gap(
        dates, dates[0], dates[21], label_horizon_trading_days=20
    )
    with pytest.raises(ValueError, match="label gap is too short"):
        assert_selection_evaluation_label_gap(
            dates, dates[0], dates[20], label_horizon_trading_days=20
        )


def test_label_safe_date_rejects_non_trading_boundaries():
    dates = pd.bdate_range("2024-09-12", periods=30)
    with pytest.raises(ValueError, match="selection end"):
        first_label_safe_evaluation_date(
            dates, "2024-09-14", label_horizon_trading_days=20
        )
