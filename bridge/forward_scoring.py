# -*- coding: utf-8 -*-
"""Forward scoring with the frozen B2 candidate.

Trains the frozen scorer on the registered selection period only, then predicts
over a label-free forward window. The forward window is deliberately read with
a projection that never opens the label columns, so a scoring run cannot peek at
outcomes: the point of forward observation is to compare predictions against
realised data later, once those outcomes exist.

Frozen contract (do not change without a new preregistration):
  config/factor_pool_b2_frozen_2026-08-28.json
  features: config/factor_pool_b2_post_lofo_retry_starter_2026-08-28_features.json
  target:   y_neutral_rank20_b2 (the registered C-track scoring source)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd

from bridge.ablation import _read_features
from bridge.evaluation import ENSEMBLE_SEEDS, _date_range, _load_frozen

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FROZEN = PROJECT_ROOT / "config" / "factor_pool_b2_frozen_2026-08-28.json"
DEFAULT_PANEL = PROJECT_ROOT / "data" / "panel_fusion_full_market.parquet"
DEFAULT_OUT = PROJECT_ROOT / "runs" / "forward_shadow_scoring"

# Columns the forward window is allowed to expose. Labels are absent by
# construction so this module cannot train or score on future information.
FORWARD_COLUMNS = [
    "date",
    "code",
    "panel_scope",
    "universe_b2",
    "industry",
    "float_mcap",
    "ret_20d",
    "adv20",
    "is_st",
]

FEATURE_COLUMNS_EXCLUDED = {"alpha056"}  # not reproducible from local CSVs

# Frozen C-track scoring source (see docs/HANDOFF.md and the freeze record).
SCORING_MODEL_ID = "huber_b2_neutral_rank"


def _project_path(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else (PROJECT_ROOT / path)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_commit() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        return out.stdout.strip()
    except Exception:
        return "unknown"


def load_scoring_frames(
    panel_path: Path,
    features: list[str],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (labelled history for fitting, label-free forward rows).

    The forward projection omits every label column on purpose.
    """
    history_columns = list(
        dict.fromkeys(
            [
                "date",
                "code",
                "panel_scope",
                "universe_b2",
                "y_excess20",
                "y_raw_rank20_b2",
                "y_neutral_rank20_b2",
                *FORWARD_COLUMNS[4:],
                *features,
            ]
        )
    )
    history = pd.read_parquet(panel_path, columns=history_columns)
    history["date"] = pd.to_datetime(history["date"], errors="raise")
    history["code"] = history["code"].astype(str).str.zfill(6)

    forward_columns = list(dict.fromkeys([*FORWARD_COLUMNS, *features]))
    forward = pd.read_parquet(panel_path, columns=forward_columns)
    forward["date"] = pd.to_datetime(forward["date"], errors="raise")
    forward["code"] = forward["code"].astype(str).str.zfill(6)

    if not history["panel_scope"].eq("full_market").all():
        raise ValueError("history panel must have panel_scope='full_market'")
    if not forward["panel_scope"].eq("full_market").all():
        raise ValueError("forward panel must have panel_scope='full_market'")

    # Prove the forward projection carries no label leakage.
    leaked = [c for c in forward.columns if c.startswith(("y_", "stock_ret", "benchmark_ret"))]
    if leaked:
        raise ValueError(f"forward projection leaked label columns: {leaked}")
    return history, forward


def fit_frozen_scorer(
    history: pd.DataFrame,
    features: list[str],
    target: str,
    selection_range: tuple[pd.Timestamp, pd.Timestamp],
) -> tuple[list[Any], dict[str, Any]]:
    import lightgbm as lgb

    selection_start, selection_end = selection_range
    eligible = history["universe_b2"].fillna(False)
    train = history.loc[
        eligible & history["date"].between(selection_start, selection_end)
    ].dropna(subset=[target, "y_excess20"]).copy()
    if train.empty:
        raise ValueError("selection window produced no training rows")
    train[features] = train[features].replace([np.inf, -np.inf], np.nan)
    weights = 1.0 / train.groupby("date", sort=False)["code"].transform("size")
    models = []
    for seed in ENSEMBLE_SEEDS:
        model = lgb.LGBMRegressor(
            objective="huber",
            alpha=0.9,
            n_estimators=200,
            learning_rate=0.05,
            num_leaves=31,
            min_child_samples=20,
            subsample=0.8,
            subsample_freq=1,
            colsample_bytree=0.8,
            reg_lambda=1.0,
            random_state=seed,
            n_jobs=1,
            verbosity=-1,
        )
        model.fit(train[features], train[target], sample_weight=weights)
        models.append(model)
    return models, {
        "train_rows": int(len(train)),
        "train_start": str(train["date"].min().date()),
        "train_end": str(train["date"].max().date()),
        "seeds": list(ENSEMBLE_SEEDS),
    }


