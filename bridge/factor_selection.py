"""Factor hygiene pipeline: per-family IC, cross-sectional rank correlation,
greedy redundancy filtering, then export the reduced feature list.

All outputs are ``post_hoc_descriptive_only``: they may only design the next
pre-registered candidate, never select a production factor.
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

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PANEL = PROJECT_ROOT / "data" / "panel_fusion_full.parquet"
DEFAULT_OUT_DIR = PROJECT_ROOT / "runs" / "factor_selection"
TARGET = "EXRET20"
CLASSIFICATION = "post_hoc_descriptive_only"
IC_HAC_LAG = 20
CORRELATION_REDUNDANCY_THRESHOLD = 0.80
ROLLING_IC_WINDOW = 60
ROLLING_IC_MIN_PERIODS = 20
RECENT_IC_WINDOW = 120
LABEL_GAP_TRADING_DAYS = 20

V6_COLUMNS = [
    "SQZ_ATR_REL__score",
    "SQZ_RANGE_ABS__score",
    "TRG_VOL_EXP__score",
    "POS_EMA20_BAND__score",
]
CAPITAL_COLUMNS = ["market_cap", "total_shares"]
STARTER_IDS = {
    "mom_20",
    "rev_5",
    "vol_20",
    "trend_10",
    "corr_pv_10",
    "range_15",
    "amt_ratio_5",
    "vwap_dev_10",
}
DERIVED_SUFFIXES = (
    "_cs_rank",
    "_cs_z",
    "_cs_winsor",
    "_ind_neutral",
)
DERIVED_MARKERS = ("_x_", "_div_")


def factor_columns(panel: pd.DataFrame) -> list[str]:
    columns = [
        *[c for c in V6_COLUMNS if c in panel.columns],
        *[c for c in CAPITAL_COLUMNS if c in panel.columns],
    ]
    columns.extend(
        sorted(
            c
            for c in panel.columns
            if c.startswith("alpha") or c.startswith("gtja")
        )
    )
    columns.extend(
        sorted(
            c
            for c in panel.columns
            if (c.endswith(DERIVED_SUFFIXES) or any(marker in c for marker in DERIVED_MARKERS))
            and c not in columns
        )
    )
    return columns


def family_of(column: str) -> str:
    if column in V6_COLUMNS:
        return "v6"
    if column in CAPITAL_COLUMNS:
        return "capital"
    if column.startswith("alpha_qg_f"):
        return "llm"
    if column.startswith("alpha_qg_"):
        stem = column[len("alpha_qg_") :]
        return "starter" if stem in STARTER_IDS else "llm"
    if column.startswith("alpha"):
        return "alpha101"
    if column.startswith("gtja"):
        return "gtja191"
    return "other"


def info_type_of(column: str, expression_map: dict[str, str] | None = None) -> str:
    expr = (expression_map or {}).get(column, column.lower())
    if any(k in expr for k in ("mom", "delta", "decay", "trend", "close/ts_mean")):
        return "momentum"
    if any(k in expr for k in ("rev", "-ts_mean(returns", "-ts_delta", "mean_reversion")):
        return "reversal"
    if any(k in expr for k in ("std", "vol", "range", "squeeze", "atr")):
        return "volatility"
    if any(k in expr for k in ("corr", "cov")):
        return "correlation"
    if any(k in expr for k in ("volume", "amount", "adv", "vwap")):
        return "volume_vwap"
    if any(k in expr for k in ("cap", "market_cap", "share")):
        return "capital"
    return "other"


def daily_ic_series(panel: pd.DataFrame, factor: str, target: str = TARGET) -> pd.Series:
    work = panel[[factor, target, "date"]].dropna().copy()
    if work.empty:
        return pd.Series(dtype=float)

    def _ic(day: pd.DataFrame) -> float:
        if len(day) < 5 or day[factor].nunique() < 5 or day[target].nunique() < 2:
            return float("nan")
        return float(spearmanr(day[factor], day[target]).statistic)

    return work.groupby("date", sort=True).apply(_ic, include_groups=False).dropna()


def ic_ir_hac(values: np.ndarray, lag: int = IC_HAC_LAG) -> float | None:
    """Mean IC divided by its Newey-West long-run standard deviation."""
    observations = np.asarray(values, dtype=float)
    observations = observations[np.isfinite(observations)]
    if len(observations) < 2:
        return None
    centered = observations - observations.mean()
    effective_lag = min(int(lag), len(observations) - 1)
    long_run_variance = float(np.dot(centered, centered) / len(observations))
    for offset in range(1, effective_lag + 1):
        weight = 1.0 - offset / (effective_lag + 1)
        autocovariance = float(
            np.dot(centered[offset:], centered[:-offset]) / len(observations)
        )
        long_run_variance += 2.0 * weight * autocovariance
    if long_run_variance <= 0:
        return None
    return float(observations.mean() / np.sqrt(long_run_variance))


def daily_ic(panel: pd.DataFrame, factor: str, target: str = TARGET) -> dict[str, Any]:
    ics = daily_ic_series(panel, factor, target)
    if len(ics) < 50:
        return {
            "factor": factor,
            "ic_mean": None,
            "ic_std": None,
            "ic_ir": None,
            "ic_ir_unadjusted": None,
            "ic_mean_hac_tstat": None,
            "ic_hac_lag": None,
            "ic_win_rate": None,
            "coverage": float(panel[factor].notna().mean()),
            "n_days": 0,
        }
    values = ics.to_numpy(dtype=float)
    standard_deviation = float(values.std(ddof=1))
    unadjusted_ir = float(values.mean() / standard_deviation) if standard_deviation > 0 else None
    hac_ir = ic_ir_hac(values)
    return {
        "factor": factor,
        "ic_mean": round(float(values.mean()), 6),
        "ic_std": round(standard_deviation, 6),
        "ic_ir": round(hac_ir, 6) if hac_ir is not None else None,
        "ic_ir_unadjusted": round(unadjusted_ir, 6) if unadjusted_ir is not None else None,
        "ic_mean_hac_tstat": round(hac_ir * np.sqrt(len(values)), 6)
        if hac_ir is not None
        else None,
        "ic_hac_lag": min(IC_HAC_LAG, len(values) - 1),
        "ic_win_rate": round(float((values > 0).mean()), 6),
        "coverage": round(float(panel[factor].notna().mean()), 6),
        "n_days": int(len(values)),
    }


def build_ic_table(panel: pd.DataFrame, columns: list[str], expression_map: dict[str, str]) -> pd.DataFrame:
    rows = [daily_ic(panel, column) for column in columns]
    table = pd.DataFrame(rows)
    table["family"] = table["factor"].map(family_of)
    table["info_type"] = table["factor"].map(
        lambda column: info_type_of(column, expression_map)
    )
    return table


def build_rank_correlation(panel: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    if not columns:
        return pd.DataFrame()
    total = np.zeros((len(columns), len(columns)), dtype=float)
    count = np.zeros((len(columns), len(columns)), dtype=int)
    for _, day in panel.groupby("date", sort=False):
        ranks = day[columns].rank(pct=True)
        correlation = ranks.corr(method="pearson", min_periods=5).to_numpy(dtype=float)
        valid = np.isfinite(correlation)
        total[valid] += correlation[valid]
        count[valid] += 1
    average = np.full(total.shape, np.nan, dtype=float)
    np.divide(total, count, out=average, where=count > 0)
    return pd.DataFrame(average, index=columns, columns=columns)


def greedy_representatives(
    corr: pd.DataFrame,
    ic_table: pd.DataFrame,
    *,
    threshold: float,
    max_keep: int | None = None,
) -> list[str]:
    priority = ic_table.set_index("factor")
    columns = list(corr.columns)
    # Priority: higher |IC| first, then coverage, then family diversity is
    # handled by the correlation constraint itself.
    columns.sort(
        key=lambda c: (
            -abs(priority.loc[c, "ic_mean"]) if pd.notna(priority.loc[c, "ic_mean"]) else -0.0,
            -(priority.loc[c, "coverage"] or 0.0),
            c,
        )
    )
    selected: list[str] = []
    for column in columns:
        if not selected:
            selected.append(column)
            continue
        correlations = corr.loc[column, selected].abs().dropna()
        if correlations.empty or correlations.max() <= threshold:
            selected.append(column)
        if max_keep is not None and len(selected) >= max_keep:
            break
    return selected


def choose_threshold(corr: pd.DataFrame, ic_table: pd.DataFrame) -> tuple[float, list[str]]:
    selected = greedy_representatives(
        corr, ic_table, threshold=CORRELATION_REDUNDANCY_THRESHOLD
    )
    return CORRELATION_REDUNDANCY_THRESHOLD, selected


def select_top_abs_ic_factors(
    ic_table: pd.DataFrame,
    *,
    top_n: int = 5,
    minimum_abs_ic: float | None = None,
) -> list[str]:
    """Select factor names by absolute selection-period IC only."""
    if top_n < 1:
        return []
    required = {"factor", "ic_mean"}
    missing = required.difference(ic_table.columns)
    if missing:
        raise ValueError(f"IC table missing columns {sorted(missing)}")
    work = ic_table.dropna(subset=["ic_mean"]).copy()
    work["abs_ic"] = work["ic_mean"].abs()
    if minimum_abs_ic is not None:
        work = work.loc[work["abs_ic"].ge(minimum_abs_ic)]
    return (
        work.sort_values(["abs_ic", "factor"], ascending=[False, True], kind="mergesort")
        .head(top_n)["factor"]
        .tolist()
    )


def split_selection_panel(
    panel: pd.DataFrame,
    selection_end: str | pd.Timestamp | None = None,
    *,
    label_gap_days: int = LABEL_GAP_TRADING_DAYS,
) -> tuple[pd.DataFrame, pd.Timestamp, pd.Timestamp]:
    """Split selection and evaluation periods with a forward-label net gap.

    EXRET20 for the last selection date uses prices 20 trading dates later.
    The evaluation period therefore starts after 20 intervening trading dates,
    matching the purged walk-forward convention used by ``bridge.gbdt``.
    """
    if label_gap_days < 0:
        raise ValueError("label_gap_days must be non-negative")
    date_values = pd.to_datetime(panel["date"], errors="raise")
    dates = pd.DatetimeIndex(date_values.unique()).sort_values()
    if len(dates) < 2:
        raise ValueError("factor selection requires at least two distinct trading dates")
    cutoff = (
        pd.Timestamp(selection_end)
        if selection_end is not None
        else dates[(len(dates) - 1) // 2]
    )
    selected = panel.loc[date_values.le(cutoff)].copy()
    cutoff_position = int(dates.searchsorted(cutoff, side="right") - 1)
    evaluation_start_position = cutoff_position + label_gap_days + 1
    evaluation = dates[evaluation_start_position:]
    if selected.empty or evaluation.empty:
        raise ValueError(
            "selection_end must leave non-empty selection and evaluation periods "
            f"after label gap={label_gap_days}"
        )
    return selected, cutoff, evaluation[0]


def build_direction_review(
    panel: pd.DataFrame,
    columns: list[str],
    registered_directions: dict[str, str] | None = None,
    *,
    recent_window: int = RECENT_IC_WINDOW,
    min_days: int = ROLLING_IC_MIN_PERIODS,
) -> pd.DataFrame:
    """Create human-review direction suggestions without changing factor data.

    ``recommended_direction`` is descriptive only.  No score is negated and
    no configuration is rewritten by this function.
    """
    registered_directions = registered_directions or {}
    rows: list[dict[str, Any]] = []
    for column in columns:
        ics = daily_ic_series(panel, column)
        finite = ics[np.isfinite(ics)]
        recent = finite.iloc[-recent_window:]
        historical = finite.iloc[:-recent_window] if len(finite) > recent_window else pd.Series(dtype=float)
        rolling = finite.rolling(
            ROLLING_IC_WINDOW, min_periods=ROLLING_IC_MIN_PERIODS
        ).mean().dropna()
        if len(finite) < min_days:
            rows.append(
                {
                    "factor": column,
                    "registered_direction": registered_directions.get(column, "unknown"),
                    "recommended_direction": "insufficient_data",
                    "selection_ic_mean": float(finite.mean()) if len(finite) else None,
                    "recent_ic_mean": float(recent.mean()) if len(recent) else None,
                    "historical_ic_mean": float(historical.mean()) if len(historical) else None,
                    "sign_consistency": None,
                    "sign_switch_count": None,
                    "daily_sign_switch_count": None,
                    "direction_window_days": ROLLING_IC_WINDOW,
                    "regime_reversal": False,
                    "market_style_change": False,
                    "frequent_direction_switch": False,
                    "direction_transform_required": False,
                    "direction_change_reason": "insufficient_data",
                    "review_status": "insufficient_data",
                    "manual_review_required": True,
                }
            )
            continue
        values = finite.to_numpy(dtype=float)
        direction_values = rolling.to_numpy(dtype=float) if len(rolling) else values
        dominant_sign = 1 if values.mean() >= 0 else -1
        nonzero_signs = np.sign(direction_values[direction_values != 0])
        sign_consistency = float((nonzero_signs == dominant_sign).mean()) if len(nonzero_signs) else 0.0
        switches = int(np.count_nonzero(nonzero_signs[1:] != nonzero_signs[:-1])) if len(nonzero_signs) > 1 else 0
        daily_signs = np.sign(values[values != 0])
        daily_switches = int(
            np.count_nonzero(daily_signs[1:] != daily_signs[:-1])
        ) if len(daily_signs) > 1 else 0
        recent_mean = float(recent.mean()) if len(recent) else float("nan")
        historical_mean = float(historical.mean()) if len(historical) else float("nan")
        regime_reversal = bool(
            len(recent) >= min_days
            and len(historical) >= min_days
            and np.isfinite(recent_mean)
            and np.isfinite(historical_mean)
            and recent_mean * historical_mean < 0
        )
        if regime_reversal:
            status = "regime_sensitive"
        elif sign_consistency < 0.55 or switches > max(2, len(nonzero_signs) // 10):
            status = "unstable"
        else:
            status = "stable"
        frequent_switch = switches > max(2, len(nonzero_signs) // 10)
        recommended = "long" if dominant_sign > 0 else "short"
        registered = registered_directions.get(column, "unknown")
        reasons = []
        if regime_reversal:
            reasons.append("market_style_change")
        if frequent_switch or sign_consistency < 0.55:
            reasons.append("frequent_direction_switch")
        if registered in {"long", "short"} and registered != recommended:
            reasons.append("registered_direction_differs")
        rows.append(
            {
                "factor": column,
                "registered_direction": registered,
                "recommended_direction": recommended,
                "selection_ic_mean": float(values.mean()),
                "recent_ic_mean": recent_mean,
                "historical_ic_mean": historical_mean,
                "sign_consistency": sign_consistency,
                "sign_switch_count": switches,
                "daily_sign_switch_count": daily_switches,
                "direction_window_days": ROLLING_IC_WINDOW,
                "regime_reversal": regime_reversal,
                "market_style_change": regime_reversal,
                "frequent_direction_switch": frequent_switch,
                "direction_transform_required": bool(
                    registered in {"long", "short"} and registered != recommended
                ),
                "direction_change_reason": ";".join(reasons) or "none",
                "review_status": status,
                "manual_review_required": bool(
                    status != "stable"
                    or registered_directions.get(column, "unknown")
                    not in {"unknown", recommended}
                ),
            }
        )
    return pd.DataFrame(rows)


def rolling_ic_series(
    panel: pd.DataFrame,
    factor: str,
    target: str = TARGET,
    *,
    window: int = ROLLING_IC_WINDOW,
    min_periods: int = ROLLING_IC_MIN_PERIODS,
) -> pd.Series:
    dates = pd.DatetimeIndex(pd.to_datetime(panel["date"].unique())).sort_values()
    return daily_ic_series(panel, factor, target).reindex(dates).rolling(
        window, min_periods=min_periods
    ).mean()


def build_ic_stability_table(panel: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for column in columns:
        ics = daily_ic_series(panel, column)
        recent = ics.iloc[-RECENT_IC_WINDOW:]
        historical = ics.iloc[:-RECENT_IC_WINDOW]
        recent_mean = float(recent.mean()) if len(recent) else float("nan")
        historical_mean = float(historical.mean()) if len(historical) else float("nan")
        reversed_recently = bool(
            len(recent) >= ROLLING_IC_MIN_PERIODS
            and len(historical) >= ROLLING_IC_MIN_PERIODS
            and np.isfinite(recent_mean)
            and np.isfinite(historical_mean)
            and recent_mean * historical_mean < 0
        )
        rows.append(
            {
                "factor": column,
                "recent_ic_mean": recent_mean,
                "historical_ic_mean": historical_mean,
                "recently_reversed": reversed_recently,
            }
        )
    return pd.DataFrame(rows)


def build_rolling_ic_by_family(panel: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for column in columns:
        values = rolling_ic_series(panel, column).dropna()
        rows.extend(
            {
                "date": date,
                "family": family_of(column),
                "factor": column,
                "rolling_ic": float(value),
            }
            for date, value in values.items()
        )
    if not rows:
        return pd.DataFrame(columns=["date", "family", "rolling_ic", "factor_count"])
    detail = pd.DataFrame(rows)
    return (
        detail.groupby(["date", "family"], sort=True)
        .agg(rolling_ic=("rolling_ic", "mean"), factor_count=("factor", "nunique"))
        .reset_index()
    )


def run(
    panel_path: Path | str = DEFAULT_PANEL,
    out_dir: Path | str = DEFAULT_OUT_DIR,
    expression_path: Path | str = PROJECT_ROOT / "config" / "expressions_combined.json",
    *,
    selection_end: str | pd.Timestamp | None = None,
) -> dict[str, Any]:
    panel = pd.read_parquet(panel_path)
    panel["date"] = pd.to_datetime(panel["date"], errors="raise")
    selection_panel, selection_end_date, evaluation_start = split_selection_panel(
        panel, selection_end, label_gap_days=LABEL_GAP_TRADING_DAYS
    )
    columns = factor_columns(selection_panel)

    expression_map: dict[str, str] = {}
    direction_map: dict[str, str] = {}
    specs = json.loads(Path(expression_path).read_text(encoding="utf-8"))["expressions"]
    for spec in specs:
        expression_map[f"alpha_qg_{spec['id']}"] = spec["expression"].lower()
        direction_map[f"alpha_qg_{spec['id']}"] = str(spec.get("direction", "unknown")).lower()

    ic_table = build_ic_table(selection_panel, columns, expression_map)
    stability = build_ic_stability_table(selection_panel, columns)
    ic_table = ic_table.merge(stability, on="factor", how="left", validate="one_to_one")
    corr = build_rank_correlation(selection_panel, columns)
    threshold, selected = choose_threshold(corr, ic_table)
    rolling_by_family = build_rolling_ic_by_family(selection_panel, columns)
    direction_review = build_direction_review(selection_panel, columns, direction_map)
    industry_neutral_candidates = select_top_abs_ic_factors(
        ic_table, top_n=len(columns), minimum_abs_ic=0.02
    )
    cross_seed_factors = select_top_abs_ic_factors(ic_table, top_n=5)

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    ic_table.to_csv(out_dir / "factor_ic_table.csv", index=False, encoding="utf-8-sig")
    corr.to_parquet(out_dir / "factor_rank_correlation.parquet")
    rolling_by_family.to_csv(out_dir / "rolling_ic_by_family.csv", index=False, encoding="utf-8-sig")
    direction_review.to_csv(out_dir / "direction_review.csv", index=False, encoding="utf-8-sig")
    augmentation_plan = {
        "classification": CLASSIFICATION,
        "selection_period_end": str(selection_end_date.date()),
        "label_gap_trading_days": LABEL_GAP_TRADING_DAYS,
        "cs_source_factors": columns,
        "industry_neutral_factors": industry_neutral_candidates,
        "industry_neutral_threshold_abs_ic": 0.02,
        "cross_seed_factors": cross_seed_factors,
        "cross_feature_count": len(cross_seed_factors) * (len(cross_seed_factors) - 1),
        "selection_only": True,
        "automatic_direction_flip": False,
    }
    (out_dir / "feature_augmentation_plan.json").write_text(
        json.dumps(augmentation_plan, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    family_summary = (
        ic_table.groupby("family")
        .agg(
            count=("factor", "size"),
            mean_ic=("ic_mean", "mean"),
            mean_abs_ic=("ic_mean", lambda s: s.abs().mean()),
            median_coverage=("coverage", "median"),
            mean_ic_win_rate=("ic_win_rate", "mean"),
        )
        .reset_index()
    )
    family_summary.to_csv(out_dir / "factor_family_summary.csv", index=False, encoding="utf-8-sig")

    payload = {
        "classification": CLASSIFICATION,
        "target": TARGET,
        "selection_period_start": str(selection_panel["date"].min().date()),
        "selection_period_end": str(selection_end_date.date()),
        "evaluation_period_start": str(evaluation_start.date()),
        "label_gap_trading_days": LABEL_GAP_TRADING_DAYS,
        "selected_features_file": "selected_features.json",
        "ic_ir_method": f"Newey-West long-run standard deviation (lag={IC_HAC_LAG})",
        "total_columns": len(columns),
        "selected_columns": selected,
        "correlation_threshold": threshold,
        "kept_count": len(selected),
        "removed_count": len(columns) - len(selected),
        "recently_reversed_count": int(ic_table["recently_reversed"].fillna(False).sum()),
        "direction_review_file": "direction_review.csv",
        "feature_augmentation_plan_file": "feature_augmentation_plan.json",
        "industry_neutral_candidate_count": len(industry_neutral_candidates),
        "cross_seed_factors": cross_seed_factors,
        "family_summary": family_summary.to_dict(orient="records"),
        "top_abs_ic_20": ic_table.assign(
            abs_ic=ic_table["ic_mean"].abs()
        ).sort_values("abs_ic", ascending=False).head(20).to_dict(orient="records"),
    }
    (out_dir / "selection_manifest.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (out_dir / "selected_features.json").write_text(
        json.dumps(selected, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "total_columns": len(columns),
                "kept_count": len(selected),
                "threshold": threshold,
                "out_dir": str(out_dir),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Factor hygiene and redundancy filter")
    parser.add_argument("--panel", default=str(DEFAULT_PANEL))
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument("--expressions", default=str(PROJECT_ROOT / "config" / "expressions_combined.json"))
    parser.add_argument("--selection-end", default=None)
    args = parser.parse_args()
    run(args.panel, args.out_dir, args.expressions, selection_end=args.selection_end)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
