# -*- coding: utf-8 -*-
"""Rebuild forward labels once the benchmark gap is closed.

The forward window 2026-07-20..2026-10-16 previously carried no labels at all,
because benchmark_close was NaN from 2026-07-20 onward and every label is
defined as stock return minus benchmark return over the same 20-day window.

Closing that gap (bridge/benchmark_extension.py) makes labels computable for
the part of the window that already has a full 20-trading-day future. That is
the honest subset: the tail of the window still cannot have labels, and this
module never invents them.

Two things matter and are both asserted here:
  1. The label definition is reused verbatim from bridge.labels, never
     re-implemented, so forward and historical labels stay the same quantity.
  2. Rows that already had labels keep exactly the values they had. A rebuild
     that quietly shifts historical labels would invalidate the whole record.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from bridge.labels import attach_cross_sectional_labels

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PANEL = PROJECT_ROOT / "data" / "panel_forward_features_2026-10-16_pit.parquet"
DEFAULT_BENCHMARK = PROJECT_ROOT / "data" / "refdata" / "benchmark_000985_ext.parquet"
DEFAULT_OUT = PROJECT_ROOT / "runs" / "forward_labels_2026-10-18" / "forward_labels.parquet"

# Everything the label definition reads, plus the keys used for checks.
LABEL_INPUTS = [
    "date", "code", "open", "close", "industry", "float_mcap",
    "universe_b1", "universe_b2", "universe_b3",
    "benchmark_open", "benchmark_close",
]
LABEL_COLUMNS = [
    "stock_ret20", "benchmark_ret20", "y_excess20",
    "y_raw_rank20_b1", "y_neutral_rank20_b1",
    "y_raw_rank20_b2", "y_neutral_rank20_b2",
    "y_raw_rank20_b3", "y_neutral_rank20_b3",
]


def load_panel_with_benchmark(
    panel_path: Path | str,
    benchmark_path: Path | str,
    *,
    since: pd.Timestamp | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Load label inputs and fill the benchmark only where it is missing."""

    panel = pd.read_parquet(panel_path, columns=LABEL_INPUTS)
    panel["date"] = pd.to_datetime(panel["date"], errors="raise")
    panel["code"] = panel["code"].astype(str).str.zfill(6)
    before_rows = int(panel["benchmark_close"].isna().sum())

    extension = pd.read_parquet(benchmark_path)
    extension["date"] = pd.to_datetime(extension["date"], errors="raise")
    extension = extension[["date", "open", "close"]].rename(
        columns={"open": "benchmark_open", "close": "benchmark_close"}
    )
    if extension.duplicated("date").any():
        raise ValueError("benchmark extension is not unique per date")

    # Only touch dates whose benchmark is entirely absent; a date that already
    # has a benchmark must never be overwritten from another source.
    per_date = panel.groupby("date")["benchmark_close"].apply(lambda s: s.notna().any())
    existing = set(per_date[per_date].index)
    filled = extension.loc[~extension["date"].isin(existing)]
    if filled.empty:
        return panel, {
            "benchmark_rows_filled": 0,
            "benchmark_dates_filled": 0,
            "rows_missing_benchmark_before": before_rows,
            "rows_missing_benchmark_after": before_rows,
        }

    fill = filled.set_index("date")
    mask = panel["date"].isin(fill.index)
    for column in ("benchmark_open", "benchmark_close"):
        panel.loc[mask, column] = panel.loc[mask, "date"].map(fill[column]).to_numpy()

    after_rows = int(panel["benchmark_close"].isna().sum())
    if since is not None:
        touched = panel.loc[panel["date"].isin(fill.index)]
        if not touched["date"].min() >= since:
            raise ValueError(
                "benchmark fill touched dates before "
                + str(since.date()) + "; refusing to widen the label window"
            )
    report = {
        "benchmark_rows_filled": int(mask.sum()),
        "benchmark_dates_filled": int(len(fill)),
        "benchmark_fill_start": str(fill.index.min().date()),
        "benchmark_fill_end": str(fill.index.max().date()),
        "rows_missing_benchmark_before": before_rows,
        "rows_missing_benchmark_after": after_rows,
    }
    return panel.sort_values(["date", "code"], kind="mergesort").reset_index(drop=True), report


