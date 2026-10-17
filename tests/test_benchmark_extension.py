# -*- coding: utf-8 -*-
"""Tests for the 000985 benchmark extension.

The whole point of this module is distrust: it may only accept a source that
reproduces the local archive exactly. These tests pin that behaviour, because a
lenient benchmark check would silently corrupt every forward label.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from bridge.benchmark_extension import (
    BenchmarkSourceError,
    build_extension,
    verify_against_local,
)


class _StubResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _StubSession:
    """Mimics the Tencent payload shape without touching the network."""

    def __init__(self, frame):
        self.frame = frame
        self.calls = 0

    def get(self, url, params=None, timeout=None, headers=None):
        self.calls += 1
        rows = [
            [
                row.date.strftime("%Y-%m-%d"),
                f"{row.open:.2f}",
                f"{row.close:.2f}",
                f"{row.high:.2f}",
                f"{row.low:.2f}",
                f"{row.volume:.0f}",
            ]
            for row in self.frame.itertuples()
        ]
        return _StubResponse({"data": {"sh000985": {"day": rows}}})


def _series(dates, start=5000.0, step=3.0):
    rows = []
    for index, date in enumerate(dates):
        close = start + index * step
        rows.append(
            {
                "date": pd.Timestamp(date),
                "open": round(close - 1.0, 2),
                "high": round(close + 5.0, 2),
                "low": round(close - 5.0, 2),
                "close": round(close, 2),
                "volume": 1_000_000 + index,
            }
        )
    return pd.DataFrame(rows)


def _local_and_fetched(overlap=260, extra=3):
    dates = pd.bdate_range("2024-01-01", periods=overlap + extra)
    full = _series(dates)
    local = full.iloc[:overlap][["date", "open", "close", "high", "low"]].copy()
    return local, full


def test_verify_accepts_an_exact_reproduction():
    local, fetched = _local_and_fetched()
    report = verify_against_local(fetched, local)
    assert report["verified_exact"] is True
    assert report["overlap_days"] == len(local)
    assert all(value == 0.0 for value in report["max_abs_diff"].values())


def test_verify_rejects_a_one_cent_drift():
    local, fetched = _local_and_fetched()
    fetched.loc[10, "close"] = fetched.loc[10, "close"] + 0.01
    with pytest.raises(BenchmarkSourceError, match="close"):
        verify_against_local(fetched, local)


def test_verify_rejects_a_source_that_drops_local_dates():
    local, fetched = _local_and_fetched()
    fetched = fetched.iloc[1:].reset_index(drop=True)
    with pytest.raises(BenchmarkSourceError, match="missing"):
        verify_against_local(fetched, local)


def test_verify_rejects_a_short_overlap():
    local, fetched = _local_and_fetched(overlap=10, extra=2)
    with pytest.raises(BenchmarkSourceError, match="overlapping days"):
        verify_against_local(fetched, local)


def test_verify_catches_swapped_high_and_close():
    # The Tencent payload orders close before high; a parser that assumed the
    # local column order would swap them and pass a naive look-plausible check.
    local, fetched = _local_and_fetched()
    swapped = fetched.copy()
    swapped["close"], swapped["high"] = fetched["high"].to_numpy(), fetched["close"].to_numpy()
    with pytest.raises(BenchmarkSourceError):
        verify_against_local(swapped, local)


def test_build_extension_writes_only_new_dates(tmp_path):
    local, fetched = _local_and_fetched()
    local_path = tmp_path / "bench.csv"
    local.to_csv(local_path, index=False)
    out = tmp_path / "ext.parquet"
    manifest = build_extension(
        output=out,
        local_path=local_path,
        start="2024-01-01",
        end="2026-12-31",
        session=_StubSession(fetched),
    )
    written = pd.read_parquet(out)
    assert set(written["date"]).isdisjoint(set(local["date"]))
    assert len(written) == 3
    assert manifest["verification"]["verified_exact"] is True
    # The archive must never be rewritten by the extension builder.
    assert pd.read_csv(local_path)["date"].max() == local["date"].max().strftime("%Y-%m-%d")


def test_build_extension_refuses_when_nothing_is_new(tmp_path):
    local, fetched = _local_and_fetched(overlap=260, extra=0)
    local_path = tmp_path / "bench.csv"
    local.to_csv(local_path, index=False)
    with pytest.raises(BenchmarkSourceError, match="no benchmark dates beyond"):
        build_extension(
            output=tmp_path / "ext.parquet",
            local_path=local_path,
            start="2024-01-01",
            end="2026-12-31",
            session=_StubSession(fetched),
        )
