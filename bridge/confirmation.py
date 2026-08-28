"""Selection-only confirmation for the post-LOFO B2 factor candidate.

The experiment compares the registered 114-factor reference with the
versioned post-LOFO candidate using identical Huber models and purged folds.
It also writes strictly out-of-fold predictions for decile and horizon
diagnostics.  Diagnostics choose the later acceptance track/horizon only;
they may not alter the candidate factor list.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bridge.ablation import (
    DEFAULT_PANEL,
    DEFAULT_PRIMARY_MODEL_IDS,
    N_SPLITS,
    SELECTION_END,
    _read_features,
    _write_json,
    load_selection_panel,
)
from bridge.baselines import run_huber_baseline
from bridge.factor_selection import ic_ir_hac
from bridge.gbdt import ENSEMBLE_SEEDS, GAP_TRADING_DAYS


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FULL_FEATURES = PROJECT_ROOT / "config" / "factor_pool_b2_adjudicated_2026-08-27_features.json"
DEFAULT_CANDIDATE_FEATURES = PROJECT_ROOT / "config" / "factor_pool_b2_post_lofo_candidate_2026-08-28_features.json"
DEFAULT_NOISE = PROJECT_ROOT / "runs" / "factor_ablation_v4_b2_huber_cross_label_2026-08-28" / "noise_band.json"
DEFAULT_OUT = PROJECT_ROOT / "runs" / "factor_confirmation_v1_b2_post_lofo_2026-08-28"
HORIZONS = (1, 5, 10, 20, 40)
HORIZON_INTERVALS = ((1, 1), (2, 5), (6, 10), (11, 20), (21, 40))


def _path(value: str | Path) -> Path:
    candidate = Path(value)
    return candidate if candidate.is_absolute() else PROJECT_ROOT / candidate


def _portable_path(value: str | Path) -> str:
    resolved = _path(value).resolve()
    try:
        return resolved.relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return str(resolved)


def register_confirmation(
    full_features_path: str | Path = DEFAULT_FULL_FEATURES,
    candidate_features_path: str | Path = DEFAULT_CANDIDATE_FEATURES,
    noise_path: str | Path = DEFAULT_NOISE,
    output_dir: str | Path = DEFAULT_OUT,
) -> dict[str, Any]:
    full = _read_features(full_features_path)
    candidate = _read_features(candidate_features_path)
    if not set(candidate).issubset(full):
        raise ValueError("candidate feature list is not a subset of the adjudicated list")
    if len(candidate) >= len(full):
        raise ValueError("confirmation requires a strictly smaller candidate feature list")
    noise = json.loads(_path(noise_path).read_text(encoding="utf-8"))
    models = noise.get("models", {})
    if set(models) != set(DEFAULT_PRIMARY_MODEL_IDS):
        raise ValueError("noise registry does not match the two registered Huber models")
    manifest = {
        "classification": "post_hoc_descriptive_only",
        "registered_before_results": True,
        "selection_only": True,
        "panel_scope": "full_market",
        "universe": "b2",
        "selection_period_end": str(SELECTION_END.date()),
        "label_gap_trading_days": GAP_TRADING_DAYS,
        "split_blocks": N_SPLITS,
        "evaluated_folds": N_SPLITS - 1,
        "ensemble_seeds": list(ENSEMBLE_SEEDS),
        "full_features_file": _portable_path(full_features_path),
        "candidate_features_file": _portable_path(candidate_features_path),
        "full_feature_count": len(full),
        "candidate_feature_count": len(candidate),
        "removed_feature_count": len(full) - len(candidate),
        "primary_models": [
            {"id": "huber_b2_raw_rank", "target": "y_raw_rank20_b2", "objective": "huber"},
            {"id": "huber_b2_neutral_rank", "target": "y_neutral_rank20_b2", "objective": "huber"},
        ],
        "pass_rule": {
            "ic": "candidate_ic >= full_ic - registered_full_baseline_1sigma",
            "hac_ic_ir": "candidate_hac_ic_ir >= full_hac_ic_ir",
            "fold_sign_consistency": "candidate_fold_ic_sign_consistency == 1.0",
            "decile_structure": "same signs for Q1, Q10, Q10-Q1, and decile monotonic Spearman",
            "all_models_and_conditions_required": True,
        },
        "diagnostic_registration": {
            "prediction_kind": "strictly_out_of_fold",
            "deciles": 10,
            "horizons": list(HORIZONS),
            "horizon_exit_date_must_not_exceed": str(SELECTION_END.date()),
            "purpose": "choose acceptance track and label horizon before one-shot evaluation",
            "factor_list_mutation_forbidden": True,
        },
        "failure_policy": "one preregistered culprit-family put-back retry; otherwise return to descriptive stage",
        "automatic_direction_flip": False,
        "evaluation_period_access": False,
    }
    output = _path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    _write_json(output / "experiment_manifest.json", manifest)
    return manifest


def _model_target(model_id: str) -> str:
    if model_id == "huber_b2_raw_rank":
        return "y_raw_rank20_b2"
    if model_id == "huber_b2_neutral_rank":
        return "y_neutral_rank20_b2"
    raise ValueError(f"unsupported confirmation model: {model_id}")


def _optional_panel_columns(panel_path: str | Path) -> list[str]:
    # Read the schema only; the prediction panel is otherwise loaded with the
    # explicit feature columns needed by the registered model.
    import pyarrow.parquet as pq

    available = set(pq.ParquetFile(_path(panel_path)).schema_arrow.names)
    return [
        column for column in (
            "industry", "float_mcap", "ret_20d", "adv20", "is_st",
            "open", "close", "benchmark_open", "benchmark_close",
        )
        if column in available
    ]


def _load_confirmation_panel(
    panel_path: str | Path, features: list[str], selection_end: pd.Timestamp = SELECTION_END
) -> pd.DataFrame:
    panel = load_selection_panel(panel_path, features, selection_end)
    optional = _optional_panel_columns(panel_path)
    missing = [column for column in optional if column not in panel.columns]
    if missing:
        extras = pd.read_parquet(_path(panel_path), columns=["date", "code", *missing])
        extras["date"] = pd.to_datetime(extras["date"], errors="raise")
        extras["code"] = extras["code"].astype(str).str.zfill(6)
        panel = panel.merge(extras, on=["date", "code"], how="left", validate="one_to_one")
    return panel


def run_confirmation_models(
    panel_path: str | Path = DEFAULT_PANEL,
    output_dir: str | Path = DEFAULT_OUT,
) -> dict[str, Any]:
    output = _path(output_dir)
    manifest_path = output / "experiment_manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError("register confirmation before running models")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not manifest.get("registered_before_results") or not manifest.get("selection_only"):
        raise ValueError("confirmation manifest is not a registered selection-only experiment")
    full_features = _read_features(manifest["full_features_file"])
    candidate_features = _read_features(manifest["candidate_features_file"])
    panel = _load_confirmation_panel(panel_path, full_features)
    results: dict[str, Any] = {}
    for pool_name, features in (("full", full_features), ("candidate", candidate_features)):
        results[pool_name] = {}
        for model in manifest["primary_models"]:
            model_id = model["id"]
            run_dir = output / pool_name / model_id
            run_dir.mkdir(parents=True, exist_ok=True)
            metrics_path = run_dir / "metrics.json"
            predictions_path = run_dir / "oof_predictions.parquet"
            if metrics_path.is_file() and predictions_path.is_file():
                result = json.loads(metrics_path.read_text(encoding="utf-8"))
            else:
                result = run_huber_baseline(
                    panel,
                    target=_model_target(model_id),
                    output_name=f"{pool_name}_{model_id}",
                    universe="b2",
                    n_splits=N_SPLITS,
                    gap_days=GAP_TRADING_DAYS,
                    feature_columns=features,
                    predictions_path=predictions_path,
                )
                _write_json(metrics_path, result)
            results[pool_name][model_id] = result
    return results


def _hac_t(values: pd.Series, lag: int = 20) -> float | None:
    clean = pd.to_numeric(values, errors="coerce").dropna().to_numpy(dtype=float)
    ratio = ic_ir_hac(clean, lag=lag)
    return float(ratio * np.sqrt(len(clean))) if ratio is not None else None


def decile_analysis(predictions: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    required = {"date", "code", "prediction", "y_excess20", "target_value"}
    missing = sorted(required - set(predictions.columns))
    if missing:
        raise ValueError(f"OOF predictions missing columns: {missing}")
    frame = predictions.dropna(subset=["prediction", "y_excess20"]).copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="raise")

    def assign_decile(day: pd.DataFrame) -> pd.Series:
        ranked = day["prediction"].rank(method="first", pct=True)
        return np.minimum(np.ceil(ranked * 10).astype(int), 10)

    decile_values = frame.groupby("date", sort=False, group_keys=False)["prediction"].transform(
        lambda values: np.minimum(np.ceil(values.rank(method="first", pct=True) * 10).astype(int), 10)
    )
    frame["decile"] = decile_values.astype(int)
    daily = frame.groupby(["date", "decile"], sort=True).agg(
        mean_y_excess20=("y_excess20", "mean"),
        mean_true_rank=("target_value", "mean"),
        stock_count=("code", "size"),
    ).reset_index()
    for column in ("float_mcap", "ret_20d", "adv20"):
        if column in frame.columns:
            values = frame.groupby(["date", "decile"], sort=True)[column].mean().rename(f"mean_{column}")
            daily = daily.merge(values.reset_index(), on=["date", "decile"], how="left")
    rows: list[dict[str, Any]] = []
    for decile, group in daily.groupby("decile", sort=True):
        row: dict[str, Any] = {
            "decile": int(decile),
            "days": int(group["date"].nunique()),
            "mean_y_excess20": float(group["mean_y_excess20"].mean()),
            "hac_t_y_excess20": _hac_t(group["mean_y_excess20"]),
            "mean_true_rank": float(group["mean_true_rank"].mean()),
            "mean_stock_count": float(group["stock_count"].mean()),
        }
        for column in ("mean_float_mcap", "mean_ret_20d", "mean_adv20"):
            if column in group.columns:
                row[column] = float(group[column].mean())
        rows.append(row)
    table = pd.DataFrame(rows).sort_values("decile")
    if set(table["decile"]) != set(range(1, 11)):
        raise ValueError("decile analysis did not produce all ten groups")
    q1 = daily.loc[daily["decile"].eq(1), ["date", "mean_y_excess20"]].rename(columns={"mean_y_excess20": "q1"})
    q10 = daily.loc[daily["decile"].eq(10), ["date", "mean_y_excess20"]].rename(columns={"mean_y_excess20": "q10"})
    spread = q1.merge(q10, on="date", how="inner")
    spread["q10_minus_q1"] = spread["q10"] - spread["q1"]
    monotonic = spearmanr(table["decile"], table["mean_y_excess20"]).statistic
    summary = {
        "classification": "post_hoc_descriptive_only",
        "q1_mean": float(table.loc[table["decile"].eq(1), "mean_y_excess20"].iloc[0]),
        "q10_mean": float(table.loc[table["decile"].eq(10), "mean_y_excess20"].iloc[0]),
        "q10_minus_q1_mean": float(spread["q10_minus_q1"].mean()),
        "q10_minus_q1_hac_t": _hac_t(spread["q10_minus_q1"]),
        "decile_monotonic_spearman": float(monotonic),
        "structure_signature": {
            "q1_sign": int(np.sign(table.loc[table["decile"].eq(1), "mean_y_excess20"].iloc[0])),
            "q10_sign": int(np.sign(table.loc[table["decile"].eq(10), "mean_y_excess20"].iloc[0])),
            "q10_minus_q1_sign": int(np.sign(spread["q10_minus_q1"].mean())),
            "monotonic_spearman_sign": int(np.sign(monotonic)),
        },
    }
    return table, summary


def bottom_decile_tradeability(
    predictions: pd.DataFrame,
    market_status: pd.DataFrame,
    *,
    horizon: int = 20,
    diagnostic_end: pd.Timestamp = SELECTION_END,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Describe whether Q1 returns survive observable tradeability screens.

    This is a C-track diagnostic only. It does not alter factors, labels, or
    the confirmation decision. Missing holding-window bars are a conservative
    suspension/delist-risk proxy because complete historical delist metadata
    is not available in the project reference table.
    """

    required_predictions = {"date", "code", "prediction", "y_excess20", "adv20"}
    required_status = {"date", "code", "is_st"}
    missing_predictions = sorted(required_predictions - set(predictions.columns))
    missing_status = sorted(required_status - set(market_status.columns))
    if missing_predictions or missing_status:
        raise ValueError(
            f"tradeability diagnostic missing columns: predictions={missing_predictions}, "
            f"market_status={missing_status}"
        )
    frame = predictions.dropna(subset=["prediction", "y_excess20"]).copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="raise")
    frame["code"] = frame["code"].astype(str).str.zfill(6)
    frame["decile"] = frame.groupby("date", sort=False)["prediction"].transform(
        lambda values: np.minimum(np.ceil(values.rank(method="first", pct=True) * 10).astype(int), 10)
    )
    frame["signal_adv_percentile"] = frame.groupby("date", sort=False)["adv20"].rank(
        method="average", pct=True
    )
    q1 = frame.loc[frame["decile"].eq(1)].copy().reset_index(drop=True)

    status = market_status[["date", "code", "is_st"]].copy()
    status["date"] = pd.to_datetime(status["date"], errors="raise")
    status["code"] = status["code"].astype(str).str.zfill(6)
    status = status.drop_duplicates(["date", "code"], keep="last")
    calendar = pd.DatetimeIndex(status["date"].dropna().unique()).sort_values()
    positions = pd.Series(np.arange(len(calendar)), index=calendar)
    q1["_position"] = q1["date"].map(positions)
    exit_positions = q1["_position"] + horizon
    valid_exit = exit_positions.lt(len(calendar))
    q1["exit_date"] = pd.NaT
    q1.loc[valid_exit, "exit_date"] = calendar.take(
        exit_positions.loc[valid_exit].astype(int).to_numpy()
    ).to_numpy()
    q1 = q1.loc[q1["exit_date"].le(diagnostic_end)].copy().reset_index(drop=True)
    q1["signal_adv_bottom_10pct"] = q1["signal_adv_percentile"].le(0.10)
    q1["_row_id"] = np.arange(len(q1))

    expanded = q1.loc[q1.index.repeat(horizon), ["_row_id", "code", "_position"]].copy()
    expanded["_offset"] = np.tile(np.arange(1, horizon + 1), len(q1))
    expanded["holding_date"] = calendar.take(
        (expanded["_position"] + expanded["_offset"]).astype(int).to_numpy()
    ).to_numpy()
    holding_status = status.rename(columns={"date": "holding_date", "is_st": "holding_is_st"})
    holding_status["_bar_present"] = True
    expanded = expanded.merge(
        holding_status,
        on=["code", "holding_date"],
        how="left",
        validate="many_to_one",
    )
    flags = expanded.groupby("_row_id", sort=False).agg(
        future_st=("holding_is_st", lambda values: bool(values.fillna(False).any())),
        missing_holding_bar=("_bar_present", lambda values: bool(values.isna().any())),
    )
    q1 = q1.merge(flags, left_on="_row_id", right_index=True, how="left", validate="one_to_one")
    q1["passes_observable_screens"] = ~(
        q1["future_st"] | q1["missing_holding_bar"] | q1["signal_adv_bottom_10pct"]
    )

    def return_summary(mask: pd.Series) -> dict[str, Any]:
        selected = q1.loc[mask]
        daily = selected.groupby("date", sort=True)["y_excess20"].mean()
        return {
            "rows": int(len(selected)),
            "days": int(daily.index.nunique()),
            "mean_daily_y_excess20": float(daily.mean()) if len(daily) else None,
            "hac_t_y_excess20": _hac_t(daily) if len(daily) else None,
        }

    all_mask = pd.Series(True, index=q1.index)
    all_summary = return_summary(all_mask)
    retained_summary = return_summary(q1["passes_observable_screens"])
    original = all_summary["mean_daily_y_excess20"]
    retained = retained_summary["mean_daily_y_excess20"]
    summary = {
        "classification": "post_hoc_descriptive_only",
        "purpose": "C-track bottom-decile tradeability life check",
        "horizon": horizon,
        "last_exit_date": str(q1["exit_date"].max().date()) if not q1.empty else None,
        "all_q1": all_summary,
        "exclude_future_st": return_summary(~q1["future_st"]),
        "exclude_missing_holding_bar": return_summary(~q1["missing_holding_bar"]),
        "exclude_signal_adv_bottom_10pct": return_summary(~q1["signal_adv_bottom_10pct"]),
        "combined_observable_screens": retained_summary,
        "absolute_return_retention_ratio": (
            float(abs(retained) / abs(original)) if original not in (None, 0.0) and retained is not None else None
        ),
        "limitations": [
            "missing_holding_bar is a conservative suspension/delist-risk proxy",
            "complete historical delist metadata is unavailable; no claim of a full delist audit",
        ],
        "factor_list_mutation_forbidden": True,
    }
    output_columns = [
        "date", "code", "exit_date", "prediction", "y_excess20", "adv20",
        "signal_adv_percentile", "signal_adv_bottom_10pct", "future_st",
        "missing_holding_bar", "passes_observable_screens",
    ]
    return q1[output_columns].sort_values(["date", "code"]), summary