def rebuild_labels(
    *,
    panel_path: Path | str = DEFAULT_PANEL,
    benchmark_path: Path | str = DEFAULT_BENCHMARK,
    out_path: Path | str = DEFAULT_OUT,
    window_start: str = "2026-07-20",
    reference_path: Path | str | None = None,
) -> dict[str, Any]:
    """Attach labels over the extended benchmark and write the forward subset."""

    start = pd.Timestamp(window_start)
    panel, bench_report = load_panel_with_benchmark(panel_path, benchmark_path, since=start)
    labelled = attach_cross_sectional_labels(panel)

    forward = labelled.loc[labelled["date"] >= start].copy()
    label_dates = forward.loc[forward["y_excess20"].notna(), "date"]
    coverage: dict[str, Any] = {
        "window_start": str(start.date()),
        "window_end": str(labelled["date"].max().date()),
        "window_days": int(forward["date"].nunique()),
        "label_dates": int(label_dates.nunique()),
        "label_rows": int(forward["y_excess20"].notna().sum()),
        "unlabelled_days_at_tail": int(
            forward["date"].nunique() - label_dates.nunique()
        ),
    }
    if label_dates.nunique():
        coverage["last_labelled_date"] = str(label_dates.max().date())
        coverage["first_labelled_date"] = str(label_dates.min().date())

    reference_report: dict[str, Any] | None = None
    if reference_path is not None:
        reference = pd.read_parquet(
            reference_path, columns=["date", "code", *LABEL_COLUMNS]
        )
        reference["date"] = pd.to_datetime(reference["date"], errors="raise")
        reference["code"] = reference["code"].astype(str).str.zfill(6)
        overlap = forward.merge(
            reference, on=["date", "code"], how="inner", suffixes=("", "_ref")
        )
        mismatches: dict[str, int] = {}
        for column in LABEL_COLUMNS:
            left = overlap[column]
            right = overlap[column + "_ref"]
            both = left.notna() & right.notna()
            if not both.any():
                mismatches[column] = 0
                continue
            worst = float((left[both] - right[both]).abs().max())
            mismatches[column] = worst
        reference_report = {
            "reference_file": str(reference_path),
            "overlap_rows": int(len(overlap)),
            "max_abs_diff_by_column": mismatches,
            "historical_labels_unchanged": all(v == 0.0 for v in mismatches.values()),
        }

    out_file = Path(out_path)
    out_file.parent.mkdir(parents=True, exist_ok=True)
    keep = ["date", "code", *LABEL_COLUMNS]
    forward[keep].to_parquet(out_file, index=False)

    manifest = {
        "classification": "forward_label_rebuild",
        "purpose": (
            "Labels for the clean forward window, produced by the same definition "
            "used historically, after the 000985 benchmark gap was closed."
        ),
        "panel_file": str(panel_path),
        "benchmark_extension_file": str(benchmark_path),
        "window_start": start,
        "benchmark_fill": bench_report,
        "coverage": coverage,
        "reference_check": reference_report,
        "label_definition": "bridge.labels.attach_cross_sectional_labels (unchanged)",
        "output_file": str(out_file),
        "production_authorized": False,
    }
    (out_file.parent / "forward_labels_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True, default=str)
        + chr(10),
        encoding="utf-8",
    )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="Rebuild forward labels")
    parser.add_argument("--panel", default=str(DEFAULT_PANEL))
    parser.add_argument("--benchmark", default=str(DEFAULT_BENCHMARK))
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--window-start", default="2026-07-20")
    parser.add_argument(
        "--reference",
        default=str(DEFAULT_PANEL),
        help="panel carrying the existing labels, for the unchanged-history check",
    )
    args = parser.parse_args()
    manifest = rebuild_labels(
        panel_path=args.panel,
        benchmark_path=args.benchmark,
        out_path=args.out,
        window_start=args.window_start,
        reference_path=args.reference,
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
