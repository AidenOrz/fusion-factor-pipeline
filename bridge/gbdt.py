"""P0.5/P2 judge: purged walk-forward GBDT on the frozen development set.

LightGBM is preferred when available.  A robust sklearn histogram GBDT
fallback keeps the split and metric harness runnable in restricted
environments.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

try:
    import lightgbm as lgb

    LIGHTGBM_AVAILABLE = True
except Exception:  # pragma: no cover - fallback for restricted environments
    lgb = None
    LIGHTGBM_AVAILABLE = False
    from sklearn.ensemble import HistGradientBoostingRegressor

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bridge.benchmark import attach_forward_returns, load_index, ndcg_at_k, ndcg_random_baseline
from bridge.factor_selection import daily_ic

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PANEL = PROJECT_ROOT / "data" / "panel_fusion.parquet"
DEFAULT_CANDIDATES = (
    PROJECT_ROOT.parent
    / "a-share-factor-engine-v6"
    / "artifacts"
    / "m5_v2_mechanism_diagnostic"
    / "regime_aligned_v21"
    / "l1b_candidates_with_v2_outcomes.csv"
)
DEFAULT_OUT_DIR = PROJECT_ROOT / "runs" / "gbdt_walkforward"

CLASSIFICATION = "post_hoc_descriptive_only"
TOP_KS = (1, 3, 5, 10, 20)
GAP_TRADING_DAYS = 20
ENSEMBLE_SEEDS = (42, 43, 44)
EVALUATION_STAGES = ("selection_ablation", "acceptance")
PRIMARY_NDCG_KS = (5, 20)


def load_candidates(path: Path | str = DEFAULT_CANDIDATES) -> pd.DataFrame:
    frame = pd.read_csv(path, dtype={"code": str}, encoding="utf-8-sig")
    frame["date"] = pd.to_datetime(frame["date"], errors="raise")
    frame["code"] = frame["code"].str.zfill(6)
    return frame.sort_values(["date", "code"]).reset_index(drop=True)


def load_fusion_panel(path: Path | str = DEFAULT_PANEL) -> pd.DataFrame:
    frame = pd.read_parquet(path)
    frame["date"] = pd.to_datetime(frame["date"], errors="raise")
    frame["code"] = frame["code"].astype(str).str.zfill(6)
    return frame.sort_values(["date", "code"]).reset_index(drop=True)


def load_feature_columns(path: Path | str) -> tuple[list[str], pd.Timestamp | None]:
    """Load selected features and their chronological selection cutoff, if present."""
    feature_path = Path(path)
    payload = json.loads(feature_path.read_text(encoding="utf-8"))
    if not isinstance(payload, list) or not all(isinstance(column, str) for column in payload):
        raise ValueError("features file must contain a JSON list of feature column names")
    selection_end = None
    manifest_path = feature_path.with_name("selection_manifest.json")
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("selected_features_file") == feature_path.name:
            value = manifest.get("selection_period_end")
            if value is not None:
                selection_end = pd.Timestamp(value)
    return payload, selection_end


def apply_feature_selection_cutoff(
    panel: pd.DataFrame,
    selection_end: pd.Timestamp | str | None,
    *,
    label_gap_days: int = GAP_TRADING_DAYS,
) -> pd.DataFrame:
    """Exclude selection rows and the forward-label net gap from evaluation."""
    if selection_end is None:
        return panel
    if label_gap_days < 0:
        raise ValueError("label_gap_days must be non-negative")
    dates = pd.DatetimeIndex(pd.to_datetime(panel["date"].unique())).sort_values()
    if dates.empty:
        return panel.iloc[0:0].copy()
    cutoff = pd.Timestamp(selection_end)
    cutoff_position = int(dates.searchsorted(cutoff, side="right") - 1)
    evaluation_position = cutoff_position + label_gap_days + 1
    if evaluation_position >= len(dates):
        return panel.iloc[0:0].copy()
    evaluation_start = dates[evaluation_position]
    return panel.loc[panel["date"] >= evaluation_start].copy()


def feature_selection_evaluation_start(
    dates: pd.Series,
    selection_end: pd.Timestamp | str | None,
    *,
    label_gap_days: int = GAP_TRADING_DAYS,
) -> pd.Timestamp | None:
    """Return the first date eligible after selection and its label gap."""
    if selection_end is None:
        return None
    unique = pd.DatetimeIndex(pd.to_datetime(dates).unique()).sort_values()
    if unique.empty:
        return None
    position = int(unique.searchsorted(pd.Timestamp(selection_end), side="right") - 1)
    start = position + label_gap_days + 1
    return unique[start] if start < len(unique) else None


def static_topk_summary(
    frame: pd.DataFrame,
    scorer: str,
    target: str,
    top_ks: tuple[int, ...] = TOP_KS,
) -> list[dict[str, Any]]:
    ranked = frame.dropna(subset=[scorer, target]).sort_values(
        ["date", scorer, "code"], ascending=[True, False, True], kind="mergesort"
    )
    ranked["rank_position"] = ranked.groupby("date", sort=False).cumcount() + 1
    rows = []
    for top_k in top_ks:
        selected = ranked.loc[ranked["rank_position"].le(top_k)]
        values = pd.to_numeric(selected[target], errors="coerce").dropna()
        rows.append(
            {
                "top_k": top_k,
                "observed_rows": int(len(values)),
                "mean_target": round(float(values.mean()), 6) if len(values) else None,
                "median_target": round(float(values.median()), 6) if len(values) else None,
                "win_rate": round(float(values.gt(0).mean()), 6) if len(values) else None,
            }
        )
    return rows


def prediction_quality_summary(
    frame: pd.DataFrame,
    scorer: str,
    target: str,
    *,
    ndcg_ks: tuple[int, ...] = PRIMARY_NDCG_KS,
) -> dict[str, Any]:
    """Summarize the ranking foundation before looking at portfolio tails.

    IC is calculated as a daily cross-sectional Spearman series.  The pooled
    row-wise correlation is intentionally kept separate because it can be
    dominated by the number of rows per date and is not the selection metric.
    """
    valid = frame.dropna(subset=[scorer, target]).copy()
    if valid.empty:
        return {
            "mean_daily_spearman_ic": None,
            "ic_std": None,
            "ic_ir_hac": None,
            "ic_ir_unadjusted": None,
            "ic_hac_tstat": None,
            "ic_win_rate": None,
            "ic_days": 0,
            **{
                name: None
                for k in ndcg_ks
                for name in (
                    f"mean_daily_ndcg_at_{k}",
                    f"mean_daily_ndcg_random_at_{k}",
                    f"mean_daily_ndcg_delta_at_{k}",
                )
            },
        }
    ic_metrics = daily_ic(valid, scorer, target)
    quality: dict[str, Any] = {
        "mean_daily_spearman_ic": ic_metrics["ic_mean"],
        "ic_std": ic_metrics["ic_std"],
        "ic_ir_hac": ic_metrics["ic_ir"],
        "ic_ir_unadjusted": ic_metrics["ic_ir_unadjusted"],
        "ic_hac_tstat": ic_metrics["ic_mean_hac_tstat"],
        "ic_win_rate": ic_metrics["ic_win_rate"],
        "ic_days": ic_metrics["n_days"],
    }
    for k in ndcg_ks:
        values: list[float] = []
        random_values: list[float] = []
        for _, day in valid.groupby("date", sort=False):
            observed = ndcg_at_k(day[scorer], day[target], k)
            random = ndcg_random_baseline(day[target], k)
            if observed is not None:
                values.append(observed)
            if random is not None:
                random_values.append(random)
        observed_mean = float(np.mean(values)) if values else None
        random_mean = float(np.mean(random_values)) if random_values else None
        quality[f"mean_daily_ndcg_at_{k}"] = (
            round(observed_mean, 6) if observed_mean is not None else None
        )
        quality[f"mean_daily_ndcg_random_at_{k}"] = (
            round(random_mean, 6) if random_mean is not None else None
        )
        quality[f"mean_daily_ndcg_delta_at_{k}"] = (
            round(observed_mean - random_mean, 6)
            if observed_mean is not None and random_mean is not None
            else None
        )
    return quality


def fold_ic_summary(folds: list[dict[str, Any]]) -> dict[str, Any]:
    """Describe fold-to-fold IC stability using the primary daily IC metric."""
    values = np.asarray(
        [
            fold["quality"]["mean_daily_spearman_ic"]
            for fold in folds
            if fold.get("quality", {}).get("mean_daily_spearman_ic") is not None
        ],
        dtype=float,
    )
    if values.size == 0:
        return {
            "fold_count": 0,
            "mean_daily_spearman_ic": None,
            "fold_ic_std": None,
            "fold_ic_sign_consistency": None,
            "positive_fold_rate": None,
        }
    positive = float((values > 0).mean())
    negative = float((values < 0).mean())
    return {
        "fold_count": int(values.size),
        "mean_daily_spearman_ic": round(float(values.mean()), 6),
        "fold_ic_std": round(float(values.std(ddof=1)), 6) if values.size > 1 else 0.0,
        "fold_ic_sign_consistency": round(max(positive, negative), 6),
        "positive_fold_rate": round(positive, 6),
    }


def binding_calibration(candidates: pd.DataFrame) -> dict[str, Any]:
    binding = candidates.loc[
        candidates["v2_positive_slot_binding"].fillna(False)
        & candidates["d1_execution_eligible"].fillna(False)
        & candidates["isolated_r_multiple"].notna()
    ].copy()
    known = {
        "legacy_score": {"top1": 1.1723, "top3": 0.6604, "top5": 0.4516},
        "composite_score": {"top1": 0.6937, "top3": 0.5693, "top5": 0.4004},
    }
    result = {"classification": CLASSIFICATION, "binding_rows": int(len(binding)), "scorers": {}}
    for scorer in ("legacy_score", "composite_score"):
        rows = static_topk_summary(binding, scorer, "isolated_r_multiple")
        lookup = {row["top_k"]: row["mean_target"] for row in rows}
        drift = {
            key: round(float(lookup[int(key[3:])] - value), 6)
            for key, value in known[scorer].items()
        }
        result["scorers"][scorer] = {"topk": rows, "known_value_drift": drift}
    return result


def make_purged_splits(
    dates: pd.Series,
    n_splits: int = 4,
    gap_days: int = GAP_TRADING_DAYS,
) -> list[tuple[list[pd.Timestamp], list[pd.Timestamp]]]:
    unique = pd.Series(pd.to_datetime(dates).sort_values().unique())
    blocks = np.array_split(unique.to_numpy(), n_splits)
    splits = []
    for index in range(n_splits):
        test_dates = pd.to_datetime(blocks[index])
        if index == 0:
            train_dates = pd.DatetimeIndex([])
        else:
            train_end = blocks[index - 1][-1]
            train_idx = unique.to_numpy() <= train_end
            train_available = pd.DatetimeIndex(unique.to_numpy()[train_idx])
            if gap_days > 0:
                train_available = train_available[: -gap_days]
            train_dates = train_available
        splits.append((list(train_dates), list(test_dates)))
    return splits


def walk_forward_gbdt(
    panel: pd.DataFrame,
    feature_columns: list[str],
    target: str = "EXRET20",
    *,
    n_splits: int = 4,
    gap_days: int = GAP_TRADING_DAYS,
    top_ks: tuple[int, ...] = TOP_KS,
    daily_returns: pd.DataFrame | None = None,
    feature_selection_end: pd.Timestamp | str | None = None,
    evaluation_stage: str = "selection_ablation",
) -> dict[str, Any]:
    if evaluation_stage not in EVALUATION_STAGES:
        raise ValueError(
            f"evaluation_stage must be one of {EVALUATION_STAGES}, got {evaluation_stage!r}"
        )
    work = panel.dropna(subset=[target]).copy()
    selection_eval_start = feature_selection_evaluation_start(
        work["date"], feature_selection_end, label_gap_days=gap_days
    )
    work = apply_feature_selection_cutoff(
        work, feature_selection_end, label_gap_days=gap_days
    )
    # LightGBM handles NaN natively; dropping rows on 300+ sparse factor
    # columns would erase the whole panel.
    work[feature_columns] = work[feature_columns].replace([np.inf, -np.inf], np.nan)
    work["_date_num"] = (work["date"] - work["date"].min()).dt.days
    splits = make_purged_splits(work["date"], n_splits=n_splits, gap_days=gap_days)

    fold_rows: list[dict[str, Any]] = []
    predictions = pd.Series(np.nan, index=work.index, dtype=float)
    legacy_frame = work[["date", "code", target, "legacy_score"]].copy()
    gain_importance: dict[str, list[float]] = {feature: [] for feature in feature_columns}
    shap_importance: dict[str, list[float]] = {feature: [] for feature in feature_columns}

    for fold_index, (train_dates, test_dates) in enumerate(splits):
        if not train_dates:
            continue
        train_mask = work["date"].isin(train_dates)
        test_mask = work["date"].isin(test_dates)
        if train_mask.sum() < 20 or test_mask.sum() < 20:
            continue
        X_test = work.loc[test_mask, feature_columns].to_numpy(dtype=float)
        predictions_by_seed: list[np.ndarray] = []
        for seed in ENSEMBLE_SEEDS:
            if LIGHTGBM_AVAILABLE:
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
            else:  # pragma: no cover - fallback
                model = HistGradientBoostingRegressor(
                    # sklearn's histogram regressor has no Huber loss; L1 is
                    # the closest robust fallback when LightGBM is absent.
                    loss="absolute_error",
                    max_iter=200,
                    learning_rate=0.05,
                    max_depth=4,
                    min_samples_leaf=20,
                    l2_regularization=1.0,
                    random_state=seed,
                )
            model.fit(
                work.loc[train_mask, feature_columns].to_numpy(dtype=float),
                work.loc[train_mask, target].to_numpy(dtype=float),
            )
            seed_pred = np.asarray(model.predict(X_test), dtype=float)
            predictions_by_seed.append(seed_pred)
            if LIGHTGBM_AVAILABLE:
                gains = np.asarray(model.feature_importances_, dtype=float)
                for feature, gain in zip(feature_columns, gains, strict=False):
                    gain_importance[feature].append(float(gain))
                try:
                    contrib = model.predict(X_test, pred_contrib=True)
                    # Last column is the bias term; feature columns are 0..n-1.
                    abs_shap = np.abs(np.asarray(contrib, dtype=float)[:, : len(feature_columns)])
                    mean_abs = abs_shap.mean(axis=0)
                    for feature, value in zip(feature_columns, mean_abs, strict=False):
                        shap_importance[feature].append(float(value))
                except Exception:
                    pass
        pred = np.mean(np.vstack(predictions_by_seed), axis=0)
        predictions.loc[test_mask] = pred
        test_slice = work.loc[test_mask].copy()
        test_slice["prediction"] = pred
        pooled_ic = float(test_slice["prediction"].corr(test_slice[target], method="spearman"))
        quality = prediction_quality_summary(test_slice, "prediction", target)
        ml_topk = static_topk_summary(test_slice, "prediction", target, top_ks=top_ks)
        legacy_topk = static_topk_summary(
            legacy_frame.loc[test_mask], "legacy_score", target, top_ks=top_ks
        )
        ml_top = {row["top_k"]: row["mean_target"] for row in ml_topk}
        legacy_top = {row["top_k"]: row["mean_target"] for row in legacy_topk}
        fold_rows.append(
            {
                "fold": fold_index,
                "train_start": str(min(train_dates).date()),
                "train_end": str(max(train_dates).date()),
                "test_start": str(min(test_dates).date()),
                "test_end": str(max(test_dates).date()),
                "train_rows": int(train_mask.sum()),
                "test_rows": int(test_mask.sum()),
                "pooled_spearman_ic": round(pooled_ic, 6),
                "quality": quality,
                "spearman_ic": quality["mean_daily_spearman_ic"],
                "ml_topk": ml_topk,
                "legacy_topk": legacy_topk,
                "ml_minus_legacy_top3": round(
                    float((ml_top.get(3) or np.nan) - (legacy_top.get(3) or np.nan)), 6
                ),
            }
        )

    eval_slice = work.loc[predictions.notna()].copy()
    eval_slice["prediction"] = predictions.loc[predictions.notna()]
    pooled_ic = float(eval_slice["prediction"].corr(eval_slice[target], method="spearman"))
    quality = prediction_quality_summary(eval_slice, "prediction", target)
    ml_topk = static_topk_summary(eval_slice, "prediction", target, top_ks=top_ks)
    legacy_topk = static_topk_summary(eval_slice, "legacy_score", target, top_ks=top_ks)
    ml_top = {row["top_k"]: row["mean_target"] for row in ml_topk}
    legacy_top = {row["top_k"]: row["mean_target"] for row in legacy_topk}
    primary_metrics = {
        "mean_daily_spearman_ic": quality["mean_daily_spearman_ic"],
        "ic_ir_hac": quality["ic_ir_hac"],
        "mean_daily_ndcg_at_5": quality["mean_daily_ndcg_at_5"],
        "mean_daily_ndcg_at_20": quality["mean_daily_ndcg_at_20"],
        "mean_daily_ndcg_delta_at_5": quality["mean_daily_ndcg_delta_at_5"],
        "mean_daily_ndcg_delta_at_20": quality["mean_daily_ndcg_delta_at_20"],
        "fold_stability": fold_ic_summary(fold_rows),
    }
    feature_importance = [
        {
            "feature": feature,
            "mean_gain": float(np.mean(gain_importance[feature]))
            if gain_importance[feature]
            else None,
            "mean_abs_shap": float(np.mean(shap_importance[feature]))
            if shap_importance[feature]
            else None,
            "n_models": int(len(shap_importance[feature])),
            "shap_folds": shap_importance[feature],
        }
        for feature in feature_columns
    ]
    benchmark_vs_sse = None
    if daily_returns is not None:
        from bridge.benchmark import evaluate_scorers_vs_benchmark

        benchmark_vs_sse = evaluate_scorers_vs_benchmark(
            eval_slice[["date", "code", "prediction", "legacy_score"]],
            daily_returns,
            ["prediction", "legacy_score"],
        )
    return {
        "classification": CLASSIFICATION,
        "evaluation_stage": evaluation_stage,
        "estimator": "lightgbm.LGBMRegressor" if LIGHTGBM_AVAILABLE else "sklearn.HistGradientBoostingRegressor",
        "objective": "huber",
        "ensemble_seeds": list(ENSEMBLE_SEEDS),
        "note": (
            "three-seed mean ensemble; LightGBM bagging enabled with subsample_freq=1"
            if LIGHTGBM_AVAILABLE
            else "three-seed mean ensemble using sklearn Huber histogram GBDT fallback"
        ),
        "n_splits": n_splits,
        "gap_trading_days": gap_days,
        "target": target,
        "feature_selection_end": (
            str(pd.Timestamp(feature_selection_end).date())
            if feature_selection_end is not None
            else None
        ),
        "feature_selection_evaluation_start": (
            str(selection_eval_start.date()) if selection_eval_start is not None else None
        ),
        "features": feature_columns,
        "evaluated_rows": int(len(eval_slice)),
        "pooled_spearman_ic": round(pooled_ic, 6),
        "overall_spearman_ic": quality["mean_daily_spearman_ic"],
        "overall_ic_quality": quality,
        "primary_metrics": primary_metrics,
        "overall_ml_topk": ml_topk,
        "overall_legacy_topk": legacy_topk,
        "overall_ml_minus_legacy_top3": round(
            float((ml_top.get(3) or np.nan) - (legacy_top.get(3) or np.nan)), 6
        ),
        "feature_importance": feature_importance,
        "benchmark_vs_sse": benchmark_vs_sse,
        "folds": fold_rows,
    }


def write_report(result: dict[str, Any], out_dir: Path | str) -> Path:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    metrics_path = out_dir / "metrics.json"
    metrics_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    if result.get("feature_importance"):
        pd.DataFrame(result["feature_importance"]).to_csv(
            out_dir / "feature_importance.csv",
            index=False,
            encoding="utf-8-sig",
        )
    lines = [
        "# GBDT 判官 walk-forward 报告",
        "",
        f"> 分类：`{result.get('classification')}`",
        f"> 模型：{result.get('estimator')}",
        f"> 说明：{result.get('note')}",
        "",
        f"## 阶段主指标：{result.get('evaluation_stage', 'selection_ablation')}",
        "",
        f"- 目标：{result.get('target')}",
        f"- 特征选择截止日：{result.get('feature_selection_end') or '未提供选择元数据'}",
        f"- 评估行数：{result.get('evaluated_rows')}",
        f"- 平均日截面 Spearman IC：{result.get('overall_spearman_ic')}",
        f"- HAC IC_IR：{result.get('overall_ic_quality', {}).get('ic_ir_hac')}",
        f"- 平均日 NDCG@5：{result.get('overall_ic_quality', {}).get('mean_daily_ndcg_at_5')}",
        f"- 平均日 NDCG@20：{result.get('overall_ic_quality', {}).get('mean_daily_ndcg_at_20')}",
        f"- NDCG@5 - 随机基线：{result.get('overall_ic_quality', {}).get('mean_daily_ndcg_delta_at_5')}",
        f"- NDCG@20 - 随机基线：{result.get('overall_ic_quality', {}).get('mean_daily_ndcg_delta_at_20')}",
        f"- 折间 IC 符号一致性：{result.get('primary_metrics', {}).get('fold_stability', {}).get('fold_ic_sign_consistency')}",
        f"- 折间 IC 标准差：{result.get('primary_metrics', {}).get('fold_stability', {}).get('fold_ic_std')}",
        "",
        "以上指标是筛选/消融期的主判据。Top-K、上证超额和 legacy 对比只作附录观察，不能单独证明排序能力。",
        "",
        "## 附录 A：Top-K 观察",
        "",
        "### ML 预测",
        "",
        pd.DataFrame(result["overall_ml_topk"]).to_markdown(index=False),
        "",
        "### legacy_score",
        "",
        pd.DataFrame(result["overall_legacy_topk"]).to_markdown(index=False),
        "",
        "## 附录 B：上证指数与 Top-K 基准",
        "",
        "上证指数、Top-K 超额和 legacy_score 仅在验收期作为主指标；当前阶段不据此作候选升级。",
        "",
    ]
    if result.get("benchmark_vs_sse"):
        bench = result["benchmark_vs_sse"]
        for scorer, rows in bench.get("scorers", {}).items():
            lines.append(f"### {scorer}")
            lines.append("")
            lines.append(pd.DataFrame(rows).to_markdown(index=False))
            lines.append("")
    lines.extend(["## 折间质量明细", "",])
    for fold in result["folds"]:
        quality = fold.get("quality", {})
        lines.extend(
            [
                f"- fold {fold['fold']}: test {fold['test_start']}..{fold['test_end']}, "
                f"rows={fold['test_rows']}, daily IC={quality.get('mean_daily_spearman_ic')}, "
                f"HAC IC_IR={quality.get('ic_ir_hac')}, "
                f"NDCG@5={quality.get('mean_daily_ndcg_at_5')}, "
                f"ML-legacy Top3={fold['ml_minus_legacy_top3']} (appendix)",
            ]
        )
    lines.append("")
    report_path = out_dir / "report.md"
    report_path.write_text("\n".join(lines), encoding="utf-8")
    return report_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Purged walk-forward GBDT judge")
    parser.add_argument("--panel", default=str(DEFAULT_PANEL))
    parser.add_argument("--candidates", default=str(DEFAULT_CANDIDATES))
    parser.add_argument("--features-file", default=None)
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument("--target", default="EXRET20")
    parser.add_argument(
        "--evaluation-stage",
        choices=EVALUATION_STAGES,
        default="selection_ablation",
        help="primary metric interpretation; Top-K becomes primary only in acceptance stage",
    )
    parser.add_argument(
        "--feature-selection-end",
        default=None,
        help="exclusive evaluation cutoff when the features file has no matching selection metadata",
    )
    parser.add_argument(
        "--daily-panel",
        default=str(
            PROJECT_ROOT.parent / "a-share-factor-engine-v6" / "artifacts" / "panel" / "panel.parquet"
        ),
    )
    parser.add_argument(
        "--index",
        default=str(PROJECT_ROOT.parent / "a-share-kline-data" / "_bench_000001SH.csv"),
    )
    parser.add_argument(
        "--mode",
        choices=["static-calibration", "walk-forward", "both"],
        default="both",
    )
    args = parser.parse_args()

    candidates = load_candidates(args.candidates)
    calibration = binding_calibration(candidates)
    if args.mode in ("static-calibration", "both"):
        print(json.dumps(calibration, ensure_ascii=False, indent=2, sort_keys=True))

    if args.mode in ("walk-forward", "both"):
        panel = load_fusion_panel(args.panel)
        if args.features_file:
            feature_columns, feature_selection_end = load_feature_columns(args.features_file)
        else:
            feature_selection_end = None
            feature_columns = [
                "SQZ_ATR_REL__score",
                "SQZ_RANGE_ABS__score",
                "TRG_VOL_EXP__score",
                "POS_EMA20_BAND__score",
            ]
            for extra in ("market_cap", "total_shares"):
                if extra in panel.columns:
                    feature_columns.append(extra)
            expression_columns = [
                column for column in panel.columns if column.startswith(("alpha", "gtja"))
            ]
            feature_columns.extend(sorted(expression_columns))
        if args.feature_selection_end is not None:
            feature_selection_end = pd.Timestamp(args.feature_selection_end)
        elif args.features_file and feature_selection_end is None:
            print(
                "[gbdt] warning: features file has no matching selection cutoff; "
                "walk-forward remains descriptive only",
                file=sys.stderr,
            )
        daily = pd.read_parquet(args.daily_panel)
        daily["date"] = pd.to_datetime(daily["date"], errors="raise")
        daily["code"] = daily["code"].astype(str).str.zfill(6)
        daily_returns = attach_forward_returns(daily, load_index(args.index))
        result = walk_forward_gbdt(
            panel,
            feature_columns,
            target=args.target,
            daily_returns=daily_returns,
            feature_selection_end=feature_selection_end,
            evaluation_stage=args.evaluation_stage,
        )
        report_path = write_report(result, args.out_dir)
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        print(f"[gbdt] report written to {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