def _forward_excess_for_horizon(prices: pd.DataFrame, horizon: int) -> pd.DataFrame:
    if horizon < 1:
        raise ValueError("horizon must be positive")
    required = {"date", "code", "open", "close", "benchmark_open", "benchmark_close"}
    missing = sorted(required - set(prices.columns))
    if missing:
        raise ValueError(f"price panel missing columns: {missing}")
    frame = prices[list(required)].copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="raise")
    frame["code"] = frame["code"].astype(str).str.zfill(6)
    frame = frame.sort_values(["code", "date"], kind="mergesort")
    benchmark = frame[["date", "benchmark_open", "benchmark_close"]].drop_duplicates("date").sort_values("date")
    dates = pd.DatetimeIndex(benchmark["date"])
    positions = pd.Series(np.arange(len(dates)), index=dates)
    frame["_position"] = frame["date"].map(positions)
    date_by_position = pd.Series(dates, index=np.arange(len(dates)))
    frame["entry_date"] = (frame["_position"] + 1).map(date_by_position)
    frame["exit_date"] = (frame["_position"] + horizon).map(date_by_position)
    opens = frame[["code", "date", "open"]].rename(columns={"date": "entry_date", "open": "entry_open"})
    closes = frame[["code", "date", "close"]].rename(columns={"date": "exit_date", "close": "exit_close"})
    frame = frame.merge(opens, on=["code", "entry_date"], how="left", validate="many_to_one")
    frame = frame.merge(closes, on=["code", "exit_date"], how="left", validate="many_to_one")
    benchmark = benchmark.copy()
    benchmark["benchmark_return"] = benchmark["benchmark_close"].shift(-horizon) / benchmark["benchmark_open"].shift(-1) - 1.0
    frame = frame.merge(benchmark[["date", "benchmark_return"]], on="date", how="left", validate="many_to_one")
    frame["forward_excess"] = frame["exit_close"] / frame["entry_open"] - 1.0 - frame["benchmark_return"]
    valid = frame["entry_open"].notna() & frame["exit_close"].notna() & frame["benchmark_return"].notna()
    frame["forward_excess"] = frame["forward_excess"].where(valid)
    return frame[["date", "code", "exit_date", "forward_excess"]]


