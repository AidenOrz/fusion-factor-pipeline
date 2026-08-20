from __future__ import annotations

import numpy as np
import pandas as pd

from bridge.ranking import walk_forward_lambdarank
from bridge.baselines import run_huber_baseline


def _panel(days: int = 200, stocks: int = 24) -> pd.DataFrame:
    rows = []
    for day_index, date in enumerate(pd.bdate_range("2024-01-02", periods=days)):
        for stock_index in range(stocks):
            score = float(stock_index)
            rows.append(
                {
                    "date": date,
                    "code": f"{stock_index:06d}",
                    "panel_scope": "full_market",
                    "universe_b1": True,
                    "y_raw_rank20_b1": (stock_index + 1) / stocks,
                    "y_excess20": score / 100.0,
                    "factor": score + day_index * 0.001,
                }
            )
    return pd.DataFrame(rows)


def test_lambdarank_uses_full_market_domain_and_produces_quality_metrics():
    result = walk_forward_lambdarank(
        _panel(), universe="b1", target_kind="raw_rank", feature_columns=["factor"], n_splits=4
    )
    assert result["scope"] == "full_market"
    assert result["objective"] == "lambdarank"
    assert result["overall_quality"]["mean_daily_spearman_ic"] is not None
    assert result["fold_stability"]["fold_count"] == 3


def test_lambdarank_rejects_event_conditioned_panel():
    panel = _panel()
    panel["panel_scope"] = "events"
    try:
        walk_forward_lambdarank(panel, universe="b1", target_kind="raw_rank", feature_columns=["factor"])
    except ValueError as exc:
        assert "full_market" in str(exc)
    else:
        raise AssertionError("events scope must be rejected")


def test_lambdarank_requires_explicit_panel_scope():
    panel = _panel().drop(columns="panel_scope")
    try:
        walk_forward_lambdarank(panel, universe="b1", target_kind="raw_rank", feature_columns=["factor"])
    except ValueError as exc:
        assert "panel_scope" in str(exc)
    else:
        raise AssertionError("an unlabeled panel scope must be rejected")


def test_lambdarank_supports_ten_ordered_relevance_levels():
    result = walk_forward_lambdarank(
        _panel(),
        universe="b1",
        target_kind="raw_rank",
        feature_columns=["factor"],
        n_splits=4,
        relevance_levels=10,
    )
    assert result["relevance_levels"] == 10


def test_huber_baseline_uses_the_same_full_market_scope():
    result = run_huber_baseline(
        _panel(),
        target="y_excess20",
        output_name="excess",
        feature_columns=["factor"],
    )
    assert result["scope"] == "full_market"
    assert result["objective"] == "huber"
    assert result["fold_stability"]["fold_count"] == 3
