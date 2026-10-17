# -*- coding: utf-8 -*-
"""Tests for the divergent-set return profile and the industry name map."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from bridge.industry_divergence import build_groups, profile
from bridge.industry_names import SW2021_LEVEL1_NAMES, sw2021_level1_name


def _scores(dates, codes=("000001", "000002", "000003", "000004"), values=None):
    rows = []
    for date in dates:
        for offset, code in enumerate(codes):
            rows.append(
                {
                    "date": pd.Timestamp(date),
                    "code": code,
                    "prediction": (
                        values[offset] if values else float(offset) * 0.1
                    ),
                }
            )
    return pd.DataFrame(rows)


def _labelled(dates, codes=("000001", "000002", "000003", "000004"), value=0.01):
    rows = []
    for date in dates:
        for code in codes:
            rows.append({"date": pd.Timestamp(date), "code": code, "y_excess20": value})
    return pd.DataFrame(rows)


def test_groups_are_disjoint_and_cover_every_row():
    dates = pd.bdate_range("2026-07-20", periods=4)
    snap = _scores(dates)
    # Reverse the ordering so the two taxonomies genuinely disagree.
    pit = _scores(dates, values=[0.3, 0.2, 0.1, 0.0])
    labels = _labelled(dates)
    merged = build_groups(snap, pit, labels)
    assert merged["group"].notna().all()
    assert len(merged) == len(snap)
    counts = merged["group"].value_counts()
    # With a reversed ranking and a 10% cut, each side excludes exactly one name.
    assert counts.get("snap_only", 0) == 1 * len(dates)
    assert counts.get("pit_only", 0) == 1 * len(dates)


def test_identical_scorings_produce_no_divergent_rows():
    dates = pd.bdate_range("2026-07-20", periods=3)
    scores = _scores(dates)
    merged = build_groups(scores, scores.copy(), _labelled(dates))
    assert not merged["group"].isin(["snap_only", "pit_only"]).any()


def test_profile_reports_null_divergence_when_sets_are_empty():
    dates = pd.bdate_range("2026-07-20", periods=3)
    scores = _scores(dates)
    merged = build_groups(scores, scores.copy(), _labelled(dates))
    payload = profile(merged)
    assert payload["divergence_test"] is None
    assert "very small" in payload["sample_warning"]


def test_profile_measures_realised_excess_per_group():
    dates = pd.bdate_range("2026-07-20", periods=3)
    snap = _scores(dates)
    pit = _scores(dates, values=[0.3, 0.2, 0.1, 0.0])
    labels = _labelled(dates, value=-0.02)
    payload = profile(build_groups(snap, pit, labels))
    for group in payload["groups"].values():
        assert group["mean_excess"] == pytest.approx(-0.02)


def test_sector_name_map_is_the_official_sw2021_set():
    assert len(SW2021_LEVEL1_NAMES) == 31
    # Spot checks against the table published in docs/INDUSTRY_PIT_2026-10-18.md.
    assert sw2021_level1_name(22) == "基础化工"
    assert sw2021_level1_name("27") == "电子"
    assert sw2021_level1_name("64.0") == "机械设备"


def test_unknown_codes_are_returned_unchanged_rather_than_guessed():
    assert sw2021_level1_name("99") == "99"
    assert sw2021_level1_name("ZZZ") == "ZZZ"