def _forward_excess_for_interval(
    prices: pd.DataFrame, start_offset: int, end_offset: int
) -> pd.DataFrame:
    """Return excess performance for one non-overlapping forward interval."""

    if start_offset < 1 or end_offset < start_offset:
        raise ValueError("forward interval must satisfy 1 <= start_offset <= end_offset")
    required = {"date", "code", "open", "close", "benchmark_open", "benchmark_close"}
    missing = sorted(required - set(prices.columns))
    if missing:
        raise ValueError(f"price panel missing columns: {missing}")
    frame = prices[list(required)].copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="raise")
    frame["code"] = frame["code"].astype(str).str.zfill(6)
    frame = frame.sort_values(["code", "date"], kind="mergesort")
    benchmark = frame[["date", "benchmark_open", "benchmark_close"]].drop_duplicates("date").sort_values("date")
    dates = pd.DatetimeIndex(benchmark["date"])
    positions = pd.Series(np.arange(len(dates)), index=dates)
    frame["_position"] = frame["date"].map(positions)
    date_by_position = pd.Series(dates, index=np.arange(len(dates)))
    frame["entry_date"] = (frame["_position"] + start_offset).map(date_by_position)
    frame["exit_date"] = (frame["_position"] + end_offset).map(date_by_position)
    opens = frame[["code", "date", "open"]].rename(columns={"date": "entry_date", "open": "entry_open"})
    closes = frame[["code", "date", "close"]].rename(columns={"date": "exit_date", "close": "exit_close"})
    frame = frame.merge(opens, on=["code", "entry_date"], how="left", validate="many_to_one")
    frame = frame.merge(closes, on=["code", "exit_date"], how="left", validate="many_to_one")
    benchmark = benchmark.copy()
    benchmark["benchmark_interval_return"] = (
        benchmark["benchmark_close"].shift(-end_offset)
        / benchmark["benchmark_open"].shift(-start_offset)
        - 1.0
    )
    frame = frame.merge(
        benchmark[["date", "benchmark_interval_return"]],
        on="date",
        how="left",
        validate="many_to_one",
    )
    frame["forward_excess"] = (
        frame["exit_close"] / frame["entry_open"] - 1.0 - frame["benchmark_interval_return"]
    )
    valid = frame["entry_open"].notna() & frame["exit_close"].notna() & frame["benchmark_interval_return"].notna()
    frame["forward_excess"] = frame["forward_excess"].where(valid)
    return frame[["date", "code", "exit_date", "forward_excess"]]


