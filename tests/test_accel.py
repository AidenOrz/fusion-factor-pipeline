# -*- coding: utf-8 -*-
"""Tests for the optional Rust acceleration path.

The contract that matters: the fast path and the Python fallback must agree
exactly. An accelerator that is subtly different is worse than no accelerator,
because the difference would show up as a mysterious study result rather than
as an error.
"""
from __future__ import annotations

import numpy as np
import pytest

from bridge import accel


def test_python_fallback_matches_rust_when_available():
    report = accel.verify_against_python(seed=7)
    if not report["available"]:
        pytest.skip("Rust extension not built in this environment")
    assert report["verified"] is True
    assert report["cases"] >= 500
    # Machine-precision agreement, not "close enough".
    assert report["max_abs_ic_diff"] < 1e-12
    assert report["max_abs_rank_diff"] == 0.0


def test_bottom_excluded_matches_pandas_semantics():
    scores = [0.9, 0.1, 0.5, 0.3, 0.7, 0.2, 0.8, 0.4, 0.6, 0.0]
    flags = accel.bottom_excluded(scores, 0.1)
    assert sum(flags) == 1
    assert flags[9] is True or flags[9] == True


def test_change_rate_uses_previous_denominator():
    # Two of yesterdays three survive => 1/3 change.
    rate = accel.membership_change_rate(["a", "b", "c"], ["a", "b", "d"])
    assert rate == pytest.approx(1.0 / 3.0)


def test_change_rate_is_none_for_empty_previous():
    assert accel.membership_change_rate([], ["a"]) is None


def test_spearman_is_one_for_a_monotone_transform():
    scores = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
    target = [10.0, 20.0, 30.0, 40.0, 50.0, 60.0]
    assert accel.spearman_ic(scores, target) == pytest.approx(1.0)


def test_spearman_rejects_a_constant_side():
    assert accel.spearman_ic([1.0] * 10, list(range(10))) is None


def test_spearman_rejects_too_few_names():
    assert accel.spearman_ic([1.0, 2.0], [2.0, 1.0]) is None


def test_churn_series_returns_one_value_per_transition():
    series = accel.churn_series([["a"], ["a"], ["b"]])
    assert len(series) == 2
    assert series[0] == pytest.approx(0.0)
    assert series[1] == pytest.approx(1.0)


def test_fallback_is_used_when_extension_is_absent(monkeypatch):
    # Simulate a machine without the Rust toolchain.
    monkeypatch.setattr(accel, "_MODULE", None)
    monkeypatch.setattr(accel, "_LOAD_ATTEMPTED", True)
    # ceil(0.34 * 3) = 2, so the two lowest scores are excluded and the
    # highest one survives. The cut is a ceiling on purpose: 10% of 2,633
    # names must still exclude 264 names, not 263.
    flags = accel.bottom_excluded([3.0, 1.0, 2.0], 0.34)
    assert flags == [False, True, True]


def test_status_reports_absence_without_raising():
    status = accel.rust_status()
    assert set(status) == {"available", "directory", "error"}
    assert isinstance(status["available"], bool)
