"""Full-market Huber regression baselines for the ranking study."""

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

try:
    import lightgbm as lgb
except Exception as exc:  # pragma: no cover
    raise RuntimeError("Huber baselines require lightgbm") from exc

from bridge.gbdt import (
    ENSEMBLE_SEEDS,
    GAP_TRADING_DAYS,
    fold_ic_summary,
    make_purged_splits,
    prediction_quality_summary,
)
from bridge.ranking import default_features


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PANEL = PROJECT_ROOT / "data" / "panel_fusion_full_market.parquet"


def run_huber_baseline(
    panel: pd.DataFrame,
    *,
    target: str,
    output_name: str,
    universe: str = "b1",
    n_splits: int = 4,
    gap_days: int = GAP_TRADING_DAYS,
    feature_columns: list[str] | None = None,
) -> dict[str, Any]:
    if universe not in {"b1", "b2", "b3"}:
        raise ValueError("universe must be one of: b1, b2, b3")
    if "panel_scope" not in panel.columns or not panel["panel_scope"].eq("full_market").all():
        raise ValueError("Huber baselines require panel_scope='full_market'")
    required = {"date", "code", f"universe_{universe}", "y_excess20", target}
    missing = required.difference(panel.columns)
    if missing:
        raise ValueError(f"baseline panel missing columns: {sorted(missing)}")
    features = feature_columns or default_features(panel)
    if not features:
        raise ValueError("no baseline features available")
    work = panel.loc[panel[f"universe_{universe}"].fillna(False)].dropna(
        subset=[target, "y_excess20"]
    ).copy()
    work[features] = work[features].replace([np.inf, -np.inf], np.nan)
    work = work.sort_values(["date", "code"], kind="mergesort").reset_index(drop=True)
    predictions = pd.Series(np.nan, index=work.index, dtype=float)
    folds: list[dict[str, Any]] = []
    fold_seed_quality: list[dict[str, Any]] = []
    for fold, (train_dates, test_dates) in enumerate(make_purged_splits(work["date"], n_splits, gap_days)):
        if not train_dates:
            continue
        train = work.loc[work["date"].isin(train_dates)]
        test = work.loc[work["date"].isin(test_dates)]
        if train.empty or test.empty:
            continue
        seed_predictions = []
        for seed in ENSEMBLE_SEEDS:
            model = lgb.LGBMRegressor(
                objective="huber", alpha=0.9, n_estimators=200, learning_rate=0.05,
                num_leaves=31, min_child_samples=20, subsample=0.8, subsample_freq=1,
                colsample_bytree=0.8, reg_lambda=1.0, random_state=seed,
                n_jobs=1, verbosity=-1,
            )
            daily_sizes = train.groupby("date", sort=False)["code"].transform("size")
            model.fit(train[features], train[target], sample_weight=1.0 / daily_sizes)
            seed_prediction = np.asarray(model.predict(test[features]), dtype=float)
            seed_predictions.append(seed_prediction)
            seed_frame = test[["date", target]].copy()
            seed_frame["prediction"] = seed_prediction
            seed_quality = prediction_quality_summary(seed_frame, "prediction", target)
            fold_seed_quality.append(
                {"fold": fold, "seed": seed, "quality": seed_quality}
            )
        pred = np.mean(np.vstack(seed_predictions), axis=0)
        predictions.loc[test.index] = pred
        evaluated_columns = ["date", "code", target]
        if "y_excess20" not in evaluated_columns:
            evaluated_columns.append("y_excess20")
        evaluated = test[evaluated_columns].copy()
        evaluated["prediction"] = pred
        folds.append({
            "fold": fold,
            "train_start": str(train["date"].min().date()),
            "train_end": str(train["date"].max().date()),
            "test_start": str(test["date"].min().date()),
            "test_end": str(test["date"].max().date()),
            "quality": prediction_quality_summary(evaluated, "prediction", target),
        })
    evaluated = work.loc[predictions.notna()].copy()
    evaluated["prediction"] = predictions.loc[evaluated.index]
    return {
        "classification": "development_descriptive_only",
        "scope": "full_market",
        "estimator": "lightgbm.LGBMRegressor",
        "objective": "huber",
        "output_name": output_name,
        "universe": universe,
        "target": target,
        "features": features,
        "gap_trading_days": gap_days,
        "ensemble_seeds": list(ENSEMBLE_SEEDS),
        "evaluated_rows": int(len(evaluated)),
        "overall_quality": prediction_quality_summary(evaluated, "prediction", target),
        "fold_stability": fold_ic_summary(folds),
        "fold_seed_quality": fold_seed_quality,
        "folds": folds,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a full-market Huber baseline")
    parser.add_argument("--panel", default=str(DEFAULT_PANEL))
    parser.add_argument("--target", choices=["excess", "raw_rank", "neutral_rank"], required=True)
    parser.add_argument("--universe", choices=["b1", "b2", "b3"], default="b1")
    parser.add_argument("--out-dir", required=True)
    args = parser.parse_args()
    panel = pd.read_parquet(args.panel)
    panel["date"] = pd.to_datetime(panel["date"], errors="raise")
    target_map = {
        "excess": "y_excess20",
        "raw_rank": f"y_raw_rank20_{args.universe}",
        "neutral_rank": f"y_neutral_rank20_{args.universe}",
    }
    result = run_huber_baseline(
        panel,
        target=target_map[args.target],
        output_name=args.target,
        universe=args.universe,
    )
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "metrics.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    q = result["overall_quality"]
    (out / "report.md").write_text(
        "\n".join([
            "# 全市场 Huber 基线",
            "",
            "> 开发集描述性实验，不构成生产批准。",
            f"- 域：`{result['universe']}`",
            f"- 目标：`{result['target']}`",
            f"- 日均 Spearman IC：{q['mean_daily_spearman_ic']}",
            f"- HAC IC_IR：{q['ic_ir_hac']}",
            f"- NDCG@5：{q['mean_daily_ndcg_at_5']}",
            f"- NDCG@20：{q['mean_daily_ndcg_at_20']}",
            "",
        ]), encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