def horizon_ic_analysis(
    predictions: pd.DataFrame, prices: pd.DataFrame, selection_end: pd.Timestamp = SELECTION_END
) -> pd.DataFrame:
    prediction_frame = predictions[["date", "code", "prediction"]].copy()
    prediction_frame["date"] = pd.to_datetime(prediction_frame["date"], errors="raise")
    prediction_frame["code"] = prediction_frame["code"].astype(str).str.zfill(6)
    rows: list[dict[str, Any]] = []
    for horizon in HORIZONS:
        labels = _forward_excess_for_horizon(prices, horizon)
        joined = prediction_frame.merge(labels, on=["date", "code"], how="left", validate="one_to_one")
        joined = joined.loc[joined["exit_date"].le(selection_end)].dropna(subset=["prediction", "forward_excess"])
        daily_ic = joined.groupby("date", sort=True).apply(
            lambda day: day["prediction"].corr(day["forward_excess"], method="spearman")
            if len(day) >= 5 else np.nan,
            include_groups=False,
        ).dropna()
        hac_ir = ic_ir_hac(daily_ic.to_numpy(dtype=float), lag=horizon)
        rows.append({
            "horizon": horizon,
            "ic_days": int(len(daily_ic)),
            "last_prediction_date": str(joined["date"].max().date()) if not joined.empty else None,
            "last_exit_date": str(joined["exit_date"].max().date()) if not joined.empty else None,
            "mean_daily_spearman_ic": float(daily_ic.mean()) if len(daily_ic) else None,
            "hac_ic_ir": float(hac_ir) if hac_ir is not None else None,
            "hac_t": float(hac_ir * np.sqrt(len(daily_ic))) if hac_ir is not None else None,
            "post_hoc_descriptive_only": True,
        })
    return pd.DataFrame(rows)


