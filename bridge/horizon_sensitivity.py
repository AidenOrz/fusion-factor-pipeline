# -*- coding: utf-8 -*-
"""Horizon sensitivity: does the score persist through days 21-40?

Pre-registered status: sensitivity ONLY. The frozen label horizon is 20 days and
this study may not change it. What it answers is narrower and legitimate:

  If the 20-day score still ranks well on days 21-40, the signal is persistent
  rather than a one-shot artefact of the label window. If it decays to zero, the
  strategy carries real 20-day-dated risk and must be rebalanced at that horizon.

Design, deliberately minimal so it cannot become a second tuning pass:
  - reuse the frozen predictions verbatim, no retraining, no reseeding
  - overlapping forward windows from the same entry, ending at +20, +40, +60
    trading days
  - report daily Spearman IC per horizon and the decay curve
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PRED = (
    PROJECT_ROOT
    / "runs/factor_evaluation_v1_b2_frozen_2026-08-28/huber_b2_neutral_rank/predictions.parquet"
)
DEFAULT_PANEL = PROJECT_ROOT / "data/panel_fusion_b2_adjudicated_2026-08-27.parquet"
DEFAULT_OUT = PROJECT_ROOT / "runs/horizon_sensitivity_2026-10-19"

HORIZONS = (20, 40, 60)
MIN_NAMES = 200


def load(panel_path: Path, pred_path: Path) -> pd.DataFrame:
    panel = pd.read_parquet(
        panel_path, columns=["date", "code", "open", "close", "universe_b2"]
    )
    panel["date"] = pd.to_datetime(panel["date"], errors="raise")
    panel["code"] = panel["code"].astype(str).str.zfill(6)
    pred = pd.read_parquet(pred_path, columns=["date", "code", "prediction"])
    pred["date"] = pd.to_datetime(pred["date"], errors="raise")
    pred["code"] = pred["code"].astype(str).str.zfill(6)
    frame = panel.merge(pred, on=["date", "code"], how="inner")
    return frame.sort_values(["code", "date"]).reset_index(drop=True)


def forward_return(frame: pd.DataFrame, horizon: int) -> pd.Series:
    """Entry is next day open; exit is the close h trading days later."""

    dates = np.sort(frame["date"].unique())
    index = {d: i for i, d in enumerate(dates)}
    n = len(dates)

    prices = frame[["code", "date", "open", "close"]]
    entry_map = prices.set_index(["code", "date"])["open"]
    exit_map = prices.set_index(["code", "date"])["close"]

    pos = frame["date"].map(index).to_numpy()
    exit_pos = pos + horizon
    valid = exit_pos < n

    entry_dates = np.full(len(frame), np.datetime64("NaT"), dtype="datetime64[ns]")
    exit_dates = np.full(len(frame), np.datetime64("NaT"), dtype="datetime64[ns]")
    entry_dates[valid] = dates[pos[valid] + 1]
    exit_dates[valid] = dates[exit_pos[valid]]

    entry = entry_map.reindex(
        pd.MultiIndex.from_arrays([frame["code"], pd.to_datetime(entry_dates)])
    ).to_numpy()
    exit_values = exit_map.reindex(
        pd.MultiIndex.from_arrays([frame["code"], pd.to_datetime(exit_dates)])
    ).to_numpy()
    out = pd.Series(exit_values / entry - 1.0, index=frame.index, dtype=float)
    out[~valid] = np.nan
    return out


def daily_ic(frame: pd.DataFrame, ret: str, min_names: int = MIN_NAMES) -> pd.Series:
    values: dict[pd.Timestamp, float] = {}
    for date, day in frame.groupby("date", sort=True):
        valid = day.dropna(subset=["prediction", ret])
        if len(valid) < min_names:
            continue
        if valid["prediction"].nunique() < 5 or valid[ret].nunique() < 2:
            continue
        values[date] = float(spearmanr(valid["prediction"], valid[ret]).statistic)
    return pd.Series(values, dtype=float)


def run(
    panel_path: Path | str = DEFAULT_PANEL,
    pred_path: Path | str = DEFAULT_PRED,
    out_dir: Path | str = DEFAULT_OUT,
) -> dict[str, Any]:
    frame = load(Path(panel_path), Path(pred_path))
    only_b2 = frame.loc[frame["universe_b2"].fillna(False)].copy()
    results: dict[str, Any] = {}
    for horizon in HORIZONS:
        only_b2["fwd"] = forward_return(only_b2, horizon)
        ics = daily_ic(only_b2, "fwd")
        results[str(horizon)] = {
            "horizon_trading_days": horizon,
            "labelled_days": int(len(ics)),
            "mean_ic": float(ics.mean()) if len(ics) else None,
            "ic_std": float(ics.std(ddof=1)) if len(ics) > 1 else None,
            "win_rate": float((ics > 0).mean()) if len(ics) else None,
            "coverage_rows": int(only_b2["fwd"].notna().sum()),
            "first_labelled": str(ics.index.min().date()) if len(ics) else None,
            "last_labelled": str(ics.index.max().date()) if len(ics) else None,
        }
        print(json.dumps(results[str(horizon)], ensure_ascii=False), flush=True)

    base = results["20"]["mean_ic"]
    payload = {
        "classification": "horizon_sensitivity",
        "status": "sensitivity_only_not_a_relabel_decision",
        "rule": (
            "The frozen horizon stays 20 trading days. A 40-day result cannot "
            "relabel the model; it can only describe persistence."
        ),
        "panel_file": str(panel_path),
        "predictions_file": str(pred_path),
        "predictions_are_frozen_and_untouched": True,
        "retrained": False,
        "horizons": results,
        "decay_vs_20d": {
            str(h): (results[str(h)]["mean_ic"] / base if base else None)
            for h in HORIZONS
        },
        "production_authorized": False,
    }
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "horizon_sensitivity.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + chr(10),
        encoding="utf-8",
    )
    return payload


def main() -> int:
    ap = argparse.ArgumentParser(description="Horizon sensitivity on frozen predictions")
    ap.add_argument("--panel", default=str(DEFAULT_PANEL))
    ap.add_argument("--pred", default=str(DEFAULT_PRED))
    ap.add_argument("--out-dir", default=str(DEFAULT_OUT))
    args = ap.parse_args()
    payload = run(args.panel, args.pred, args.out_dir)
    print(json.dumps({"decay_vs_20d": payload["decay_vs_20d"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
