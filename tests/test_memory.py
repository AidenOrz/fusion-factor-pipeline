# -*- coding: utf-8 -*-
"""Tests for the memory-discipline helpers.

These pin the properties the memory work relies on: a projection must read only
the requested columns, shrinking must never grow a column, and streaming must
keep the peak at one row group.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from bridge import memory


def _panel(tmp_path, rows: int = 500):
    rng = np.random.RandomState(5)
    frame = pd.DataFrame({
        "date": pd.bdate_range("2026-01-01", periods=rows),
        "code": [f"{i:06d}" for i in rng.randint(0, 5, size=rows)],
        "industry": rng.choice(["BANK", "TECH", "PHARMA"], size=rows),
        "value": rng.normal(size=rows),
        "sparse": rng.normal(size=rows),
    })
    path = tmp_path / "panel.parquet"
    frame.to_parquet(path, index=False)
    return path, frame


def test_projection_reads_only_requested_columns(tmp_path):
    path, _ = _panel(tmp_path)
    frame = memory.read_panel(path, columns=["date", "code", "value"])
    assert list(frame.columns) == ["date", "code", "value"]


def test_projection_actually_saves_memory(tmp_path):
    path, full = _panel(tmp_path)
    projected = memory.read_panel(path, columns=["date", "value"])
    assert memory.frame_mb(projected) < memory.frame_mb(full)


def test_shrink_converts_low_cardinality_but_not_key_columns(tmp_path):
    path, _ = _panel(tmp_path)
    frame = memory.read_panel(path, columns=["date", "code", "industry"])
    shrunk = memory.shrink(frame)
    # industry has 3 distinct values over 500 rows, so it must become categorical.
    assert str(shrunk["industry"].dtype) == "category"
    # code is a join key and must stay a plain string.
    assert str(shrunk["code"].dtype) != "category"


def test_shrink_never_grows_a_column(tmp_path):
    # A near-unique string column must be left alone: converting it would make
    # it larger, which is the mistake category_threshold exists to prevent.
    rows = 200
    frame = pd.DataFrame({
        "unique_text": [f"value-{i}-{i * 7}" for i in range(rows)],
        "small": ["a"] * rows,
    })
    before = int(frame["unique_text"].memory_usage(deep=True))
    shrunk = memory.shrink(frame.copy())
    after = int(shrunk["unique_text"].memory_usage(deep=True))
    assert after <= before * 1.05
    assert str(shrunk["small"].dtype) == "category"


def test_streaming_yields_every_row_exactly_once(tmp_path):
    path, frame = _panel(tmp_path, rows=1000)
    chunks = list(memory.iter_row_groups(path, columns=["date", "code", "value"]))
    total = sum(len(c) for c in chunks)
    assert total == len(frame)
    assert len(chunks) >= 1


def test_streaming_peak_is_below_the_whole_frame(tmp_path):
    path, frame = _panel(tmp_path, rows=2000)
    biggest = max(memory.frame_mb(c) for c in memory.iter_row_groups(path))
    assert biggest <= memory.frame_mb(frame)


def test_write_panel_creates_many_row_groups(tmp_path):
    path, frame = _panel(tmp_path, rows=1000)
    info = memory.write_panel(frame, tmp_path / "out.parquet", row_group_size=200)
    assert info["row_groups"] >= 4
    assert info["rows"] == 1000


def test_write_then_stream_roundtrips(tmp_path):
    path, frame = _panel(tmp_path, rows=900)
    target = tmp_path / "out.parquet"
    memory.write_panel(frame, target, row_group_size=300)
    chunks = list(memory.iter_row_groups(target, row_group_size=300))
    assert sum(len(c) for c in chunks) == len(frame)


def test_release_clears_frames():
    frame = pd.DataFrame({"a": np.arange(1000)})
    memory.release(frame)
    assert len(frame) == 0


def test_estimate_projection_reports_missing_columns(tmp_path):
    path, _ = _panel(tmp_path)
    report = memory.estimate_projection(path, ["date", "value", "does_not_exist"])
    assert report["missing"] == ["does_not_exist"]
    assert report["requested"] == 2
    assert report["estimated_mb"] > 0
