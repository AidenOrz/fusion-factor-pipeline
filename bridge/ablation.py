"""Pre-registered B2 factor ablation driver.

The driver keeps the factor list, split contract, and deletion rule explicit.
It has three deliberately separate phases:

``prepare-info-types``
    Resolve source-library metadata into an auditable grouping file.
``register``
    Write the experiment manifest before any model result is produced.
``run --phase baseline`` / ``run --phase lofo``
    Fit the two primary models on the selection period only.  LOFO refuses to
    run until a baseline-derived noise band exists.

All outputs are descriptive development artifacts.  No phase edits a factor
configuration or promotes a feature list.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bridge.baselines import run_huber_baseline
from bridge.gbdt import ENSEMBLE_SEEDS, GAP_TRADING_DAYS
from bridge.ranking import walk_forward_lambdarank, write_ranking_report
from bridge.paths import validate_startup_or_exit


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PANEL = PROJECT_ROOT / "data" / "panel_fusion_b2_adjudicated_2026-08-27.parquet"
DEFAULT_FEATURES = PROJECT_ROOT / "config" / "factor_pool_b2_adjudicated_2026-08-27_features.json"
DEFAULT_GROUPING = PROJECT_ROOT / "docs" / "B2_ABLATION_GROUPING_2026-08-27.csv"
DEFAULT_INFO_REVIEW = PROJECT_ROOT / "docs" / "B2_INFO_TYPE_REVIEW_2026-08-27.csv"
DEFAULT_OUT = PROJECT_ROOT / "runs" / "factor_ablation_v4_b2_huber_cross_label_2026-08-28"
SELECTION_END = pd.Timestamp("2024-09-12")
N_SPLITS = 4  # first block has no train set; three evaluated expanding folds

# The v3 run is retained as historical evidence. New registrations use the
# same estimator across the two labels so a LOFO difference is interpretable
# as a label comparison rather than a mixture of model and label effects.
DEFAULT_PRIMARY_MODEL_IDS = ("huber_b2_raw_rank", "huber_b2_neutral_rank")


def _path(value: str | Path) -> Path:
    candidate = Path(value)
    return candidate if candidate.is_absolute() else PROJECT_ROOT / candidate


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _registered_model_ids(manifest: dict[str, Any]) -> tuple[str, ...]:
    """Return primary model ids while preserving old manifests."""
    models = manifest.get("primary_models")
    if not isinstance(models, list) or not models:
        raise ValueError("experiment manifest must register at least one primary model")
    if any(not isinstance(item, dict) for item in models):
        raise ValueError("experiment manifest contains a non-object primary model")
    model_ids = tuple(str(item.get("id", "")) for item in models)
    if not model_ids or any(not model_id for model_id in model_ids):
        raise ValueError("experiment manifest contains an invalid primary model id")
    if len(model_ids) != len(set(model_ids)):
        raise ValueError("experiment manifest contains duplicate primary model ids")
    return model_ids


def _read_features(path: str | Path) -> list[str]:
    values = json.loads(_path(path).read_text(encoding="utf-8"))
    if not isinstance(values, list) or not values or not all(isinstance(item, str) for item in values):
        raise ValueError("features file must contain a non-empty JSON list of strings")
    if len(values) != len(set(values)):
        raise ValueError("features file contains duplicate columns")
    return values


def _read_grouping(path: str | Path, features: Iterable[str]) -> pd.DataFrame:
    frame = pd.read_csv(_path(path), encoding="utf-8-sig", dtype=str).fillna("")
    required = {"factor", "source_factor", "family", "info_type"}
    missing = sorted(required.difference(frame.columns))
    if missing:
        raise ValueError(f"grouping manifest missing columns: {missing}")
    if frame["factor"].duplicated().any():
        raise ValueError("grouping manifest has duplicate factor rows")
    expected = list(features)
    actual = frame["factor"].tolist()
    if set(actual) != set(expected):
        raise ValueError(
            "grouping manifest factors do not match features file: "
            f"missing={sorted(set(expected) - set(actual))}, "
            f"extra={sorted(set(actual) - set(expected))}"
        )
    if frame["info_type"].eq("").any():
        raise ValueError("grouping manifest contains empty info_type values")
    if "info_type_group" not in frame.columns:
        frame["info_type_group"] = frame["info_type"]
    if frame["info_type_group"].eq("").any():
        raise ValueError("grouping manifest contains empty info_type_group values")
    return frame


def _load_expression_map() -> dict[str, str]:
    config = PROJECT_ROOT / "config" / "expressions_combined.json"
    if not config.is_file():
        return {}
    payload = json.loads(config.read_text(encoding="utf-8"))
    return {
        f"alpha_qg_{item['id']}": str(item.get("expression", "")).lower()
        for item in payload.get("expressions", [])
        if isinstance(item, dict) and item.get("id")
    }


def _normalise_registry_category(category: str, text: str) -> tuple[str, str]:
    """Map source-library categories to the pre-registered broad groups."""
    category = str(category or "").strip().lower()
    text = str(text or "").lower()
    direct = {
        "momentum": "momentum",
        "mean_reversion": "reversal",
        "volatility": "volatility",
        "correlation": "correlation",
        "volume": "volume_vwap",
        "volume_price": "volume_vwap",
        "cap_weighted": "capital",
        "breakout": "momentum",
    }
    if category in direct:
        return direct[category], "aurumq_registry_category"
    # Industry-neutral is a transformation, not an economic primitive.  Use
    # an explicit keyword rule and retain the source category for audit.
    if category == "industry_neutral":
        if any(token in text for token in ("corr", "correlation", "covariance")):
            return "correlation", "aurumq_registry_category_keyword"
        if any(token in text for token in ("volume", "vwap", "adv")):
            return "volume_vwap", "aurumq_registry_category_keyword"
        if any(token in text for token in ("delta", "momentum", "trend")):
            return "momentum", "aurumq_registry_category_keyword"
        return "other", "aurumq_registry_category_unresolved"
    return "other", "aurumq_registry_category_unresolved"


def _info_type_group(info_type: str, source_category: str, source_factor: str) -> str:
    """Map detail labels to the six pre-registered economic buckets.

    Compound/manual labels remain in ``info_type`` for audit, but do not form
    singleton LOFO experiments.  This mapping is metadata-only and never
    consults target returns.
    """
    detail = str(info_type).strip().lower()
    category = str(source_category).strip().lower()
    if detail in {"momentum", "momentum_reversal", "momentum_volatility"}:
        return "momentum"
    if detail == "reversal":
        return "reversal"
    if detail == "volatility":
        return "volatility"
    if detail == "correlation":
        return "correlation"
    if detail == "volume_vwap":
        return "volume_vwap"
    if detail == "capital":
        return "capital"
    if source_factor == "gtja_182" or category == "benchmark":
        return "correlation"
    if source_factor == "alpha020":
        return "momentum"
    return "other"


def prepare_info_types(grouping_path: str | Path, features_path: str | Path, output_path: str | Path) -> dict[str, Any]:
    """Create an auditable info-type file from aurumq and local metadata."""
    features = _read_features(features_path)
    grouping = _read_grouping(grouping_path, features)
    roots = validate_startup_or_exit(("aurumq",))
    sys.path.insert(0, str(roots["aurumq"] / "src"))
    try:
        import aurumq_rl.factors.alpha101  # noqa: F401
        import aurumq_rl.factors.gtja191  # noqa: F401
        from aurumq_rl.factors.registry import list_all_factors
    except Exception as exc:  # pragma: no cover - depends on sibling checkout
        raise RuntimeError("could not load aurumq factor registry") from exc
    registry = list_all_factors()
    expression_map = _load_expression_map()
    explicit_qg = {
        "alpha_qg_corr_pv_10": ("correlation", "manual_candidate_metadata"),
        "alpha_qg_f001": ("momentum", "manual_candidate_metadata"),
        "alpha_qg_f005": ("volume_vwap", "manual_candidate_metadata"),
        "alpha_qg_f006": ("volume_vwap", "manual_candidate_metadata"),
        "alpha_qg_f008": ("momentum_reversal", "manual_candidate_metadata"),
        "alpha_qg_range_15": ("volatility", "config_expression_metadata"),
        "alpha_qg_f009": ("momentum_volatility", "config_expression_metadata"),
    }
    output_rows: list[dict[str, Any]] = []
    for row in grouping.to_dict(orient="records"):
        factor = row["factor"]
        source = row["source_factor"] or factor
        entry = registry.get(source)
        if factor in explicit_qg or source in explicit_qg:
            info_type, source_kind = explicit_qg.get(factor, explicit_qg[source])
            category = entry.category if entry else "quantgpt"
            note = "explicit metadata from the registered QuantGPT/starter expression"
        elif entry:
            category = entry.category
            text = " ".join((entry.description or "", entry.legacy_aqml_expr or ""))
            info_type, source_kind = _normalise_registry_category(category, text)
            note = "derived from aurumq-rl FactorEntry category; no target statistics used"
        else:
            expression = expression_map.get(source, "")
            lowered = expression.lower()
            if any(token in lowered for token in ("corr", "cov")):
                info_type = "correlation"
            elif any(token in lowered for token in ("std", "vol", "range")):
                info_type = "volatility"
            elif any(token in lowered for token in ("volume", "amount", "vwap")):
                info_type = "volume_vwap"
            elif any(token in lowered for token in ("mom", "delta", "trend", "returns")):
                info_type = "momentum"
            else:
                info_type = "other"
            category = row["family"]
            source_kind = "config_expression_keyword" if expression else "unresolved"
            note = "no source registry entry; classification is conservative and expression-based"
        output_rows.append({
            "factor": factor,
            "source_factor": source,
            "family": row["family"],
            "source_category": category,
            "info_type": info_type,
            "info_type_group": _info_type_group(info_type, category, source),
            "info_type_source": source_kind,
            "review_status": "confirmed" if source_kind != "unresolved" else "needs_manual_review",
            "note": note,
        })
    output = _path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(output_rows).to_csv(output, index=False, encoding="utf-8-sig")
    return {
        "output": str(output),
        "factor_count": len(output_rows),
        "unresolved_count": sum(row["review_status"] != "confirmed" for row in output_rows),
        "info_type_counts": pd.Series([row["info_type"] for row in output_rows]).value_counts().to_dict(),
        "info_type_group_counts": pd.Series([row["info_type_group"] for row in output_rows]).value_counts().to_dict(),
    }


def register_experiment(features_path: str | Path, grouping_path: str | Path, output_dir: str | Path, *, selection_end: pd.Timestamp = SELECTION_END, gap_days: int = GAP_TRADING_DAYS) -> dict[str, Any]:
    features = _read_features(features_path)
    grouping = _read_grouping(grouping_path, features)
    if gap_days != 20:
        raise ValueError("this B2 experiment requires the registered 20-trading-day label gap")
    output = _path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    manifest = {
        "classification": "post_hoc_descriptive_only",
        "registered_before_results": True,
        "panel_scope": "full_market",
        "universe": "b2",
        "selection_period_end": str(selection_end.date()),
        "label_gap_trading_days": gap_days,
        "requested_folds": 3,
        "split_blocks": N_SPLITS,
        "ensemble_seeds": list(ENSEMBLE_SEEDS),
        "features_file": str(_path(features_path)),
        "grouping_file": str(_path(grouping_path)),
        "feature_count": len(features),
        "family_groups": sorted(grouping["family"].unique().tolist()),
        "info_type_groups": sorted(grouping["info_type_group"].unique().tolist()),
        "primary_models": [
            {"id": DEFAULT_PRIMARY_MODEL_IDS[0], "target": "y_raw_rank20_b2", "objective": "huber"},
            {"id": DEFAULT_PRIMARY_MODEL_IDS[1], "target": "y_neutral_rank20_b2", "objective": "huber"},
        ],
        "noise_band": {
            "metric": "paired_fold_seed_ic_difference_std",
            "definition": "std(seed IC - three-seed ensemble IC) across evaluated folds and seeds of the full feature set",
            "ddof": 1,
            "delta_sign": "positive means removing the group improves IC; negative means removing the group harms IC",
            "deletion_rule": "a group is a removal candidate when no primary model shows removal_harms; all indistinguishable remains a removal candidate, while any directional conflict requires manual review",
            "value_registered_after_full_baseline": True,
        },
        "selection_only": True,
        "automatic_direction_flip": False,
        "production_config_mutation": False,
    }
    _write_json(output / "experiment_manifest.json", manifest)
    return manifest


def _panel_columns(features: list[str]) -> list[str]:
    return [
        "date", "code", "panel_scope", "universe_b2", "y_excess20",
        "y_raw_rank20_b2", "y_neutral_rank20_b2", *features,
    ]


def load_selection_panel(panel_path: str | Path, features: list[str], selection_end: pd.Timestamp = SELECTION_END) -> pd.DataFrame:
    path = _path(panel_path)
    columns = list(dict.fromkeys(_panel_columns(features)))
    panel = pd.read_parquet(path, columns=columns)
    panel["date"] = pd.to_datetime(panel["date"], errors="raise")
    panel["code"] = panel["code"].astype(str).str.zfill(6)
    if not panel["panel_scope"].eq("full_market").all():
        raise ValueError("ablation panel must have panel_scope='full_market'")
    if not panel["universe_b2"].fillna(False).any():
        raise ValueError("ablation panel has no B2 rows")
    selected = panel.loc[panel["date"].le(selection_end)].copy()
    if selected.empty or selected["date"].nunique() < 80:
        raise ValueError("selection panel is empty or too short for purged folds")
    return selected.sort_values(["date", "code"], kind="mergesort").reset_index(drop=True)


def _write_huber_report(result: dict[str, Any], output_dir: Path) -> None:
    quality = result["overall_quality"]
    lines = [
        "# B2 消融 Huber 基线",
        "",
        "> 开发集 selection-only 描述性实验，不构成生产批准。",
        f"- 标签：`{result['target']}`",
        f"- 日均 Spearman IC：{quality['mean_daily_spearman_ic']}",
        f"- HAC IC_IR：{quality['ic_ir_hac']}",
        f"- NDCG@5：{quality['mean_daily_ndcg_at_5']}",
        f"- NDCG@20：{quality['mean_daily_ndcg_at_20']}",
        "",
    ]
    (output_dir / "report.md").write_text("\n".join(lines), encoding="utf-8")


def _run_model(panel: pd.DataFrame, features: list[str], model_id: str, output_dir: Path) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    metrics_path = output_dir / "metrics.json"
    if metrics_path.is_file():
        return json.loads(metrics_path.read_text(encoding="utf-8"))
    if model_id in {"huber_b2_raw_rank", "huber_b2_neutral_rank"}:
        target = "y_raw_rank20_b2" if model_id == "huber_b2_raw_rank" else "y_neutral_rank20_b2"
        result = run_huber_baseline(
            panel, target=target, output_name=model_id, universe="b2",
            n_splits=N_SPLITS, gap_days=GAP_TRADING_DAYS, feature_columns=features,
        )
        _write_huber_report(result, output_dir)
    elif model_id == "lambdarank_b2_neutral_rank":
        result = walk_forward_lambdarank(
            panel, universe="b2", target_kind="neutral_rank", feature_columns=features,
            n_splits=N_SPLITS, gap_days=GAP_TRADING_DAYS,
        )
        write_ranking_report(result, output_dir)
    else:
        raise ValueError(f"unknown primary model: {model_id}")
    _write_json(metrics_path, result)
    return result


def _noise_band(result: dict[str, Any]) -> float | None:
    by_fold = {
        int(row["fold"]): row["quality"].get("mean_daily_spearman_ic")
        for row in result.get("folds", [])
    }
    values: list[float] = []
    for row in result.get("fold_seed_quality", []):
        seed_ic = row.get("quality", {}).get("mean_daily_spearman_ic")
        ensemble_ic = by_fold.get(int(row["fold"]))
        if seed_ic is not None and ensemble_ic is not None:
            values.append(float(seed_ic) - float(ensemble_ic))
    if len(values) < 2:
        return None
    return float(np.std(np.asarray(values, dtype=float), ddof=1))


def run_baseline(panel_path: str | Path, features_path: str | Path, output_dir: str | Path, selection_end: pd.Timestamp = SELECTION_END) -> dict[str, Any]:
    output = _path(output_dir)
    manifest_path = output / "experiment_manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError("run register before baseline: experiment_manifest.json is missing")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not manifest.get("registered_before_results"):
        raise ValueError("experiment manifest is not marked registered_before_results")
    model_ids = _registered_model_ids(manifest)
    features = _read_features(features_path)
    panel = load_selection_panel(panel_path, features, selection_end)
    models = {}
    for model_id in model_ids:
        models[model_id] = _run_model(panel, features, model_id, output / "baseline" / model_id)
    noise = {model_id: _noise_band(result) for model_id, result in models.items()}
    if any(value is None or not np.isfinite(value) or value <= 0 for value in noise.values()):
        raise ValueError(f"full baseline did not produce valid positive noise bands: {noise}")
    payload = {
        "classification": "post_hoc_descriptive_only",
        "selection_end": str(selection_end.date()),
        "panel_path": str(_path(panel_path)),
        "evaluated_dates": int(panel["date"].nunique()),
        "evaluated_rows": int(len(panel)),
        "models": {
            model_id: {
                "overall_quality": result["overall_quality"],
                "fold_stability": result["fold_stability"],
                "noise_std": noise[model_id],
            }
            for model_id, result in models.items()
        },
        "noise_band_registered_after_full_baseline": True,
    }
    _write_json(output / "noise_band.json", payload)
    return payload


def run_lofo(panel_path: str | Path, features_path: str | Path, grouping_path: str | Path, output_dir: str | Path, *, group_kind: str = "all", selection_end: pd.Timestamp = SELECTION_END) -> pd.DataFrame:
    output = _path(output_dir)
    manifest_path = output / "experiment_manifest.json"
    noise_path = output / "noise_band.json"
    if not manifest_path.is_file() or not noise_path.is_file():
        raise FileNotFoundError("LOFO requires register and a completed full baseline noise_band.json")
    features = _read_features(features_path)
    grouping = _read_grouping(grouping_path, features)
    panel = load_selection_panel(panel_path, features, selection_end)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    model_ids = _registered_model_ids(manifest)
    noise_payload = json.loads(noise_path.read_text(encoding="utf-8"))
    full_payload = noise_payload["models"]
    groups: list[tuple[str, str]] = []
    if group_kind in ("family", "all"):
        groups.extend(("family", str(value)) for value in sorted(grouping["family"].unique()))
    if group_kind in ("info_type", "all"):
        groups.extend(("info_type_group", str(value)) for value in sorted(grouping["info_type_group"].unique()))
    rows: list[dict[str, Any]] = []
    for kind, group in groups:
        removed = set(grouping.loc[grouping[kind].eq(group), "factor"])
        reduced = [feature for feature in features if feature not in removed]
        if not reduced:
            raise ValueError(f"removing {kind}={group!r} leaves no features")
        for model_id in model_ids:
            run_dir = output / "lofo" / kind / group / model_id
            result = _run_model(panel, reduced, model_id, run_dir)
            full_ic = float(full_payload[model_id]["overall_quality"]["mean_daily_spearman_ic"])
            reduced_ic = result["overall_quality"]["mean_daily_spearman_ic"]
            delta = float(reduced_ic) - full_ic if reduced_ic is not None else None
            sigma = float(full_payload[model_id]["noise_std"])
            delta_direction, marginal_contribution = _classify_delta(delta, sigma)
            rows.append({
                "group_kind": kind,
                "group": group,
                "model": model_id,
                "removed_count": len(removed),
                "remaining_count": len(reduced),
                "full_ic": full_ic,
                "reduced_ic": reduced_ic,
                "delta_ic": delta,
                "noise_std": sigma,
                "delta_direction": delta_direction,
                "marginal_contribution": marginal_contribution,
                "within_noise_band": delta_direction == "indistinguishable",
            })
    summary = pd.DataFrame(rows)
    if not summary.empty:
        # Keep each registered grouping independently reproducible.  The
        # aggregate is rebuilt by key so rerunning one grouping cannot erase
        # previously completed family or info-type results.
        summary.to_csv(output / f"lofo_summary_{group_kind}.csv", index=False, encoding="utf-8-sig")
        aggregate_path = output / "lofo_summary.csv"
        prior = pd.read_csv(aggregate_path, encoding="utf-8-sig") if aggregate_path.is_file() else pd.DataFrame()
        if not prior.empty:
            prior = prior.loc[
                ~prior.set_index(["group_kind", "group", "model"]).index.isin(
                    summary.set_index(["group_kind", "group", "model"]).index
                )
            ]
        combined = pd.concat([prior, summary], ignore_index=True)
        combined = combined.sort_values(["group_kind", "group", "model"], kind="mergesort")
        combined.to_csv(aggregate_path, index=False, encoding="utf-8-sig")
        decisions = _group_decisions(combined)
        decisions.to_csv(output / f"lofo_group_decisions_{group_kind}.csv", index=False, encoding="utf-8-sig")
        prior_decisions_path = output / "lofo_group_decisions.csv"
        prior_decisions = pd.read_csv(prior_decisions_path, encoding="utf-8-sig") if prior_decisions_path.is_file() else pd.DataFrame()
        if not prior_decisions.empty:
            prior_decisions = prior_decisions.loc[
                ~prior_decisions.set_index(["group_kind", "group"]).index.isin(
                    decisions.set_index(["group_kind", "group"]).index
                )
            ]
        combined_decisions = pd.concat([prior_decisions, decisions], ignore_index=True)
        combined_decisions = combined_decisions.sort_values(["group_kind", "group"], kind="mergesort")
        combined_decisions.to_csv(prior_decisions_path, index=False, encoding="utf-8-sig")
        _write_ablation_report(combined, output)
    return summary


def _classify_delta(delta: float | None, sigma: float | None) -> tuple[str, str]:
    """Classify LOFO direction using the registered noise band."""
    try:
        delta_value = float(delta) if delta is not None else float("nan")
        sigma_value = float(sigma) if sigma is not None else float("nan")
    except (TypeError, ValueError):
        return "invalid", "unknown"
    if not np.isfinite(delta_value) or not np.isfinite(sigma_value) or sigma_value <= 0:
        return "invalid", "unknown"
    if delta_value > sigma_value:
        return "removal_improves", "negative"
    if delta_value < -sigma_value:
        return "removal_harms", "positive"
    return "indistinguishable", "indistinguishable"


def _group_decisions(summary: pd.DataFrame) -> pd.DataFrame:
    """Aggregate directional LOFO evidence without mutating any config."""
    required = {"group_kind", "group", "model", "delta_ic", "noise_std"}
    missing = required.difference(summary.columns)
    if missing:
        raise ValueError(f"LOFO summary missing columns: {sorted(missing)}")
    frame = summary.copy()
    if "delta_direction" not in frame.columns or "marginal_contribution" not in frame.columns:
        classified = frame.apply(
            lambda row: _classify_delta(row.get("delta_ic"), row.get("noise_std")), axis=1
        )
        frame["delta_direction"] = [item[0] for item in classified]
        frame["marginal_contribution"] = [item[1] for item in classified]
    rows: list[dict[str, Any]] = []
    for (group_kind, group), values in frame.groupby(["group_kind", "group"], sort=True):
        directions = set(values["delta_direction"].astype(str))
        if "invalid" in directions:
            decision = "invalid_requires_recheck"
            recommendation = "无法判定：噪声带或 ΔIC 无效，先重查运行产物"
        elif "removal_harms" in directions and "removal_improves" in directions:
            decision = "direction_conflict"
            recommendation = "人工复核：主线方向冲突，不能自动列入移除候选"
        elif "removal_harms" in directions:
            decision = "retain_positive_marginal"
            recommendation = "保留：至少一条主线显示移除会显著变差"
        elif "removal_improves" in directions:
            decision = "removal_candidate_improves"
            recommendation = "可列入移除候选：至少一条主线显著改善，且没有主线显示正边际贡献"
        else:
            decision = "removal_candidate_indistinguishable"
            recommendation = "可列入移除候选：两条主线均无可检测边际贡献"
        rows.append({
            "group_kind": group_kind,
            "group": group,
            "models": ",".join(values["model"].astype(str)),
            "directions": ",".join(sorted(directions)),
            "decision": decision,
            "recommendation": recommendation,
        })
    return pd.DataFrame(rows)


def _write_ablation_report(summary: pd.DataFrame, output_dir: Path) -> None:
    """Write a human-readable descriptive report without making decisions."""
    lines = [
        "# B2 预注册整组消融报告",
        "",
        "> 分类：`post_hoc_descriptive_only`。所有结果只来自 selection period，不能直接改写候选池或生产配置。",
        "> 面板：`full_market` / `b2`；标签：20 日 `y_raw_rank20_b2` 与 `y_neutral_rank20_b2`；purge gap：20 个交易日。",
        "",
        "## 判定规则",
        "",
        "完整集基线先于 LOFO 登记噪声带。`delta_ic > noise_std` 表示移除后显著变好（该组边际贡献为负）；`delta_ic < -noise_std` 表示移除后显著变差（该组边际贡献为正）；带内表示无可检测边际贡献。只有没有任何主线显示 `removal_harms` 时，才可列入移除候选；方向冲突必须人工复核。本报告不自动执行删除。",
        "",
        "## 汇总",
        "",
    ]
    if summary.empty:
        lines.append("暂无 LOFO 结果。")
    else:
        view = summary.copy()
        if "delta_direction" not in view.columns or "marginal_contribution" not in view.columns:
            classified = view.apply(
                lambda row: _classify_delta(row.get("delta_ic"), row.get("noise_std")), axis=1
            )
            view["delta_direction"] = [item[0] for item in classified]
            view["marginal_contribution"] = [item[1] for item in classified]
        if "within_noise_band" not in view.columns:
            view["within_noise_band"] = view["delta_direction"].eq("indistinguishable")
        decisions = _group_decisions(view)
        for (kind, group), rows in view.groupby(["group_kind", "group"], sort=True):
            recommendation = decisions.loc[
                (decisions["group_kind"] == kind) & (decisions["group"] == group),
                "recommendation",
            ].iloc[0]
            lines.append(f"### {kind} = `{group}`")
            lines.append("")
            lines.append(rows[["model", "removed_count", "remaining_count", "full_ic", "reduced_ic", "delta_ic", "noise_std", "delta_direction", "marginal_contribution", "within_noise_band"]].to_markdown(index=False))
            lines.append("")
            lines.append(f"- 预注册解释：{recommendation}。")
            lines.append("")
    lines.extend([
        "## 下一步",
        "",
        "1. 人工确认本报告中的 `可列入移除候选`，只在候选清单配置中登记一次冻结变更。",
        "2. 用冻结后的清单在 evaluation period 一次性验证；不把 selection 期 LOFO 结果当成 Top-K 验收。",
        "3. 排序质量稳定后再做十分位、horizon IC 衰减、Top-K 净收益、右尾捕获和 regret。",
        "",
    ])
    (output_dir / "ablation_report.md").write_text("\n".join(lines), encoding="utf-8")


def _cmd_prepare(args: argparse.Namespace) -> int:
    print(json.dumps(prepare_info_types(args.grouping, args.features, args.output), ensure_ascii=False, indent=2))
    return 0


def _cmd_register(args: argparse.Namespace) -> int:
    print(json.dumps(register_experiment(args.features, args.grouping, args.out_dir), ensure_ascii=False, indent=2))
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    if args.phase == "baseline":
        payload = run_baseline(args.panel, args.features, args.out_dir)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    elif args.phase == "lofo":
        summary = run_lofo(args.panel, args.features, args.grouping, args.out_dir, group_kind=args.group_kind)
        print(summary.to_json(orient="records", force_ascii=False, indent=2))
    else:
        raise ValueError(f"unsupported phase: {args.phase}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the pre-registered B2 factor ablation")
    sub = parser.add_subparsers(dest="command", required=True)
    prepare = sub.add_parser("prepare-info-types")
    prepare.add_argument("--grouping", default=str(DEFAULT_GROUPING))
    prepare.add_argument("--features", default=str(DEFAULT_FEATURES))
    prepare.add_argument("--output", default=str(DEFAULT_INFO_REVIEW))
    prepare.set_defaults(func=_cmd_prepare)
    register = sub.add_parser("register")
    register.add_argument("--features", default=str(DEFAULT_FEATURES))
    register.add_argument("--grouping", default=str(DEFAULT_INFO_REVIEW))
    register.add_argument("--out-dir", default=str(DEFAULT_OUT))
    register.set_defaults(func=_cmd_register)
    run = sub.add_parser("run")
    run.add_argument("--phase", choices=("baseline", "lofo"), required=True)
    run.add_argument("--panel", default=str(DEFAULT_PANEL))
    run.add_argument("--features", default=str(DEFAULT_FEATURES))
    run.add_argument("--grouping", default=str(DEFAULT_INFO_REVIEW))
    run.add_argument("--out-dir", default=str(DEFAULT_OUT))
    run.add_argument("--group-kind", choices=("all", "family", "info_type"), default="all")
    run.set_defaults(func=_cmd_run)
    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
