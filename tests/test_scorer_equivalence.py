# -*- coding: utf-8 -*-
"""Tests for the frozen scorer equivalence gate.

The gate only has meaning if it is strict and if it can actually fail. These
tests pin both: exact reproduction passes, a single shifted score does not, and
a key mismatch is reported rather than silently ignored.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from bridge.scorer_equivalence import compare_predictions


def _frame(values, dates=None, codes=None):
    dates = dates or [pd.Timestamp("2025-01-02")] * len(values)
    codes = codes or [f"{i:06d}" for i in range(len(values))]
    return pd.DataFrame({"date": dates, "code": codes, "prediction": values})


def test_identical_predictions_are_equivalent():
    frame = _frame([0.1, 0.2, 0.3])
    report = compare_predictions(frame, frame.copy())
    assert report["equivalent"] is True
    assert report["max_abs_diff"] == 0.0
    assert report["rows_compared"] == 3


def test_a_single_ulp_shift_is_rejected():
    left = _frame([0.1, 0.2, 0.3])
    right = left.copy()
    right.loc[1, "prediction"] = np.nextafter(0.2, 1.0)
    report = compare_predictions(left, right)
    assert report["equivalent"] is False
    assert report["rows_over_tolerance"] == 1
    assert len(report["worst_examples"]) == 1


def test_missing_keys_break_equivalence():
    left = _frame([0.1, 0.2, 0.3])
    right = _frame([0.1, 0.2, 0.3, 0.4])
    report = compare_predictions(left, right)
    assert report["equivalent"] is False
    assert report["key_sets_match"] is False
    assert report["rows_only_recorded"] == 1


def test_zero_padded_codes_are_compared_as_strings():
    left = _frame([0.1], codes=["000001"])
    right = _frame([0.1], codes=["1"])
    report = compare_predictions(left, right)
    assert report["equivalent"] is True


def test_duplicate_keys_are_refused():
    left = pd.DataFrame({
        "date": [pd.Timestamp("2025-01-02")] * 2,
        "code": ["000001", "000001"],
        "prediction": [0.1, 0.2],
    })
    with pytest.raises(ValueError, match="keyed by"):
        compare_predictions(left, left.copy())


def test_disjoint_frames_raise_instead_of_passing():
    left = _frame([0.1], dates=[pd.Timestamp("2025-01-02")])
    right = _frame([0.1], dates=[pd.Timestamp("2025-01-03")])
    with pytest.raises(ValueError, match="share no key"):
        compare_predictions(left, right)