def predict_forward(
    forward: pd.DataFrame,
    models: list[Any],
    features: list[str],
    forward_start: pd.Timestamp,
    forward_end: pd.Timestamp,
) -> pd.DataFrame:
    eligible = forward["universe_b2"].fillna(False)
    window = forward.loc[eligible & forward["date"].between(forward_start, forward_end)].copy()
    if window.empty:
        raise ValueError("forward window produced no eligible rows")
    window[features] = window[features].replace([np.inf, -np.inf], np.nan)
    stack = [np.asarray(m.predict(window[features]), dtype=float) for m in models]
    window["prediction"] = np.mean(np.vstack(stack), axis=0)
    delta = np.vstack(stack).std(axis=0)
    window["prediction_seed_std"] = delta
    return window


def main() -> int:
    parser = argparse.ArgumentParser(description="Forward scoring with the frozen candidate")
    parser.add_argument("--frozen", default=str(DEFAULT_FROZEN))
    parser.add_argument("--panel", default=str(DEFAULT_PANEL))
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT))
    parser.add_argument(
        "--forward-start",
        default=None,
        help="forward window start (default: max(panel date) - 40 calendar days)",
    )
    parser.add_argument("--forward-end", default=None)
    args = parser.parse_args()

    frozen = _load_frozen(_project_path(args.frozen))
    features = [
        f for f in _read_features(_project_path(frozen["features_file"]))
        if f not in FEATURE_COLUMNS_EXCLUDED
    ]
    panel_path = _project_path(args.panel)
    history, forward = load_scoring_frames(panel_path, features)

    selection_range = _date_range(frozen["selection_period"])
    panel_max = history["date"].max()
    forward_end = (
        pd.Timestamp(args.forward_end) if args.forward_end else panel_max
    )
    forward_start = (
        pd.Timestamp(args.forward_start)
        if args.forward_start
        else forward_end - pd.Timedelta(days=40)
    )
    if forward_start <= selection_range[1]:
        raise ValueError(
            "forward window must start after the selection period ends; "
            f"got {forward_start.date()} <= {selection_range[1].date()}"
        )

    # The registered C-track scoring source is the neutral model; select by id
    # rather than trusting positional order in the frozen config.
    scoring_model = next(
        m for m in frozen["models"] if m["id"] == SCORING_MODEL_ID
    )
    target = scoring_model["target"]
    models, train_info = fit_frozen_scorer(
        history, features, target, selection_range
    )
    scored = predict_forward(forward, models, features, forward_start, forward_end)

    out_dir = _project_path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    keep = [
        "date", "code", "prediction", "prediction_seed_std",
        "universe_b2", "industry", "float_mcap", "adv20", "is_st", "ret_20d",
    ]
    scored[keep].sort_values(["date", "code"]).to_parquet(
        out_dir / "forward_scores.parquet", index=False
    )
    daily = (
        scored.groupby("date")
        .agg(rows=("code", "size"), score_mean=("prediction", "mean"),
             score_std=("prediction", "std"))
        .reset_index()
    )
    daily.to_csv(out_dir / "forward_daily_summary.csv", index=False, encoding="utf-8-sig")

    # C-track bottom-exclusion list per day (10% of the scored universe).
    fraction = float(frozen["acceptance_track"]["bottom_exclusion_fraction"])
    scored["rank_desc"] = scored.groupby("date")["prediction"].rank(
        ascending=False, method="first"
    )
    scored["rank_asc"] = scored.groupby("date")["prediction"].rank(
        ascending=True, method="first"
    )
    scored["bottom_excluded"] = scored["rank_asc"] <= np.ceil(
        fraction * scored.groupby("date")["code"].transform("size")
    )
    excluded = scored.loc[
        scored["bottom_excluded"], ["date", "code", "prediction", "industry"]
    ]
    excluded.to_csv(out_dir / "bottom_excluded.csv", index=False, encoding="utf-8-sig")

    manifest = {
        "classification": "forward_shadow_scoring",
        "registered_before_results": True,
        "frozen_config_file": str(_project_path(args.frozen)),
        "panel_file": str(panel_path),
        "panel_sha256": _sha256(panel_path),
        "features_file": frozen["features_file"],
        "feature_count": len(features),
        "excluded_features": sorted(FEATURE_COLUMNS_EXCLUDED),
        "scoring_model_id": SCORING_MODEL_ID,
        "target": target,
        "selection_period": frozen["selection_period"],
        "forward_start": str(pd.Timestamp(forward_start).date()),
        "forward_end": str(pd.Timestamp(forward_end).date()),
        "forward_rows": int(len(scored)),
        "forward_days": int(scored["date"].nunique()),
        "forward_codes": int(scored["code"].nunique()),
        "bottom_exclusion_fraction": fraction,
        "labels_used_for_forward": False,
        "labels_used_for_training": f"selection window only ({frozen['selection_period']})",
        "commit": _git_commit(),
        "production_authorized": False,
    }
    manifest.update(train_info)
    (out_dir / "forward_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(
        {
            "out_dir": str(out_dir),
            "forward_rows": manifest["forward_rows"],
            "forward_days": manifest["forward_days"],
            "forward_codes": manifest["forward_codes"],
            "window": f"{manifest['forward_start']}..{manifest['forward_end']}",
            "excluded_rows": int(len(excluded)),
        },
        ensure_ascii=False,
        indent=2,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
