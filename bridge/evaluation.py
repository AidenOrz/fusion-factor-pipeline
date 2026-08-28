"""One-shot internal evaluation for the frozen B2 factor pool.

The model is fitted once on the registered selection period and predicts the
entire evaluation period without updating on evaluation labels.  This is an
internal historical holdout, not clean 2026 OOS and not production approval.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bridge.ablation import _read_features, _write_json
from bridge.confirmation import bottom_decile_tradeability, decile_analysis
from bridge.gbdt import ENSEMBLE_SEEDS, prediction_quality_summary

try:
    import lightgbm as lgb
except Exception as exc:  # pragma: no cover
    raise RuntimeError("frozen evaluation requires lightgbm") from exc


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PANEL = PROJECT_ROOT / "data" / "panel_fusion_b2_adjudicated_2026-08-27.parquet"
DEFAULT_FROZEN = PROJECT_ROOT / "config" / "factor_pool_b2_frozen_2026-08-28.json"
DEFAULT_OUT = PROJECT_ROOT / "runs" / "factor_evaluation_v1_b2_frozen_2026-08-28"


def _path(value: str | Path) -> Path:
    candidate = Path(value)
    return candidate if candidate.is_absolute() else PROJECT_ROOT / candidate


def _portable_path(value: str | Path) -> str:
    resolved = _path(value).resolve()
    try:
        return resolved.relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return str(resolved)


def _load_frozen(path: str | Path) -> dict[str, Any]:
    frozen = json.loads(_path(path).read_text(encoding="utf-8"))
    if frozen.get("status") != "frozen_for_one_shot_evaluation":
        raise ValueError("factor pool is not frozen for one-shot evaluation")
    if frozen.get("evaluation_authorized") is not True:
        raise ValueError("frozen config does not authorize evaluation")
    if frozen.get("automatic_direction_flip") is not False:
        raise ValueError("frozen config must prohibit automatic direction flips")
    return frozen


def register_evaluation(
    frozen_path: str | Path = DEFAULT_FROZEN,
    panel_path: str | Path = DEFAULT_PANEL,
    output_dir: str | Path = DEFAULT_OUT,
) -> dict[str, Any]:
    frozen = _load_frozen(frozen_path)
    features = _read_features(_path(frozen["features_file"]))
    if len(features) != int(frozen["feature_count"]):
        raise ValueError("frozen feature count does not match features file")
    manifest = {
        "classification": "one_shot_internal_historical_evaluation",
        "registered_before_results": True,
        "frozen_config_file": _portable_path(frozen_path),
        "panel_file": _portable_path(panel_path),
        "features_file": frozen["features_file"],
        "feature_count": len(features),
        "panel_scope": "full_market",
        "universe": "b2",
        "selection_period": frozen["selection_period"],
        "evaluation_period": frozen["evaluation_period"],
        "training_mode": "fit_selection_once_no_evaluation_updates",
        "models": frozen["models"],
        "ensemble_seeds": list(ENSEMBLE_SEEDS),
        "pass_rule": frozen["evaluation_pass_rule"],
        "acceptance_track": frozen["acceptance_track"],
        "automatic_direction_flip": False,
        "evaluation_result_may_not_mutate_factor_list": True,
        "clean_2026_oos_access": False,
        "production_authorized": False,
    }
    output = _path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    decision_path = output / "evaluation_decision.json"
    if decision_path.exists():
        raise RuntimeError("one-shot evaluation has already produced a decision")
    _write_json(output / "evaluation_manifest.json", manifest)
    return manifest


def _load_panel(panel_path: str | Path, features: list[str]) -> pd.DataFrame:
    optional = ["industry", "float_mcap", "ret_20d", "adv20", "is_st"]
    columns = list(dict.fromkeys([
        "date", "code", "panel_scope", "universe_b2", "y_excess20",
        "y_raw_rank20_b2", "y_neutral_rank20_b2", *optional, *features,
    ]))
    panel = pd.read_parquet(_path(panel_path), columns=columns)
    panel["date"] = pd.to_datetime(panel["date"], errors="raise")
    panel["code"] = panel["code"].astype(str).str.zfill(6)
    if not panel["panel_scope"].eq("full_market").all():
        raise ValueError("evaluation panel must have panel_scope='full_market'")
    return panel.sort_values(["date", "code"], kind="mergesort").reset_index(drop=True)


def _date_range(value: str) -> tuple[pd.Timestamp, pd.Timestamp]:
    parts = value.split("..")
    if len(parts) != 2:
        raise ValueError(f"invalid date range: {value}")
    return pd.Timestamp(parts[0]), pd.Timestamp(parts[1])


def _block_stability(frame: pd.DataFrame, target: str, blocks: int = 3) -> dict[str, Any]:
    dates = pd.DatetimeIndex(frame["date"].unique()).sort_values()
    chunks = [chunk for chunk in np.array_split(dates, blocks) if len(chunk)]
    rows = []
    signs = []
    for index, dates_in_block in enumerate(chunks, start=1):
        block = frame.loc[frame["date"].isin(dates_in_block)]
        quality = prediction_quality_summary(block, "prediction", target)
        ic = quality["mean_daily_spearman_ic"]
        sign = int(np.sign(ic)) if ic is not None else 0
        signs.append(sign)
        rows.append({
            "block": index,
            "start": str(dates_in_block.min().date()),
            "end": str(dates_in_block.max().date()),
            "quality": quality,
        })
    nonzero = [sign for sign in signs if sign != 0]
    consistency = 1.0 if len(nonzero) == len(chunks) and len(set(nonzero)) == 1 else 0.0
    return {"block_count": len(chunks), "block_ic_sign_consistency": consistency, "blocks": rows}


def _fit_predict(
    panel: pd.DataFrame,
    features: list[str],
    target: str,
    selection_range: tuple[pd.Timestamp, pd.Timestamp],
    evaluation_range: tuple[pd.Timestamp, pd.Timestamp],
) -> tuple[pd.DataFrame, dict[str, Any]]:
    selection_start, selection_end = selection_range
    evaluation_start, evaluation_end = evaluation_range
    eligible = panel["universe_b2"].fillna(False)
    train = panel.loc[
        eligible & panel["date"].between(selection_start, selection_end)
    ].dropna(subset=[target, "y_excess20"]).copy()
    evaluation = panel.loc[
        eligible & panel["date"].between(evaluation_start, evaluation_end)
    ].dropna(subset=[target, "y_excess20"]).copy()
    if train.empty or evaluation.empty:
        raise ValueError("selection or evaluation rows are empty")
    if train["date"].max() >= evaluation["date"].min():
        raise ValueError("selection and evaluation periods overlap")
    train[features] = train[features].replace([np.inf, -np.inf], np.nan)
    evaluation[features] = evaluation[features].replace([np.inf, -np.inf], np.nan)
    weights = 1.0 / train.groupby("date", sort=False)["code"].transform("size")
    predictions = []
    for seed in ENSEMBLE_SEEDS:
        model = lgb.LGBMRegressor(
            objective="huber", alpha=0.9, n_estimators=200, learning_rate=0.05,
            num_leaves=31, min_child_samples=20, subsample=0.8, subsample_freq=1,
            colsample_bytree=0.8, reg_lambda=1.0, random_state=seed,
            n_jobs=1, verbosity=-1,
        )
        model.fit(train[features], train[target], sample_weight=weights)
        predictions.append(np.asarray(model.predict(evaluation[features]), dtype=float))
    evaluated = evaluation.copy()
    evaluated["prediction"] = np.mean(np.vstack(predictions), axis=0)
    quality = prediction_quality_summary(evaluated, "prediction", target)
    stability = _block_stability(evaluated, target)
    result = {
        "classification": "one_shot_internal_historical_evaluation",
        "target": target,
        "features": features,
        "train_rows": int(len(train)),
        "train_start": str(train["date"].min().date()),
        "train_end": str(train["date"].max().date()),
        "evaluation_rows": int(len(evaluated)),
        "evaluation_start": str(evaluated["date"].min().date()),
        "evaluation_end": str(evaluated["date"].max().date()),
        "overall_quality": quality,
        "block_stability": stability,
        "evaluation_labels_used_for_fit": False,
    }
    return evaluated, result


def run_evaluation(
    output_dir: str | Path = DEFAULT_OUT,
) -> dict[str, Any]:
    output = _path(output_dir)
    manifest_path = output / "evaluation_manifest.json"
    decision_path = output / "evaluation_decision.json"
    if decision_path.exists():
        raise RuntimeError("one-shot evaluation has already produced a decision")
    if not manifest_path.is_file():
        raise FileNotFoundError("register evaluation before running")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("registered_before_results") is not True:
        raise ValueError("evaluation manifest was not registered before results")
    frozen = _load_frozen(manifest["frozen_config_file"])
    features = _read_features(_path(manifest["features_file"]))
    panel = _load_panel(manifest["panel_file"], features)
    selection_range = _date_range(manifest["selection_period"])
    evaluation_range = _date_range(manifest["evaluation_period"])
    model_results = []
    decile_summaries: dict[str, dict[str, Any]] = {}
    q1_summaries: dict[str, dict[str, Any]] = {}
    for model in manifest["models"]:
        model_id = model["id"]
        target = model["target"]
        run_dir = output / model_id
        run_dir.mkdir(parents=True, exist_ok=True)
        predictions, metrics = _fit_predict(panel, features, target, selection_range, evaluation_range)
        prediction_columns = [
            "date", "code", target, "y_excess20", "prediction",
            "industry", "float_mcap", "ret_20d", "adv20", "is_st",
        ]
        predictions[prediction_columns].rename(columns={target: "target_value"}).to_parquet(
            run_dir / "predictions.parquet", index=False
        )
        _write_json(run_dir / "metrics.json", metrics)
        decile_table, decile_summary = decile_analysis(
            predictions.rename(columns={target: "target_value"})
        )
        decile_table.to_csv(run_dir / "decile_table.csv", index=False, encoding="utf-8-sig")
        _write_json(run_dir / "decile_summary.json", decile_summary)
        q1_rows, q1_summary = bottom_decile_tradeability(
            predictions,
            panel[["date", "code", "is_st"]],
            diagnostic_end=evaluation_range[1],
        )
        q1_rows.to_csv(run_dir / "q1_tradeability.csv", index=False, encoding="utf-8-sig")
        _write_json(run_dir / "q1_tradeability_summary.json", q1_summary)
        model_results.append({"model": model_id, **metrics})
        decile_summaries[model_id] = decile_summary
        q1_summaries[model_id] = q1_summary

    expected_signature = manifest["pass_rule"]["decile_structure_signature_each_model"]
    minimum_ir = float(manifest["pass_rule"]["hac_ic_ir_minimum_each_model"])
    expected_consistency = float(
        manifest["pass_rule"]["evaluation_block_ic_sign_consistency_each_model"]
    )
    checks = []
    for result in model_results:
        model_id = result["model"]
        model_checks = {
            "hac_ic_ir_minimum": float(result["overall_quality"]["ic_ir_hac"]) >= minimum_ir,
            "block_sign_consistency": (
                float(result["block_stability"]["block_ic_sign_consistency"])
                == expected_consistency
            ),
            "decile_structure_preserved": (
                decile_summaries[model_id]["structure_signature"] == expected_signature
            ),
        }
        checks.append({
            "model": model_id,
            "mean_daily_spearman_ic": result["overall_quality"]["mean_daily_spearman_ic"],
            "hac_ic_ir": result["overall_quality"]["ic_ir_hac"],
            "block_ic_sign_consistency": result["block_stability"]["block_ic_sign_consistency"],
            "decile_structure_signature": decile_summaries[model_id]["structure_signature"],
            "q1_tradeability_retention_ratio": q1_summaries[model_id]["absolute_return_retention_ratio"],
            **model_checks,
            "model_pass": all(model_checks.values()),
        })
    decision = {
        "classification": "one_shot_internal_historical_evaluation",
        "checks": checks,
        "evaluation_pass": all(check["model_pass"] for check in checks),
        "evaluation_consumed": True,
        "factor_list_mutation_authorized": False,
        "production_authorized": False,
        "next_step_if_pass": "run frozen C-track economic acceptance; A-track Top-K remains appendix",
        "next_step_if_fail": "record final rejection for this frozen candidate; no tuning on evaluation",
    }
    _write_json(decision_path, decision)
    return decision


def main() -> int:
    parser = argparse.ArgumentParser(description="One-shot evaluation of the frozen B2 factor pool")
    sub = parser.add_subparsers(dest="command", required=True)
    register = sub.add_parser("register")
    register.add_argument("--frozen", default=str(DEFAULT_FROZEN))
    register.add_argument("--panel", default=str(DEFAULT_PANEL))
    register.add_argument("--out-dir", default=str(DEFAULT_OUT))
    run = sub.add_parser("run")
    run.add_argument("--out-dir", default=str(DEFAULT_OUT))
    args = parser.parse_args()
    if args.command == "register":
        result = register_evaluation(args.frozen, args.panel, args.out_dir)
    else:
        result = run_evaluation(args.out_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
