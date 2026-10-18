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
from typing import Any, Sequence

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
    # Both reads are projected, and neither frame is a full copy of the panel.
    # Reading the same wide panel twice without projection is what pushed peak
    # RSS past 7 GB on a 16 GB machine.
    from bridge.memory import read_panel

    history = read_panel(panel_path, columns=history_columns)
    history["date"] = pd.to_datetime(history["date"], errors="raise")
    history["code"] = history["code"].astype(str).str.zfill(6)

    forward_columns = list(dict.fromkeys([*FORWARD_COLUMNS, *features]))
    forward = read_panel(panel_path, columns=forward_columns)
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

def assert_feature_coverage(
    forward: pd.DataFrame,
    features: Sequence[str],
    *,
    min_non_null_share: float = 0.50,
    window_start: pd.Timestamp | None = None,
    baseline_shares: dict[str, float] | None = None,
    baseline_tolerance: float = 0.30,
) -> dict[str, float]:
    """Refuse to score when the forward window does not actually carry the features.

    This guard exists because its absence cost a whole round. The original code
    asserted only that the forward projection carried NO label columns - it
    checked for leakage and never checked for presence. A panel missing 8 of the
    37 frozen features, with 28 more entirely NaN in the window, scored happily
    and produced an IC that looked entirely reasonable.

    A model that receives all-NaN inputs returns a near-constant value (LightGBM
    sends those rows down the default branch), so the failure is silent: the
    scores are wrong but not absurd, and every downstream metric is meaningless.

    A flat threshold is not enough on its own: some registered features are
    structurally sparse. alpha028 is entirely empty on 112 of 629 historical days,
    so demanding 50 percent from it forward would reject a healthy panel. When
    baseline_shares (measured on the training window) is supplied, a feature
    passes if it clears the flat floor OR stays close to its own historical share.

    Returns the per-feature non-null share for the manifest. Raises when any
    frozen feature cannot be verified, naming every offender at once rather than
    failing on the first one.
    """

    missing = [c for c in features if c not in forward.columns]
    window = forward
    if window_start is not None:
        window = forward.loc[forward["date"] >= window_start]
    if "universe_b2" in window.columns:
        in_universe = window["universe_b2"].fillna(False)
        if in_universe.any():
            window = window.loc[in_universe]

    shares: dict[str, float] = {}
    for column in features:
        if column not in forward.columns:
            shares[column] = 0.0
            continue
        shares[column] = float(window[column].notna().mean()) if len(window) else 0.0

    def _acceptable(column: str, share: float) -> bool:
        if share >= min_non_null_share:
            return True
        if baseline_shares is None:
            return False
        baseline = float(baseline_shares.get(column, 0.0))
        if baseline <= 0.0:
            return False
        # Permit a forward share somewhat below its own history, but never accept
        # a feature that has effectively vanished.
        return share >= baseline * (1.0 - baseline_tolerance) and share > 0.05

    empty = [c for c, share in shares.items() if not _acceptable(c, share)]
    if missing or empty:
        raise ValueError(
            "forward window does not carry the frozen feature set;"
            f" absent columns ({len(missing)}): {sorted(missing)[:10]};"
            f" insufficient coverage ({len(empty)}): "
            + str([(c, round(shares[c], 4)) for c in empty[:10]])
            + f"; required non-null share >= {min_non_null_share}"
        )
    return shares



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
    parser.add_argument(
        "--industry-pit",
        default=None,
        help=(
            "parquet of SW2021 PIT classification (code, effective_date, sw_l1); "
            "when given, output carries industry_pit_l1 next to the snapshot column"
        ),
    )
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
    # Refuse to score when the window does not carry the frozen features. Without
    # this the pipeline happily fits on history and predicts from all-NaN inputs.
    # Baselines come from the TRAINING frame (history), not from the forward frame:
    # the forward window is exactly what has no history to compare against. The
    # forward frame is label-free but still carries the feature columns for the
    # selection period, so it can supply them.
    train_window = forward.loc[
        forward["date"].between(*selection_range) & forward["universe_b2"].fillna(False)
    ]
    if train_window.empty:
        train_window = history.loc[
            history["date"].between(*selection_range) & history["universe_b2"].fillna(False)
        ]
    baseline = {
        c: float(train_window[c].notna().mean())
        for c in features if c in train_window.columns
    }
    print("baseline window rows:", len(train_window),
          " weakest baseline:", min(baseline.items(), key=lambda kv: kv[1]))
    coverage = assert_feature_coverage(
        forward, features, window_start=pd.Timestamp(forward_start),
        baseline_shares=baseline,
    )
    print(
        "feature coverage verified; weakest column:",
        min(coverage.items(), key=lambda kv: kv[1]),
    )

    models, train_info = fit_frozen_scorer(
        history, features, target, selection_range
    )
    scored = predict_forward(forward, models, features, forward_start, forward_end)

    out_dir = _project_path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    # Attach the real PIT industry when supplied. The snapshot column is kept
    # intact so no reader can mistake one taxonomy for the other.
    pit_note = "snapshot_only"
    if args.industry_pit:
        pit_path = _project_path(args.industry_pit)
        pit = pd.read_parquet(pit_path)
        pit = pit.rename(columns={"effective_date": "pit_date"})
        pit = pit[["pit_date", "code", "sw_l1"]].copy()
        pit["code"] = pit["code"].astype(str).str.zfill(6)
        pit = pit.sort_values(["pit_date", "code"]).reset_index(drop=True)
        left = scored[["date", "code"]].sort_values(["date", "code"]).reset_index(drop=True)
        joined = pd.merge_asof(
            left, pit, left_on="date", right_on="pit_date",
            by="code", direction="backward",
        )
        scored["industry_pit_l1"] = joined["sw_l1"].to_numpy()
        pit_note = "sw2021_pit:" + pit_path.name
        print(
            "industry PIT attached; unmatched:",
            int(scored["industry_pit_l1"].isna().sum()),
        )

    keep = [
        "date", "code", "prediction", "prediction_seed_std",
        "universe_b2", "industry", "float_mcap", "adv20", "is_st", "ret_20d",
    ]
    if "industry_pit_l1" in scored.columns:
        keep.append("industry_pit_l1")
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
    exclude_columns = ["date", "code", "prediction", "industry"]
    if "industry_pit_l1" in scored.columns:
        exclude_columns.append("industry_pit_l1")
    excluded = scored.loc[scored["bottom_excluded"], exclude_columns]
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
        "industry_source": pit_note,
        "feature_coverage": {k: round(v, 4) for k, v in coverage.items()},
        "feature_coverage_min_share": round(min(coverage.values()), 4),
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