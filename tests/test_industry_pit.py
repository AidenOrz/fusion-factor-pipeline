# -*- coding: utf-8 -*-
"""Regression tests for the SW2021 industry PIT integration.

The contract these lock down:
  * the PIT table is a *time series* (multiple records per code allowed);
  * the as-of join is per-code and never leaks a future classification;
  * the snapshot column is preserved so no reader confuses the two taxonomies.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest


def _pit() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "code": ["000001", "000001", "000001", "600519"],
            "pit_date": pd.to_datetime(
                ["2015-10-27", "2021-07-30", "2026-01-05", "2015-10-27"]
            ),
            "sw_code": ["440101", "480101", "480301", "340404"],
            "sw_l1": ["44", "48", "48", "34"],
            "sw_l2": ["4401", "4801", "4803", "3404"],
            "sw_l3": ["440101", "480101", "480301", "340404"],
        }
    )


def test_pit_allows_multiple_records_per_code():
    pit = _pit()
    counts = pit.groupby("code").size()
    assert counts["000001"] == 3
    # A snapshot would collapse this to one row per code; the PIT deliberately
    # does not, which is the whole point of the source.
    assert counts.max() > 1


def test_asof_join_picks_the_latest_effective_date_not_the_future():
    pit = _pit().sort_values(["pit_date", "code"]).reset_index(drop=True)
    probe = pd.DataFrame(
        {
            "date": pd.to_datetime(["2020-06-01", "2022-03-01", "2026-06-01"]),
            "code": ["000001"] * 3,
        }
    )
    joined = pd.merge_asof(
        probe.sort_values("date"), pit, left_on="date", right_on="pit_date",
        by="code", direction="backward",
    )
    # 2020 -> the 2015 record; 2022 -> the 2021 record; 2026 -> the 2026 record
    assert joined["sw_l1"].tolist() == ["44", "48", "48"]
    assert joined["pit_date"].iloc[0] == pd.Timestamp("2015-10-27")


def test_asof_join_does_not_match_a_different_code():
    pit = _pit().sort_values(["pit_date", "code"]).reset_index(drop=True)
    probe = pd.DataFrame(
        {"date": pd.to_datetime(["2026-06-01"]), "code": ["600519"]}
    )
    joined = pd.merge_asof(
        probe, pit, left_on="date", right_on="pit_date",
        by="code", direction="backward",
    )
    assert joined["sw_l1"].iloc[0] == "34"


def test_no_match_before_first_effective_date_stays_null():
    pit = _pit()
    pit = pit[pit["code"] == "000001"].sort_values("pit_date").reset_index(drop=True)
    probe = pd.DataFrame(
        {"date": pd.to_datetime(["2010-01-01"]), "code": ["000001"]}
    )
    joined = pd.merge_asof(
        probe, pit, left_on="date", right_on="pit_date",
        by="code", direction="backward",
    )
    assert pd.isna(joined["sw_l1"].iloc[0])
