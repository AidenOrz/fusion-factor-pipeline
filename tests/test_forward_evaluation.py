# -*- coding: utf-8 -*-
"""Tests for the forward label rebuild and the forward evaluation protocol."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from bridge.forward_evaluation import (
    daily_ic_series,
    evaluate,
    load_prereg,
    turnover_series,
)
from bridge.forward_labels import load_panel_with_benchmark, rebuild_labels

PREREG_PATH = (
    Path(__file__).resolve().parents[1]
    / "config"
    / "forward_observation_prereg_2026-10-18.json"
)


def _panel(tmp_path, dates, codes=("000001", "000002", "000003"), bench=None):
    rows = []
    for index, date in enumerate(dates):
        for offset, code in enumerate(codes):
            rows.append(
                {
                    "date": pd.Timestamp(date),
                    "code": code,
                    "open": 10.0 + index + offset,
                    "close": 10.0 + index + offset,
                    "industry": "BANK",
                    "float_mcap": 1e9 + offset,
                    "universe_b1": True,
                    "universe_b2": True,
                    "universe_b3": True,
                    "benchmark_open": (bench.get(date) if bench else None),
                    "benchmark_close": (bench.get(date) if bench else None),
                }
            )
    path = tmp_path / "panel.parquet"
    pd.DataFrame(rows).to_parquet(path, index=False)
    return path


def _benchmark_extension(tmp_path, dates, start=100.0):
    frame = pd.DataFrame(
        {
            "date": [pd.Timestamp(d) for d in dates],
            "open": [start + i for i in range(len(dates))],
            "close": [start + i + 1 for i in range(len(dates))],
        }
    )
    path = tmp_path / "bench_ext.parquet"
    frame.to_parquet(path, index=False)
    return path


def test_benchmark_fill_only_touches_dates_that_had_none(tmp_path):
    history = pd.bdate_range("2026-07-01", periods=10)
    forward = pd.bdate_range("2026-07-15", periods=10)
    bench = {d: 100.0 for d in history}
    panel_path = _panel(tmp_path, list(history) + list(forward), bench=bench)
    ext_path = _benchmark_extension(tmp_path, forward)

    panel, report = load_panel_with_benchmark(
        panel_path, ext_path, since=pd.Timestamp("2026-07-15")
    )
    assert report["benchmark_dates_filled"] == len(forward)
    filled = panel.loc[panel["date"].isin(forward), "benchmark_close"]
    assert filled.notna().all()
    # Historical dates keep their original value, not the extension's.
    kept = panel.loc[panel["date"].isin(history), "benchmark_close"]
    assert (kept == 100.0).all()


def test_benchmark_fill_refuses_to_rewrite_existing_dates(tmp_path):
    days = pd.bdate_range("2026-07-15", periods=10)
    panel_path = _panel(tmp_path, list(days), bench={d: 999.0 for d in days})
    ext_path = _benchmark_extension(tmp_path, days, start=100.0)
    _, report = load_panel_with_benchmark(panel_path, ext_path, since=pd.Timestamp("2026-01-01"))
    assert report["benchmark_dates_filled"] == 0


def test_rebuild_labels_produces_labels_and_leaves_history_alone(tmp_path):
    days = pd.bdate_range("2026-07-01", periods=80)
    split = 30
    bench = {d: 100.0 + i for i, d in enumerate(days[:split])}
    panel_path = _panel(tmp_path, list(days), bench=bench)
    ext_path = _benchmark_extension(tmp_path, days[split:], start=200.0)

    manifest = rebuild_labels(
        panel_path=panel_path,
        benchmark_path=ext_path,
        out_path=tmp_path / "labels.parquet",
        window_start=str(days[0].date()),
    )
    labels = pd.read_parquet(tmp_path / "labels.parquet")
    assert labels["y_excess20"].notna().any()
    # A 20-day forward label cannot exist for the last 20 trading days.
    assert manifest["coverage"]["unlabelled_days_at_tail"] == 20


def test_prereg_must_be_registered_before_results(tmp_path):
    payload = {"status": "draft", "production_authorized": False}
    path = tmp_path / "prereg.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="registered before results"):
        load_prereg(path)


def test_prereg_rejects_production_authorization(tmp_path):
    payload = {"status": "registered_before_results", "production_authorized": True}
    path = tmp_path / "prereg.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="production_authorized"):
        load_prereg(path)


def test_daily_ic_drops_thin_and_degenerate_days():
    rows = []
    rng = np.random.RandomState(3)
    thin_day = pd.Timestamp("2026-08-03")
    good_day = pd.Timestamp("2026-08-04")
    for index in range(10):
        rows.append({"date": thin_day, "code": f"{index:06d}",
                     "prediction": float(index), "y_excess20": float(index)})
    for index in range(600):
        rows.append({"date": good_day, "code": f"{index:06d}",
                     "prediction": float(rng.normal()),
                     "y_excess20": float(rng.normal())})
    table = daily_ic_series(pd.DataFrame(rows))
    thin = table.loc[table["date"] == thin_day].iloc[0]
    good = table.loc[table["date"] == good_day].iloc[0]
    assert thin["dropped_reason"] == "too_few_names"
    assert np.isnan(thin["ic"])
    assert not np.isnan(good["ic"])


def test_turnover_is_the_share_of_yesterday_still_excluded():
    frames = []
    for date, excluded in [
        (pd.Timestamp("2026-08-03"), {"a", "b"}),
        (pd.Timestamp("2026-08-04"), {"b", "c"}),
    ]:
        frames.append(pd.DataFrame({
            "date": date,
            "code": sorted({"a", "b", "c", "d"}),
            "bottom_excluded": [code in excluded for code in sorted({"a", "b", "c", "d"})],
        }))
    turnover = turnover_series(pd.concat(frames, ignore_index=True))
    assert float(turnover.iloc[0]) == 0.5


def test_evaluate_forces_exploratory_below_the_protocol_minimum(tmp_path):
    # Reuse the real protocol: the point is that the shipped thresholds, not a
    # test-local copy, refuse to promote a small sample.
    prereg = json.loads(PREREG_PATH.read_text(encoding="utf-8"))
    prereg_path = tmp_path / "prereg.json"
    prereg_path.write_text(json.dumps(prereg, ensure_ascii=False), encoding="utf-8")

    rng = np.random.RandomState(7)
    rows = []
    for day in pd.bdate_range("2026-08-03", periods=6):
        for index in range(900):
            value = float(rng.normal())
            rows.append({
                "date": day,
                "code": f"{index:06d}",
                "prediction": value,
                "y_excess20": value * 0.5 + float(rng.normal()) * 0.1,
            })
    scores = pd.DataFrame(rows)
    scores_path = tmp_path / "scores.parquet"
    scores.to_parquet(scores_path, index=False)
    labels_path = tmp_path / "labels.parquet"
    # The scores file still carries y_excess20; evaluate() must tolerate that
    # instead of producing suffixed duplicate columns.
    scores[["date", "code", "y_excess20"]].to_parquet(labels_path, index=False)

    payload = evaluate(
        scores_path=scores_path,
        labels_path=labels_path,
        prereg_path=prereg_path,
        out_dir=tmp_path / "out",
    )
    # Strong IC, but too few labelled days: the protocol must not call this a pass.
    assert payload["verdict"] == "EXPLORATORY"
    assert payload["verdict_is_exploratory"] is True
    assert payload["primary_endpoint"]["mean"] > 0.5
