"""Purged full-market LambdaRank experiments.

This module intentionally accepts only panels materialized with
``panel_scope=full_market``.  The v6 event file has no role in training,
feature selection, or domain comparison.
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

try:
    import lightgbm as lgb
except Exception as exc:  # pragma: no cover - dependency availability
    raise RuntimeError("full-market LambdaRank requires lightgbm") from exc

from bridge.gbdt import (
    ENSEMBLE_SEEDS,
    GAP_TRADING_DAYS,
    PRIMARY_NDCG_KS,
    fold_ic_summary,
    make_purged_splits,
    prediction_quality_summary,
    static_topk_summary,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PANEL = PROJECT_ROOT / "data" / "panel_fusion_full_market.parquet"
DEFAULT_OUT_DIR = PROJECT_ROOT / "runs" / "full_market_lambdarank"
UNIVERSES = ("b1", "b2", "b3")
TARGETS = ("raw_rank", "neutral_rank")
RELEVANCE_LEVELS = 100


def _target_column(universe: str, target_kind: str) -> str:
    if universe not in UNIVERSES:
        raise ValueError(f"universe must be one of {UNIVERSES}")
    if target_kind not in TARGETS:
        raise ValueError(f"target_kind must be one of {TARGETS}")
    prefix = "y_raw_rank20" if target_kind == "raw_rank" else "y_neutral_rank20"
    return f"{prefix}_{universe}"


def _relevance(values: pd.Series, levels: int = RELEVANCE_LEVELS) -> np.ndarray:
    if levels < 2:
        raise ValueError("relevance levels must be at least 2")
    numeric = pd.to_numeric(values, errors="raise").to_numpy(dtype=float)
    result = np.ceil(numeric * levels).astype(int) - 1
    return np.clip(result, 0, levels - 1)


def _date_sorted_work(
    panel: pd.DataFrame,
    *,
    universe: str,
    target: str,
    feature_columns: list[str],
) -> pd.DataFrame:
    if "panel_scope" not in panel.columns:
        raise ValueError("LambdaRank requires an explicit panel_scope='full_market' column")
    if not panel["panel_scope"].eq("full_market").all():
        scopes = sorted(panel["panel_scope"].dropna().unique().tolist())
        raise ValueError(f"LambdaRank only accepts full_market panels, got scopes={scopes}")
    required = {"date", "code", f"universe_{universe}", target, "y_excess20", *feature_columns}
    missing = required.difference(panel.columns)
    if missing:
        raise ValueError(f"ranking panel missing columns: {sorted(missing)}")
    work = panel.loc[panel[f"universe_{universe}"].fillna(False)].copy()
    work = work.dropna(subset=[target, "y_excess20"])
    work[feature_columns] = work[feature_columns].replace([np.inf, -np.inf], np.nan)
    return work.sort_values(["date", "code"], kind="mergesort").reset_index(drop=True)


def _groups(frame: pd.DataFrame) -> np.ndarray:
    return frame.groupby("date", sort=False).size().to_numpy(dtype=int)


def _daily_weights(frame: pd.DataFrame) -> np.ndarray:
    sizes = frame.groupby("date", sort=False)["code"].transform("size")
    return (1.0 / sizes).to_numpy(dtype=float)


def default_features(panel: pd.DataFrame) -> list[str]:
    preferred = [
        # These are full-market daily-panel fields, not event-only v6 scores.
        "atr_relative",
        "range_relative",
        "vol_ratio",
        "position_ema20",
        "ret_5d",
        "ret_20d",
        "realized_vol_120d_d1",
        "amount_dry_ratio",
    ]
    preferred.extend(sorted(column for column in panel.columns if column.startswith("alpha_qg_")))
    return [column for column in preferred if column in panel.columns]


def walk_forward_lambdarank(
    panel: pd.DataFrame,
    *,
    universe: str,
    target_kind: str,
    feature_columns: list[str] | None = None,
    n_splits: int = 4,
    gap_days: int = GAP_TRADING_DAYS,
    relevance_levels: int = RELEVANCE_LEVELS,
) -> dict[str, Any]:
    """Train three-seed LambdaRank models with date query groups and a purge."""
    target = _target_column(universe, target_kind)
    features = feature_columns or default_features(panel)
    if not features:
        raise ValueError("no ranking features available")
    work = _date_sorted_work(panel, universe=universe, target=target, feature_columns=features)
    splits = make_purged_splits(work["date"], n_splits=n_splits, gap_days=gap_days)
    predictions = pd.Series(np.nan, index=work.index, dtype=float)
    fold_rows: list[dict[str, Any]] = []
    importances: dict[str, list[float]] = {feature: [] for feature in features}

    for fold, (train_dates, test_dates) in enumerate(splits):
        if not train_dates:
            continue
        train = work.loc[work["date"].isin(train_dates)].copy()
        test = work.loc[work["date"].isin(test_dates)].copy()
        if train.empty or test.empty:
            continue
        train = train.sort_values(["date", "code"], kind="mergesort")
        test = test.sort_values(["date", "code"], kind="mergesort")
        x_train = train[features].to_numpy(dtype=float)
        x_test = test[features].to_numpy(dtype=float)
        y_train = _relevance(train[target], relevance_levels)
        train_groups = _groups(train)
        seed_predictions: list[np.ndarray] = []
        for seed in ENSEMBLE_SEEDS:
            model = lgb.LGBMRanker(
                objective="lambdarank",
                metric="ndcg",
                importance_type="gain",
                label_gain=list(range(relevance_levels)),
                lambdarank_truncation_level=20,
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
            model.fit(
                x_train,
                y_train,
                group=train_groups,
                sample_weight=_daily_weights(train),
            )
            seed_predictions.append(np.asarray(model.predict(x_test), dtype=float))
            for feature, gain in zip(features, model.feature_importances_, strict=False):
                importances[feature].append(float(gain))
        test_prediction = np.mean(np.vstack(seed_predictions), axis=0)
        predictions.loc[test.index] = test_prediction
        evaluated = test.assign(prediction=test_prediction)
        quality = prediction_quality_summary(evaluated, "prediction", target)
        economics = static_topk_summary(evaluated, "prediction", "y_excess20")
        fold_rows.append(
            {
                "fold": fold,
                "train_start": str(train["date"].min().date()),
                "train_end": str(train["date"].max().date()),
                "test_start": str(test["date"].min().date()),
                "test_end": str(test["date"].max().date()),
                "train_rows": int(len(train)),
                "test_rows": int(len(test)),
                "quality": quality,
                "topk_y_excess20_appendix": economics,
            }
        )
    evaluated = work.loc[predictions.notna()].copy()
    if evaluated.empty:
        raise ValueError("no folds produced predictions")
    evaluated["prediction"] = predictions.loc[evaluated.index]
    quality = prediction_quality_summary(evaluated, "prediction", target)
    result = {
        "classification": "development_descriptive_only",
        "scope": "full_market",
        "estimator": "lightgbm.LGBMRanker",
        "objective": "lambdarank",
        "universe": universe,
        "target": target,
        "relevance_levels": relevance_levels,
        "gap_trading_days": gap_days,
        "ensemble_seeds": list(ENSEMBLE_SEEDS),
        "features": features,
        "evaluated_rows": int(len(evaluated)),
        "overall_quality": quality,
        "fold_stability": fold_ic_summary(fold_rows),
        "topk_y_excess20_appendix": static_topk_summary(evaluated, "prediction", "y_excess20"),
        "folds": fold_rows,
        "feature_importance": [
            {
                "feature": feature,
                "mean_gain": float(np.mean(values)) if values else None,
                "n_models": len(values),
            }
            for feature, values in importances.items()
        ],
    }
    return result


def write_ranking_report(result: dict[str, Any], out_dir: Path | str) -> Path:
    path = Path(out_dir)
    path.mkdir(parents=True, exist_ok=True)
    (path / "metrics.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    pd.DataFrame(result["feature_importance"]).to_csv(
        path / "feature_importance.csv", index=False, encoding="utf-8-sig"
    )
    quality = result["overall_quality"]
    lines = [
        "# 全市场 LambdaRank 报告",
        "",
        "> 分类：开发集描述性实验，不构成生产批准。",
        "> 事件集未参与本训练、标签或域定义。",
        "",
        f"- 域：`{result['universe']}`",
        f"- 标签：`{result['target']}`",
        f"- 评估行数：{result['evaluated_rows']}",
        f"- 日均 Spearman IC：{quality['mean_daily_spearman_ic']}",
        f"- HAC IC_IR：{quality['ic_ir_hac']}",
        f"- NDCG@5：{quality['mean_daily_ndcg_at_5']}",
        f"- NDCG@20：{quality['mean_daily_ndcg_at_20']}",
        f"- 折间 IC 一致性：{result['fold_stability']['fold_ic_sign_consistency']}",
        "",
        "## 附录：Top-K 真实超额收益",
        "",
        pd.DataFrame(result["topk_y_excess20_appendix"]).to_markdown(index=False),
        "",
    ]
    report = path / "report.md"
    report.write_text("\n".join(lines), encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Purged full-market LambdaRank experiment")
    parser.add_argument("--panel", default=str(DEFAULT_PANEL))
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument("--universe", choices=UNIVERSES, default="b1")
    parser.add_argument("--target", choices=TARGETS, default="raw_rank")
    parser.add_argument("--features-file", default=None)
    parser.add_argument("--n-splits", type=int, default=4)
    parser.add_argument("--relevance-levels", type=int, default=RELEVANCE_LEVELS)
    args = parser.parse_args()
    panel = pd.read_parquet(args.panel)
    panel["date"] = pd.to_datetime(panel["date"], errors="raise")
    features = None
    if args.features_file:
        features = json.loads(Path(args.features_file).read_text(encoding="utf-8"))
    result = walk_forward_lambdarank(
        panel,
        universe=args.universe,
        target_kind=args.target,
        feature_columns=features,
        n_splits=args.n_splits,
        relevance_levels=args.relevance_levels,
    )
    report = write_ranking_report(result, args.out_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    print(f"[ranking] report written to {report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
