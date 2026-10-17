# -*- coding: utf-8 -*-
"""Prove the frozen scorer still reproduces its recorded predictions exactly.

This is the Phase E gate that must pass before any forward observation counts:
if rebuilding the registered estimator from the frozen config cannot reproduce
the historical predictions exactly, then a forward score produced by the same
code path is not comparable to the historical record.

What is compared
----------------
For every model in the frozen config, the estimator is refitted on the
registered selection period (same estimator, same seeds, same weights) and then
predicts the registered evaluation period. The result is joined to the stored
predictions.parquet on (date, code) and must match to floating-point exactness.

What is NOT done
----------------
No retuning, no reseeding, no use of evaluation labels for fitting. The stored
predictions are the reference; this module only checks reproducibility.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from bridge.ablation import _read_features
from bridge.evaluation import _date_range, _fit_predict, _load_frozen, _load_panel

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FROZEN = PROJECT_ROOT / "config" / "factor_pool_b2_frozen_2026-08-28.json"
DEFAULT_RUN = PROJECT_ROOT / "runs" / "factor_evaluation_v1_b2_frozen_2026-08-28"
DEFAULT_PANEL = PROJECT_ROOT / "data" / "panel_fusion_b2_adjudicated_2026-08-27.parquet"
DEFAULT_OUT = PROJECT_ROOT / "runs" / "scorer_equivalence_2026-10-18"

# Tolerance is exact equality; a looser tolerance would hide the very drift this
# check exists to detect. The value is reported so a near miss stays visible.
EXACT_TOLERANCE = 0.0


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def compare_predictions(
    rebuilt: pd.DataFrame,
    recorded: pd.DataFrame,
    *,
    tolerance: float = EXACT_TOLERANCE,
) -> dict[str, Any]:
    """Join on (date, code) and compare the prediction columns."""

    left = rebuilt[["date", "code", "prediction"]].copy()
    right = recorded[["date", "code", "prediction"]].copy()
    for frame in (left, right):
        frame["date"] = pd.to_datetime(frame["date"], errors="raise")
        frame["code"] = frame["code"].astype(str).str.zfill(6)
    if left.duplicated(["date", "code"]).any() or right.duplicated(["date", "code"]).any():
        raise ValueError("prediction frames are not keyed by (date, code)")
    joined = left.merge(
        right,
        on=["date", "code"],
        how="outer",
        suffixes=("_rebuilt", "_recorded"),
        indicator=True,
    )
    both = joined.loc[joined["_merge"] == "both"].copy()
    only_rebuilt = int((joined["_merge"] == "left_only").sum())
    only_recorded = int((joined["_merge"] == "right_only").sum())
    if both.empty:
        raise ValueError("rebuilt and recorded predictions share no key")
    diff = (both["prediction_rebuilt"] - both["prediction_recorded"]).abs()
    violations = both.loc[diff > tolerance].copy()
    violations["abs_diff"] = (
        violations["prediction_rebuilt"] - violations["prediction_recorded"]
    ).abs()
    worst = (
        violations.nlargest(5, "abs_diff")[
            ["date", "code", "prediction_rebuilt", "prediction_recorded", "abs_diff"]
        ].to_dict(orient="records")
        if len(violations)
        else []
    )
    return {
        "rows_compared": int(len(both)),
        "rows_only_rebuilt": only_rebuilt,
        "rows_only_recorded": only_recorded,
        "max_abs_diff": float(diff.max()),
        "mean_abs_diff": float(diff.mean()),
        "rows_over_tolerance": int(len(violations)),
        "tolerance": float(tolerance),
        "key_sets_match": only_rebuilt == 0 and only_recorded == 0,
        "equivalent": bool(
            only_rebuilt == 0 and only_recorded == 0 and len(violations) == 0
        ),
        "worst_examples": worst,
    }


def run(
    *,
    frozen_path: Path | str = DEFAULT_FROZEN,
    run_dir: Path | str = DEFAULT_RUN,
    panel_path: Path | str = DEFAULT_PANEL,
    out_dir: Path | str = DEFAULT_OUT,
    models: list[str] | None = None,
) -> dict[str, Any]:
    frozen_path = Path(frozen_path)
    run_path = Path(run_dir)
    panel_path = Path(panel_path)
    frozen = _load_frozen(frozen_path)
    features = _read_features(PROJECT_ROOT / frozen["features_file"])
    panel = _load_panel(panel_path, features)
    selection_range = _date_range(frozen["selection_period"])
    evaluation_range = _date_range(frozen["evaluation_period"])

    results: dict[str, Any] = {}
    for model in frozen["models"]:
        model_id = model["id"]
        if models and model_id not in models:
            continue
        predictions_path = run_path / model_id / "predictions.parquet"
        if not predictions_path.is_file():
            results[model_id] = {"skipped": "missing " + str(predictions_path)}
            continue
        recorded = pd.read_parquet(predictions_path)
        rebuilt, metrics = _fit_predict(
            panel, features, model["target"], selection_range, evaluation_range
        )
        comparison = compare_predictions(rebuilt, recorded)
        recorded_metrics_path = run_path / model_id / "metrics.json"
        recorded_metrics = (
            json.loads(recorded_metrics_path.read_text(encoding="utf-8"))
            if recorded_metrics_path.is_file()
            else {}
        )
        recorded_ic = (recorded_metrics.get("overall_quality") or {}).get(
            "mean_daily_spearman_ic"
        )
        rebuilt_ic = (metrics.get("overall_quality") or {}).get("mean_daily_spearman_ic")
        comparison["recorded_ic"] = recorded_ic
        comparison["rebuilt_ic"] = rebuilt_ic
        comparison["ic_matches"] = bool(
            recorded_ic is not None
            and rebuilt_ic is not None
            and float(recorded_ic) == float(rebuilt_ic)
        )
        comparison["predictions_file"] = str(predictions_path)
        comparison["predictions_sha256"] = _sha256(predictions_path)
        comparison["train_rows"] = metrics["train_rows"]
        results[model_id] = comparison
        print(
            "[" + model_id + "] rows=" + str(comparison["rows_compared"])
            + " max_abs_diff=" + format(comparison["max_abs_diff"], ".3e")
            + " equivalent=" + str(comparison["equivalent"]),
            flush=True,
        )

    payload = {
        "classification": "frozen_scorer_equivalence_check",
        "purpose": (
            "Phase E gate: a forward score is only comparable to the historical "
            "record if the registered estimator reproduces it exactly."
        ),
        "frozen_config_file": str(frozen_path),
        "panel_file": str(panel_path),
        "panel_sha256": _sha256(panel_path),
        "features_file": frozen["features_file"],
        "feature_count": len(features),
        "selection_period": frozen["selection_period"],
        "evaluation_period": frozen["evaluation_period"],
        "models": results,
        "all_models_equivalent": all(
            result.get("equivalent") is True for result in results.values()
        ),
        "evaluation_labels_used_for_fit": False,
        "production_authorized": False,
    }
    output = Path(out_dir)
    output.mkdir(parents=True, exist_ok=True)
    (output / "equivalence_report.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + chr(10),
        encoding="utf-8",
    )
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Check the frozen scorer reproduces its recorded predictions"
    )
    parser.add_argument("--frozen", default=str(DEFAULT_FROZEN))
    parser.add_argument("--run-dir", default=str(DEFAULT_RUN))
    parser.add_argument("--panel", default=str(DEFAULT_PANEL))
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT))
    parser.add_argument("--models", default=None, help="comma separated model ids")
    args = parser.parse_args()
    model_ids = (
        [item.strip() for item in args.models.split(",") if item.strip()]
        if args.models
        else None
    )
    payload = run(
        frozen_path=args.frozen,
        run_dir=args.run_dir,
        panel_path=args.panel,
        out_dir=args.out_dir,
        models=model_ids,
    )
    print(json.dumps(
        {
            "all_models_equivalent": payload["all_models_equivalent"],
            "out_dir": str(args.out_dir),
        },
        ensure_ascii=False,
        indent=2,
    ))
    return 0 if payload["all_models_equivalent"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
