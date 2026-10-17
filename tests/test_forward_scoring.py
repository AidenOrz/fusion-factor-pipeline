# -*- coding: utf-8 -*-
"""Regression tests for bridge.forward_scoring.

The critical property is that forward scoring cannot see outcomes: the forward
projection must carry no label column at all, and the fit must be confined to
the registered selection window.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from bridge.forward_scoring import (
    FORWARD_COLUMNS,
    FEATURE_COLUMNS_EXCLUDED,
    load_scoring_frames,
)


def _panel(tmp_path, *, with_labels: bool = True, days: int = 60):
    rng = np.random.RandomState(11)
    codes = [f"{i:06d}" for i in range(1, 21)]
    dates = pd.bdate_range("2023-06-01", periods=days)
    rows = []
    for date in dates:
        for code in codes:
            row = {
                "date": date,
                "code": code,
                "panel_scope": "full_market",
                "universe_b2": True,
                "industry": "BANK",
                "float_mcap": float(rng.uniform(1e9, 5e9)),
                "ret_20d": float(rng.normal(0, 0.05)),
                "adv20": float(rng.uniform(1e7, 1e8)),
                "is_st": False,
                "f_a": float(rng.normal()),
                "f_b": float(rng.normal()),
            }
            if with_labels:
                row["y_excess20"] = float(rng.normal(0, 0.05))
                row["y_raw_rank20_b2"] = float(rng.uniform(0, 1))
                row["y_neutral_rank20_b2"] = float(rng.uniform(0, 1))
            rows.append(row)
    path = tmp_path / "panel.parquet"
    pd.DataFrame(rows).to_parquet(path, index=False)
    return path


def test_forward_projection_excludes_all_labels(tmp_path):
    path = _panel(tmp_path)
    history, forward = load_scoring_frames(path, ["f_a", "f_b"])
    assert "y_neutral_rank20_b2" in history.columns
    leaked = [c for c in forward.columns if c.startswith(("y_", "stock_ret", "benchmark_ret"))]
    assert leaked == []
    assert "y_neutral_rank20_b2" not in forward.columns


def test_forward_projection_is_exactly_the_allowed_columns(tmp_path):
    path = _panel(tmp_path)
    _, forward = load_scoring_frames(path, ["f_a", "f_b"])
    expected = set(FORWARD_COLUMNS) | {"f_a", "f_b"}
    assert set(forward.columns) == expected


def test_load_rejects_non_full_market_scope(tmp_path):
    path = _panel(tmp_path)
    frame = pd.read_parquet(path)
    frame.loc[0, "panel_scope"] = "events"
    frame.to_parquet(path, index=False)
    with pytest.raises(ValueError, match="full_market"):
        load_scoring_frames(path, ["f_a", "f_b"])


def test_alpha056_is_excluded_from_features():
    # It cannot be reproduced from the local CSVs, so scoring must not ask for it.
    assert "alpha056" in FEATURE_COLUMNS_EXCLUDED
