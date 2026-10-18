# -*- coding: utf-8 -*-
"""Tests for the forward feature-coverage guard.

This guard exists because the pipeline once scored happily from a window where 8
of 37 frozen features were absent and 28 more were entirely NaN. The failure was
silent: all-NaN inputs make LightGBM return a near-constant, and the resulting IC
looked plausible. These tests pin the guard against that exact shape.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from bridge.forward_scoring import assert_feature_coverage


def _forward(rows: int = 400, *, fill: float | None = 1.0) -> pd.DataFrame:
    rng = np.random.RandomState(3)
    frame = pd.DataFrame({
        "date": pd.bdate_range("2026-07-20", periods=rows),
        "code": [f"{i:06d}" for i in rng.randint(0, 20, size=rows)],
        "universe_b2": True,
        "good_a": rng.normal(size=rows),
        "good_b": rng.normal(size=rows),
    })
    if fill is None:
        frame["empty_c"] = np.nan
    else:
        frame["empty_c"] = fill
    return frame


def test_passes_when_every_feature_is_present_and_populated():
    frame = _forward()
    shares = assert_feature_coverage(frame, ["good_a", "good_b"])
    assert shares["good_a"] == 1.0
    assert shares["good_b"] == 1.0


def test_rejects_a_missing_feature():
    frame = _forward()
    with pytest.raises(ValueError, match="absent columns"):
        assert_feature_coverage(frame, ["good_a", "not_in_the_panel"])


def test_rejects_an_all_nan_feature():
    # This is the exact failure that produced an invalid forward IC.
    frame = _forward(fill=None)
    with pytest.raises(ValueError, match="insufficient coverage"):
        assert_feature_coverage(frame, ["good_a", "empty_c"])


def test_reports_every_offender_not_just_the_first():
    frame = _forward()
    frame["empty_d"] = np.nan
    with pytest.raises(ValueError) as excinfo:
        assert_feature_coverage(frame, ["good_a", "empty_c", "empty_d", "nope"])
    message = str(excinfo.value)
    assert "absent columns (1)" in message
    assert "insufficient coverage (2)" in message


def test_partial_coverage_below_threshold_is_rejected():
    frame = _forward()
    # Only a fifth of the rows carry a value.
    mask = np.zeros(len(frame), dtype=bool)
    mask[: len(frame) // 5] = True
    frame["sparse"] = np.where(mask, 1.0, np.nan)
    with pytest.raises(ValueError, match="insufficient coverage"):
        assert_feature_coverage(frame, ["sparse"], min_non_null_share=0.5)


def test_coverage_is_measured_inside_the_declared_window():
    frame = _forward()
    # Values exist historically but not in the forward window.
    cutoff = frame["date"].iloc[len(frame) // 2]
    frame.loc[frame["date"] >= cutoff, "late_gap"] = np.nan
    frame.loc[frame["date"] < cutoff, "late_gap"] = 1.0
    with pytest.raises(ValueError, match="insufficient coverage"):
        assert_feature_coverage(frame, ["late_gap"], window_start=cutoff)


def test_coverage_restricted_to_b2_members_when_present():
    frame = _forward()
    frame["universe_b2"] = False
    frame.loc[frame.index[:20], "universe_b2"] = True
    frame.loc[frame.index[20:], "b2_only_gap"] = np.nan
    frame.loc[frame.index[:20], "b2_only_gap"] = 1.0
    # Inside B2 the column is fully populated, so this must pass.
    shares = assert_feature_coverage(frame, ["b2_only_gap"])
    assert shares["b2_only_gap"] == 1.0


def test_threshold_is_configurable():
    frame = _forward()
    mask = np.zeros(len(frame), dtype=bool)
    mask[: len(frame) // 2] = True
    frame["half"] = np.where(mask, 1.0, np.nan)
    assert assert_feature_coverage(frame, ["half"], min_non_null_share=0.45)["half"] > 0.45
    with pytest.raises(ValueError):
        assert_feature_coverage(frame, ["half"], min_non_null_share=0.95)