def horizon_interval_ic_analysis(
    predictions: pd.DataFrame, prices: pd.DataFrame, selection_end: pd.Timestamp = SELECTION_END
) -> pd.DataFrame:
    """Measure IC in disjoint forward windows to separate persistence from accumulation."""

    prediction_frame = predictions[["date", "code", "prediction"]].copy()
    prediction_frame["date"] = pd.to_datetime(prediction_frame["date"], errors="raise")
    prediction_frame["code"] = prediction_frame["code"].astype(str).str.zfill(6)
    rows: list[dict[str, Any]] = []
    for start_offset, end_offset in HORIZON_INTERVALS:
        labels = _forward_excess_for_interval(prices, start_offset, end_offset)
        joined = prediction_frame.merge(labels, on=["date", "code"], how="left", validate="one_to_one")
        joined = joined.loc[joined["exit_date"].le(selection_end)].dropna(subset=["prediction", "forward_excess"])
        daily_ic = joined.groupby("date", sort=True).apply(
            lambda day: day["prediction"].corr(day["forward_excess"], method="spearman")
            if len(day) >= 5 else np.nan,
            include_groups=False,
        ).dropna()
        lag = end_offset - start_offset + 1
        hac_ir = ic_ir_hac(daily_ic.to_numpy(dtype=float), lag=lag)
        rows.append({
            "interval": f"{start_offset}-{end_offset}",
            "start_offset": start_offset,
            "end_offset": end_offset,
            "ic_days": int(len(daily_ic)),
            "last_exit_date": str(joined["exit_date"].max().date()) if not joined.empty else None,
            "mean_daily_spearman_ic": float(daily_ic.mean()) if len(daily_ic) else None,
            "hac_ic_ir": float(hac_ir) if hac_ir is not None else None,
            "hac_t": float(hac_ir * np.sqrt(len(daily_ic))) if hac_ir is not None else None,
            "post_hoc_descriptive_only": True,
        })
    return pd.DataFrame(rows)


