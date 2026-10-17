# -*- coding: utf-8 -*-
"""Divergent-set return profile: snapshot industry vs SW2021 PIT industry.

Background: docs/INDUSTRY_PIT_2026-10-18.md judged the snapshot hazard closed on
overlap, rank correlation, and a *state* profile of the divergent sets. The
*return* profile could not be computed then, because the forward window carried
no labels at all. Labels now exist for 39 forward days, so the missing half of
the evidence can be produced.

What is measured
-----------------
For each date, two scorers (snapshot-industry neutralisation and PIT-industry
neutralisation) each exclude the bottom 10%. That splits the cross-section into:

  both_excluded   scored bottom by both
  snap_only       excluded only under the snapshot taxonomy
  pit_only        excluded only under the PIT taxonomy
  retained        excluded by neither

If the divergent sets carried materially different realised returns, the
taxonomy choice would be an economic decision, not a labelling detail. If they
are within noise of each other, the earlier "hazard closed" verdict gains the
return-side support it was missing.

Honest limit: 39 labelled days on a 20-day label is a very small sample. This
report is descriptive and cannot promote or reject the industry switch.
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

PROJECT_ROOT = Path(__file__).resolve().parents[1]
# These are the two INDEPENDENTLY TRAINED scorings from the sensitivity study:
# one neutralised with the snapshot industry column, one with SW2021 PIT.
# Do not substitute runs/forward_shadow_*_pit here: that run only ATTACHES the
# PIT column to a snapshot-trained score, so its predictions are identical to
# the snapshot run and the divergent sets would come out empty.
DEFAULT_SNAP = PROJECT_ROOT / "artifacts" / "sw_pit_scores_snapshot.parquet"
DEFAULT_PIT = PROJECT_ROOT / "artifacts" / "sw_pit_scores_sw.parquet"
DEFAULT_LABELS = PROJECT_ROOT / "runs" / "forward_labels_2026-10-18" / "forward_labels.parquet"
DEFAULT_OUT = PROJECT_ROOT / "runs" / "industry_divergence_returns_2026-10-18"

EXCLUSION_FRACTION = 0.10


def _excluded(scores: pd.DataFrame, fraction: float = EXCLUSION_FRACTION) -> pd.DataFrame:
    frame = scores[["date", "code", "prediction"]].dropna(subset=["prediction"]).copy()
    sizes = frame.groupby("date")["code"].transform("size")
    rank_asc = frame.groupby("date")["prediction"].rank(ascending=True, method="first")
    frame["excluded"] = rank_asc <= np.ceil(fraction * sizes)
    return frame[["date", "code", "excluded"]]


def build_groups(
    snap_scores: pd.DataFrame,
    pit_scores: pd.DataFrame,
    labels: pd.DataFrame,
    fraction: float = EXCLUSION_FRACTION,
) -> pd.DataFrame:
    """Join two scorings and attach realised excess returns."""

    snap = _excluded(snap_scores, fraction).rename(columns={"excluded": "excluded_snap"})
    pit = _excluded(pit_scores, fraction).rename(columns={"excluded": "excluded_pit"})
    merged = snap.merge(pit, on=["date", "code"], how="inner")
    merged = merged.merge(
        labels[["date", "code", "y_excess20"]], on=["date", "code"], how="left"
    )
    merged["group"] = np.select(
        [
            merged["excluded_snap"] & merged["excluded_pit"],
            merged["excluded_snap"] & ~merged["excluded_pit"],
            ~merged["excluded_snap"] & merged["excluded_pit"],
        ],
        ["both_excluded", "snap_only", "pit_only"],
        default="retained",
    )
    return merged


def profile(merged: pd.DataFrame) -> dict[str, Any]:
    labelled = merged.dropna(subset=["y_excess20"])
    rows: dict[str, Any] = {}
    for group, frame in labelled.groupby("group", sort=True):
        daily = frame.groupby("date")["y_excess20"].mean()
        rows[str(group)] = {
            "rows": int(len(frame)),
            "days": int(daily.size),
            "mean_excess": float(frame["y_excess20"].mean()),
            "mean_of_daily_means": float(daily.mean()) if daily.size else None,
            "daily_mean_std": float(daily.std(ddof=1)) if daily.size > 1 else None,
            "positive_day_share": float((daily > 0).mean()) if daily.size else None,
        }
    divergence = None
    only_snap = labelled.loc[labelled["group"] == "snap_only", "y_excess20"]
    only_pit = labelled.loc[labelled["group"] == "pit_only", "y_excess20"]
    if len(only_snap) and len(only_pit):
        from scipy.stats import mannwhitneyu

        test = mannwhitneyu(only_snap, only_pit, alternative="two-sided")
        divergence = {
            "snap_only_mean_excess": float(only_snap.mean()),
            "pit_only_mean_excess": float(only_pit.mean()),
            "difference_snap_minus_pit": float(only_snap.mean() - only_pit.mean()),
            "mannwhitney_pvalue": float(test.pvalue),
            "note": (
                "A large p-value means the two divergent tails are statistically "
                "indistinguishable on realised returns; it does not prove equality."
            ),
        }
    return {
        "labelled_rows": int(len(labelled)),
        "labelled_days": int(labelled["date"].nunique()),
        "groups": rows,
        "divergence_test": divergence,
        "sample_warning": (
            "39 labelled days on a 20-day overlapping label is a very small, highly "
            "autocorrelated sample. Descriptive only."
        ),
    }


def run(
    *,
    snap_path: Path | str = DEFAULT_SNAP,
    pit_path: Path | str = DEFAULT_PIT,
    labels_path: Path | str = DEFAULT_LABELS,
    out_dir: Path | str = DEFAULT_OUT,
) -> dict[str, Any]:
    frames = []
    for path in (snap_path, pit_path, labels_path):
        frame = pd.read_parquet(path)
        frame["date"] = pd.to_datetime(frame["date"], errors="raise")
        frame["code"] = frame["code"].astype(str).str.zfill(6)
        frames.append(frame)
    snap_scores, pit_scores, labels = frames

    merged = build_groups(snap_scores, pit_scores, labels)
    payload = {
        "classification": "industry_divergence_return_profile",
        "snapshot_scores_file": str(snap_path),
        "pit_scores_file": str(pit_path),
        "labels_file": str(labels_path),
        "exclusion_fraction": EXCLUSION_FRACTION,
        "profile": profile(merged),
        "production_authorized": False,
    }
    output = Path(out_dir)
    output.mkdir(parents=True, exist_ok=True)
    (output / "divergence_return_profile.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + chr(10),
        encoding="utf-8",
    )
    merged.to_parquet(output / "divergence_groups.parquet", index=False)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Divergent-set return profile")
    parser.add_argument("--snap", default=str(DEFAULT_SNAP))
    parser.add_argument("--pit", default=str(DEFAULT_PIT))
    parser.add_argument("--labels", default=str(DEFAULT_LABELS))
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT))
    args = parser.parse_args()
    payload = run(
        snap_path=args.snap,
        pit_path=args.pit,
        labels_path=args.labels,
        out_dir=args.out_dir,
    )
    print(json.dumps(payload["profile"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