def _same_structure(full: dict[str, Any], candidate: dict[str, Any]) -> bool:
    return full["structure_signature"] == candidate["structure_signature"]


def analyze_and_decide(
    panel_path: str | Path = DEFAULT_PANEL,
    noise_path: str | Path = DEFAULT_NOISE,
    output_dir: str | Path = DEFAULT_OUT,
) -> dict[str, Any]:
    output = _path(output_dir)
    manifest = json.loads((output / "experiment_manifest.json").read_text(encoding="utf-8"))
    noise = json.loads(_path(noise_path).read_text(encoding="utf-8"))["models"]
    prices = pd.read_parquet(
        _path(panel_path),
        columns=["date", "code", "open", "close", "benchmark_open", "benchmark_close"],
        filters=[("date", "<=", SELECTION_END)],
    )
    market_status = pd.read_parquet(
        _path(panel_path),
        columns=["date", "code", "is_st"],
    )
    metrics: dict[str, dict[str, Any]] = {}
    deciles: dict[str, dict[str, Any]] = {}
    for pool in ("full", "candidate"):
        metrics[pool] = {}
        deciles[pool] = {}
        for model in manifest["primary_models"]:
            model_id = model["id"]
            run_dir = output / pool / model_id
            metrics[pool][model_id] = json.loads((run_dir / "metrics.json").read_text(encoding="utf-8"))
            predictions = pd.read_parquet(run_dir / "oof_predictions.parquet")
            table, summary = decile_analysis(predictions)
            table.to_csv(run_dir / "decile_table.csv", index=False, encoding="utf-8-sig")
            _write_json(run_dir / "decile_summary.json", summary)
            horizon_table = horizon_ic_analysis(predictions, prices)
            horizon_table.to_csv(run_dir / "horizon_ic.csv", index=False, encoding="utf-8-sig")
            interval_table = horizon_interval_ic_analysis(predictions, prices)
            interval_table.to_csv(run_dir / "horizon_interval_ic.csv", index=False, encoding="utf-8-sig")
            q1_rows, q1_summary = bottom_decile_tradeability(predictions, market_status)
            q1_rows.to_csv(run_dir / "q1_tradeability.csv", index=False, encoding="utf-8-sig")
            _write_json(run_dir / "q1_tradeability_summary.json", q1_summary)
            deciles[pool][model_id] = summary
    checks: list[dict[str, Any]] = []
    for model in manifest["primary_models"]:
        model_id = model["id"]
        full = metrics["full"][model_id]
        candidate = metrics["candidate"][model_id]
        full_q = full["overall_quality"]
        candidate_q = candidate["overall_quality"]
        sigma = float(noise[model_id]["noise_std"])
        model_checks = {
            "ic_within_1sigma": float(candidate_q["mean_daily_spearman_ic"]) >= float(full_q["mean_daily_spearman_ic"]) - sigma,
            "hac_ic_ir_not_lower": float(candidate_q["ic_ir_hac"]) >= float(full_q["ic_ir_hac"]),
            "fold_sign_consistency_one": float(candidate["fold_stability"]["fold_ic_sign_consistency"]) == 1.0,
            "decile_structure_unchanged": _same_structure(deciles["full"][model_id], deciles["candidate"][model_id]),
        }
        checks.append({
            "model": model_id,
            "full_ic": full_q["mean_daily_spearman_ic"],
            "candidate_ic": candidate_q["mean_daily_spearman_ic"],
            "noise_std": sigma,
            "full_hac_ic_ir": full_q["ic_ir_hac"],
            "candidate_hac_ic_ir": candidate_q["ic_ir_hac"],
            **model_checks,
            "model_pass": all(model_checks.values()),
        })
    decision = {
        "classification": "post_hoc_descriptive_only",
        "selection_only": True,
        "checks": checks,
        "confirmation_pass": all(check["model_pass"] for check in checks),
        "failure_policy": manifest["failure_policy"],
        "evaluation_authorized": False,
    }
    _write_json(output / "confirmation_decision.json", decision)
    return decision


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the registered post-LOFO B2 confirmation")
    sub = parser.add_subparsers(dest="command", required=True)
    register = sub.add_parser("register")
    register.add_argument("--full-features", default=str(DEFAULT_FULL_FEATURES))
    register.add_argument("--candidate-features", default=str(DEFAULT_CANDIDATE_FEATURES))
    register.add_argument("--noise", default=str(DEFAULT_NOISE))
    register.add_argument("--out-dir", default=str(DEFAULT_OUT))
    run = sub.add_parser("run")
    run.add_argument("--panel", default=str(DEFAULT_PANEL))
    run.add_argument("--noise", default=str(DEFAULT_NOISE))
    run.add_argument("--out-dir", default=str(DEFAULT_OUT))
    args = parser.parse_args()
    if args.command == "register":
        result = register_confirmation(args.full_features, args.candidate_features, args.noise, args.out_dir)
    else:
        run_confirmation_models(args.panel, args.out_dir)
        result = analyze_and_decide(args.panel, args.noise, args.out_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
