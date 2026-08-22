"""Factor hygiene pipeline: per-family IC, cross-sectional rank correlation,
greedy redundancy filtering, then export the reduced feature list.

All outputs are ``post_hoc_descriptive_only``: they may only design the next
pre-registered candidate, never select a production factor.
"""

from __future__ import annotations

import argparse
import gc
import json
import sys
from pathlib import Path
from typing import Any, Iterable, Sequence

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PANEL = PROJECT_ROOT / "data" / "panel_fusion_full.parquet"
DEFAULT_OUT_DIR = PROJECT_ROOT / "runs" / "factor_selection"
TARGET = "EXRET20"
DEFAULT_SELECTION_PANEL = PROJECT_ROOT / "data" / "panel_fusion_full_market.parquet"
DEFAULT_SELECTION_OUT_DIR = PROJECT_ROOT / "runs" / "factor_selection_v3_b2_neutral_selection"
DEFAULT_SELECTION_UNIVERSE = "b2"
DEFAULT_SELECTION_TARGET = "y_neutral_rank20_b2"
CLASSIFICATION = "post_hoc_descriptive_only"
IC_HAC_LAG = 20
CORRELATION_REDUNDANCY_THRESHOLD = 0.80
MIN_ABS_SELECTION_IC = 0.01
REGIME_REVIEW_MIN_ABS_IC = 0.02
ROLLING_IC_WINDOW = 60
ROLLING_IC_MIN_PERIODS = 20
RECENT_IC_WINDOW = 120
LABEL_GAP_TRADING_DAYS = 20
WATCH_ONLY_FACTORS = ("alpha004", "gtja_183", "alpha052")
WATCH_ONLY_RECENT_IC_THRESHOLD = 0.03
WATCH_ONLY_UPGRADE_RULE = (
    "recent and historical IC must have the same non-zero sign and "
    "abs(recent IC) > 0.03; upgrade to pre_registered_candidate for human review"
)
STATIC_INELIGIBLE_DIAGNOSES = {
    "all_missing",
    "low_cross_sectional_distinctness",
    "insufficient_target_or_cross_section_sample",
}
# These names are not imported as decisions.  They are a hold-out list for
# explicit re-adjudication because the 2026-08-18 review used another panel
# scope/target.  A current B2 result may approve or reject them later.
LEGACY_REVIEW_CONFLICT_FACTORS = frozenset(
    {
        "alpha004",
        "alpha052",
        "alpha055",
        "alpha059",
        "alpha071",
        "alpha091",
        "alpha093",
        "gtja_001",
        "gtja_058",
        "gtja_072",
        "gtja_073",
        "gtja_082",
        "gtja_090",
        "gtja_125",
        "gtja_181",
        "gtja_183",
        "gtja_176",
    }
)

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


def build_ic_table(
    panel: pd.DataFrame,
    columns: list[str],
    expression_map: dict[str, str],
    *,
    target: str = TARGET,
) -> pd.DataFrame:
    rows = [daily_ic(panel, column, target) for column in columns]
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
    target: str = TARGET,
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
        ics = daily_ic_series(panel, column, target)
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
                    "raw_regime_reversal": False,
                    "regime_amplitude_max_abs_ic": None,
                    "regime_amplitude_threshold_abs_ic": REGIME_REVIEW_MIN_ABS_IC,
                    "regime_amplitude_sufficient": False,
                    "no_significant_signal": False,
                    "regime_reversal": False,
                    "market_style_change": False,
                    "frequent_direction_switch": False,
                    "direction_transform_required": False,
                    "direction_change_reason": "insufficient_data",
                    "review_status": "insufficient_data",
                    "manual_review_required": False,
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
        raw_regime_reversal = bool(
            len(recent) >= min_days
            and len(historical) >= min_days
            and np.isfinite(recent_mean)
            and np.isfinite(historical_mean)
            and recent_mean * historical_mean < 0
        )
        regime_amplitude = float(
            max(abs(recent_mean), abs(historical_mean))
            if np.isfinite(recent_mean) and np.isfinite(historical_mean)
            else 0.0
        )
        regime_amplitude_sufficient = regime_amplitude > REGIME_REVIEW_MIN_ABS_IC
        regime_reversal = raw_regime_reversal and regime_amplitude_sufficient
        no_significant_signal = raw_regime_reversal and not regime_amplitude_sufficient
        if no_significant_signal:
            status = "no_significant_signal"
        elif regime_reversal:
            status = "regime_sensitive"
        elif sign_consistency < 0.55 or switches > max(2, len(nonzero_signs) // 10):
            status = "unstable"
        else:
            status = "stable"
        frequent_switch = switches > max(2, len(nonzero_signs) // 10)
        recommended = "long" if dominant_sign > 0 else "short"
        registered = registered_directions.get(column, "unknown")
        reasons = []
        if no_significant_signal:
            reasons.append("regime_reversal_below_amplitude_threshold")
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
                "raw_regime_reversal": raw_regime_reversal,
                "regime_amplitude_max_abs_ic": regime_amplitude,
                "regime_amplitude_threshold_abs_ic": REGIME_REVIEW_MIN_ABS_IC,
                "regime_amplitude_sufficient": regime_amplitude_sufficient,
                "no_significant_signal": no_significant_signal,
                "regime_reversal": regime_reversal,
                "market_style_change": regime_reversal,
                "frequent_direction_switch": frequent_switch,
                "direction_transform_required": bool(
                    registered in {"long", "short"} and registered != recommended
                ),
                "direction_change_reason": ";".join(reasons) or "none",
                "review_status": status,
                "manual_review_required": bool(
                    status in {"regime_sensitive", "unstable"}
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


def build_ic_stability_table(
    panel: pd.DataFrame,
    columns: list[str],
    *,
    target: str = TARGET,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for column in columns:
        ics = daily_ic_series(panel, column, target)
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


def build_rolling_ic_by_family(
    panel: pd.DataFrame,
    columns: list[str],
    *,
    target: str = TARGET,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for column in columns:
        values = rolling_ic_series(panel, column, target).dropna()
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


def _parquet_factor_columns(path: Path | str) -> list[str]:
    """List factor columns without loading a potentially multi-GB parquet file."""
    try:
        import pyarrow.parquet as pq

        names = pq.ParquetFile(path).schema.names
    except Exception:
        names = list(pd.read_parquet(path, engine="auto").columns)
    return [
        column
        for column in names
        if column.startswith(("alpha", "gtja"))
    ]


def _parquet_columns(path: Path | str) -> list[str]:
    """Return parquet schema names without materialising the table."""
    try:
        import pyarrow.parquet as pq

        return list(pq.ParquetFile(path).schema.names)
    except Exception:
        return list(pd.read_parquet(path, engine="auto").columns)


def _read_selection_panel(path: Path | str, *, universe: str, target: str) -> pd.DataFrame:
    """Read only columns needed for selection, keeping the memory peak bounded."""
    available = set(_parquet_columns(path))
    required = {
        "date",
        "code",
        "panel_scope",
        f"universe_{universe}",
        target,
        "universe_version",
    }
    required.update(column for column in V6_COLUMNS + CAPITAL_COLUMNS if column in available)
    required.update(
        column
        for column in available
        if column.startswith(("alpha", "gtja"))
        or column.endswith(DERIVED_SUFFIXES)
        or any(marker in column for marker in DERIVED_MARKERS)
    )
    missing = required - available
    if missing:
        # The contract validator will give the user the complete missing-column
        # message after the read; fail here only for an impossible empty read.
        required = available.intersection(required)
    return pd.read_parquet(path, columns=sorted(required))


def _merge_factor_files(
    panel: pd.DataFrame,
    factor_paths: Sequence[Path | str],
) -> pd.DataFrame:
    """Attach all external factors with one key join where DuckDB is available."""
    paths = [Path(path) for path in factor_paths]
    if not paths:
        return panel
    existing = set(panel.columns)
    file_columns: list[tuple[Path, list[str]]] = []
    for path in paths:
        if not path.is_file():
            raise FileNotFoundError(f"factor parquet does not exist: {path}")
        columns = [column for column in _parquet_factor_columns(path) if column not in existing]
        if columns:
            file_columns.append((path, columns))
            existing.update(columns)
    if not file_columns:
        return panel
    keys = panel[["date", "code"]].drop_duplicates().copy()
    keys["date"] = pd.to_datetime(keys["date"], errors="raise")
    keys["code"] = keys["code"].astype(str).str.zfill(6)
    try:
        import duckdb

        connection = duckdb.connect(database=":memory:")
        connection.register("selection_keys", keys)
        joins: list[str] = []
        selections = ["k.date", "k.code"]
        for index, (path, columns) in enumerate(file_columns):
            alias = f"f{index}"
            duplicate = connection.execute(
                "SELECT 1 FROM read_parquet(?) GROUP BY date, code "
                "HAVING COUNT(*) > 1 LIMIT 1",
                [str(path)],
            ).fetchall()
            if duplicate:
                connection.close()
                raise ValueError(f"factor parquet has duplicate (date, code) keys: {path}")
            joins.append(
                f"LEFT JOIN read_parquet(?) AS {alias} ON "
                f"CAST({alias}.date AS DATE) = k.date "
                f"AND LPAD(CAST({alias}.code AS VARCHAR), 6, '0') = k.code"
            )
            selections.extend(f'{alias}."{column}" AS "{column}"' for column in columns)
        query = (
            "SELECT " + ", ".join(selections) + " FROM selection_keys AS k "
            + " ".join(joins)
        )
        params = [str(path) for path, _ in file_columns]
        factor = connection.execute(query, params).fetch_df()
        connection.close()
    except ImportError:
        factor = panel[["date", "code"]].copy()
        for path, columns in file_columns:
            factor = factor.merge(
                pd.read_parquet(path, columns=["date", "code", *columns]),
                on=["date", "code"],
                how="left",
                validate="one_to_one",
            )
    factor["date"] = pd.to_datetime(factor["date"], errors="raise")
    factor["code"] = factor["code"].astype(str).str.zfill(6)
    if factor.duplicated(["date", "code"]).any():
        raise ValueError("factor parquet files contain duplicate (date, code) keys")
    return panel.merge(factor, on=["date", "code"], how="left", validate="one_to_one", sort=False)


def _merge_factor_file(
    panel: pd.DataFrame,
    factor_path: Path | str,
) -> pd.DataFrame:
    """Join one external factor parquet to an already filtered selection panel.

    The join is key-based and reads only rows whose ``(date, code)`` keys are
    present in the selection panel.  This keeps full Alpha101/GTJA191 review
    feasible without materialising a duplicate full-market panel in memory.
    """
    path = Path(factor_path)
    if not path.is_file():
        raise FileNotFoundError(f"factor parquet does not exist: {path}")
    columns = _parquet_factor_columns(path)
    if not columns:
        raise ValueError(f"factor parquet has no alpha/gtja columns: {path}")
    existing = set(panel.columns)
    columns = [column for column in columns if column not in existing]
    if not columns:
        return panel
    keys = panel[["date", "code"]].drop_duplicates().copy()
    keys["date"] = pd.to_datetime(keys["date"], errors="raise")
    keys["code"] = keys["code"].astype(str).str.zfill(6)
    try:
        import duckdb

        connection = duckdb.connect(database=":memory:")
        connection.register("selection_keys", keys)
        selected_columns = ", ".join(f'f."{column}"' for column in columns)
        query = (
            "SELECT CAST(f.date AS DATE) AS date, "
            "LPAD(CAST(f.code AS VARCHAR), 6, '0') AS code, "
            f"{selected_columns} FROM read_parquet(?) AS f "
            "INNER JOIN selection_keys AS k "
            "ON CAST(f.date AS DATE) = CAST(k.date AS DATE) "
            "AND LPAD(CAST(f.code AS VARCHAR), 6, '0') = CAST(k.code AS VARCHAR)"
        )
        factor = connection.execute(query, [str(path)]).fetch_df()
        connection.close()
    except ImportError:
        # Tests and small ad-hoc panels do not require DuckDB.  The fallback
        # still uses an explicit key join and never relies on row position.
        factor = pd.read_parquet(path, columns=["date", "code", *columns])
        factor["date"] = pd.to_datetime(factor["date"], errors="raise")
        factor["code"] = factor["code"].astype(str).str.zfill(6)
        factor = factor.merge(keys, on=["date", "code"], how="inner", validate="many_to_one")
    factor["date"] = pd.to_datetime(factor["date"], errors="raise")
    factor["code"] = factor["code"].astype(str).str.zfill(6)
    if factor.duplicated(["date", "code"]).any():
        raise ValueError(f"factor parquet has duplicate (date, code) keys: {path}")
    return panel.merge(
        factor,
        on=["date", "code"],
        how="left",
        validate="one_to_one",
        sort=False,
    )


def _as_bool_mask(values: pd.Series) -> pd.Series:
    if pd.api.types.is_bool_dtype(values):
        return values.fillna(False)
    return values.astype("string").str.strip().str.lower().isin(
        {"1", "1.0", "true", "t", "yes", "y"}
    )


def validate_selection_contract(
    panel: pd.DataFrame,
    *,
    universe: str,
    target: str,
    panel_scope: str = "full_market",
) -> None:
    """Reject legacy events panels before any selection statistic is computed."""
    if universe not in {"b1", "b2", "b3"}:
        raise ValueError("universe must be one of: b1, b2, b3")
    required = {"date", "code", "panel_scope", f"universe_{universe}", target}
    missing = required.difference(panel.columns)
    if missing:
        raise ValueError(
            "full-market factor selection requires columns: "
            f"{sorted(missing)}; pass the new panel and explicit target/universe"
        )
    if panel.duplicated(["date", "code"]).any():
        raise ValueError("full-market selection panel has duplicate (date, code) keys")
    scopes = set(panel["panel_scope"].dropna().astype(str).unique())
    if panel_scope != "full_market" or scopes != {"full_market"}:
        raise ValueError(
            "factor selection only accepts panel_scope='full_market'; "
            f"received requested={panel_scope!r}, values={sorted(scopes)}"
        )


def prepare_selection_panel(
    panel: pd.DataFrame,
    *,
    universe: str,
    target: str,
    factor_paths: Sequence[Path | str] = (),
    selection_end: str | pd.Timestamp | None = None,
) -> tuple[pd.DataFrame, pd.Timestamp, pd.Timestamp]:
    """Build the B-domain selection slice and attach external factor files."""
    validate_selection_contract(panel, universe=universe, target=target)
    work = panel.copy()
    work["date"] = pd.to_datetime(work["date"], errors="raise")
    work["code"] = work["code"].astype(str).str.zfill(6)
    base_selection, cutoff, evaluation_start = split_selection_panel(
        work, selection_end, label_gap_days=LABEL_GAP_TRADING_DAYS
    )
    mask = _as_bool_mask(base_selection[f"universe_{universe}"])
    selected = base_selection.loc[mask].copy()
    selected = _merge_factor_files(selected, factor_paths)
    gc.collect()
    return selected, cutoff, evaluation_start


def build_factor_coverage_diagnostics(
    panel: pd.DataFrame,
    columns: Iterable[str],
    *,
    target: str = TARGET,
) -> pd.DataFrame:
    """Report row coverage and daily cross-sectional distinctness by factor."""
    rows: list[dict[str, Any]] = []
    for factor in columns:
        if factor not in panel.columns:
            raise ValueError(f"factor column missing from selection panel: {factor}")
        daily_rows: list[dict[str, int]] = []
        for _, day in panel.groupby("date", sort=True):
            valid = day[[factor, target]].dropna()
            daily_rows.append(
                {
                    "nonnull": int(day[factor].notna().sum()),
                    "unique": int(day[factor].nunique(dropna=True)),
                    "target_unique": int(day[target].nunique(dropna=True)),
                    "eligible": int(
                        len(valid) >= 5
                        and valid[factor].nunique() >= 5
                        and valid[target].nunique() >= 2
                    ),
                }
            )
        daily = pd.DataFrame(daily_rows)
        coverage = float(panel[factor].notna().mean()) if len(panel) else 0.0
        nonnull_days = int((daily["nonnull"] > 0).sum()) if not daily.empty else 0
        eligible_days = int(daily["eligible"].sum()) if not daily.empty else 0
        median_unique = (
            float(daily["unique"].median()) if not daily.empty else 0.0
        )
        if int(panel[factor].notna().sum()) == 0:
            diagnosis = "all_missing"
        elif eligible_days == 0 and median_unique < 5:
            diagnosis = "low_cross_sectional_distinctness"
        elif eligible_days == 0:
            diagnosis = "insufficient_target_or_cross_section_sample"
        elif coverage < 0.5:
            diagnosis = "sparse_row_coverage"
        else:
            diagnosis = "adequate_for_daily_ic"
        rows.append(
            {
                "factor": factor,
                "panel_rows": int(len(panel)),
                "nonnull_rows": int(panel[factor].notna().sum()),
                "coverage": coverage,
                "panel_days": int(panel["date"].nunique()),
                "nonnull_days": nonnull_days,
                "ic_eligible_days": eligible_days,
                "median_daily_unique": median_unique,
                "max_daily_unique": int(daily["unique"].max()) if not daily.empty else 0,
                "median_daily_nonnull": (
                    float(daily["nonnull"].median()) if not daily.empty else 0.0
                ),
                "diagnosis": diagnosis,
            }
        )
    return pd.DataFrame(rows)


def apply_watch_only_policy(
    review: pd.DataFrame,
    watch_only_factors: Iterable[str] = WATCH_ONLY_FACTORS,
    *,
    recent_ic_threshold: float = WATCH_ONLY_RECENT_IC_THRESHOLD,
) -> pd.DataFrame:
    """Mark watch-only upgrades for human review without changing directions."""
    result = review.copy()
    watch = {str(factor) for factor in watch_only_factors}
    result["watch_only"] = result["factor"].astype(str).isin(watch)
    recent = pd.to_numeric(result["recent_ic_mean"], errors="coerce")
    historical = pd.to_numeric(result["historical_ic_mean"], errors="coerce")
    same_nonzero_sign = (
        recent.notna()
        & historical.notna()
        & recent.ne(0)
        & historical.ne(0)
        & (np.sign(recent) == np.sign(historical))
    )
    threshold_met = recent.abs().gt(float(recent_ic_threshold))
    upgrade = result["watch_only"] & same_nonzero_sign & threshold_met
    result["watch_only_same_sign"] = same_nonzero_sign
    result["watch_only_recent_abs_ic_gt_threshold"] = threshold_met
    result["watch_only_trigger_met"] = upgrade
    result["watch_only_upgrade_rule"] = WATCH_ONLY_UPGRADE_RULE
    result.loc[result["watch_only"] & ~upgrade, "review_status"] = "watch_only"
    result.loc[upgrade, "review_status"] = "pre_registered_candidate"
    # A watch-only factor remains out of the candidate list until its
    # pre-registered trigger fires.  Observation alone is not a current
    # direction decision for a human to make.
    result.loc[result["watch_only"], "manual_review_required"] = (
        result.loc[result["watch_only"], "manual_review_required"].fillna(False)
        | upgrade.loc[result["watch_only"]]
    )
    result["watch_only_upgrade_reason"] = np.where(
        upgrade,
        "recent/history same sign and recent abs(IC) exceeds threshold",
        "trigger not met; continue observation",
    )
    return result


def apply_static_selection_policy(
    ic_table: pd.DataFrame,
    diagnostics: pd.DataFrame,
    *,
    minimum_abs_ic: float = MIN_ABS_SELECTION_IC,
) -> pd.DataFrame:
    """Apply the pre-registered model-independent deletion rules."""
    required_ic = {"factor", "ic_mean"}
    required_diagnostics = {"factor", "diagnosis"}
    if missing := required_ic.difference(ic_table.columns):
        raise ValueError(f"IC table missing columns {sorted(missing)}")
    if missing := required_diagnostics.difference(diagnostics.columns):
        raise ValueError(f"diagnostics missing columns {sorted(missing)}")
    result = ic_table.merge(
        diagnostics[["factor", "diagnosis"]],
        on="factor",
        how="left",
        validate="one_to_one",
    )
    ic = pd.to_numeric(result["ic_mean"], errors="coerce")
    invalid_diagnosis = result["diagnosis"].isin(STATIC_INELIGIBLE_DIAGNOSES)
    insufficient_ic = ic.isna()
    weak_ic = ic.notna() & ic.abs().lt(float(minimum_abs_ic))
    result["static_selection_eligible"] = ~(
        invalid_diagnosis | insufficient_ic | weak_ic
    )
    result["static_exclusion_reason"] = np.select(
        [invalid_diagnosis, insufficient_ic, weak_ic],
        [
            "insufficient_cross_sectional_data",
            "insufficient_daily_ic_days",
            f"abs_ic_below_{minimum_abs_ic:g}",
        ],
        default="eligible",
    )
    result["static_minimum_abs_ic"] = float(minimum_abs_ic)
    return result


def apply_direction_selection_policy(
    ic_table: pd.DataFrame,
    direction_review: pd.DataFrame,
    *,
    legacy_conflicts: Iterable[str] = LEGACY_REVIEW_CONFLICT_FACTORS,
) -> pd.DataFrame:
    """Keep unresolved direction/legacy conflicts out of the candidate list."""
    review_columns = [
        "factor",
        "review_status",
        "manual_review_required",
        "watch_only",
        "recommended_direction",
    ]
    missing = set(review_columns).difference(direction_review.columns)
    if missing:
        raise ValueError(f"direction review missing columns {sorted(missing)}")
    result = ic_table.merge(
        direction_review[review_columns],
        on="factor",
        how="left",
        validate="one_to_one",
    )
    legacy = {str(factor) for factor in legacy_conflicts}
    result["legacy_review_conflict"] = result["factor"].astype(str).isin(legacy)
    status = result["review_status"].fillna("unknown")
    unresolved_status = status.isin(
        {"regime_sensitive", "unstable", "pre_registered_candidate"}
    )
    no_signal = status.eq("no_significant_signal")
    manual_direction = result["manual_review_required"].fillna(False)
    result["direction_adjudication_required"] = (
        result["legacy_review_conflict"] | unresolved_status | manual_direction
    ) & result["static_selection_eligible"] & ~no_signal
    result["direction_research_observation"] = (
        status.eq("regime_sensitive") & ~result["static_selection_eligible"]
    )
    result["candidate_eligible"] = (
        result["static_selection_eligible"]
        & status.eq("stable")
        & ~result["direction_adjudication_required"]
    )
    result["candidate_exclusion_reason"] = np.select(
        [
            ~result["static_selection_eligible"],
            no_signal,
            result["legacy_review_conflict"],
            status.eq("watch_only"),
            unresolved_status,
            manual_direction,
        ],
        [
            result["static_exclusion_reason"].astype(str),
            "no_significant_signal",
            "legacy_review_conflict_pending_adjudication",
            "watch_only_not_triggered",
            status.astype(str),
            "direction_mismatch_pending_adjudication",
        ],
        default="eligible",
    )
    return result


def build_direction_adjudication_queue(
    review: pd.DataFrame,
    policy: pd.DataFrame,
) -> pd.DataFrame:
    """Export unresolved current-B2 evidence for human adjudication."""
    merged = review.merge(
        policy[
            [
                "factor",
                "static_selection_eligible",
                "candidate_eligible",
                "candidate_exclusion_reason",
                "legacy_review_conflict",
                "direction_adjudication_required",
                "direction_research_observation",
            ]
        ],
        on="factor",
        how="left",
        validate="one_to_one",
    )
    queue = merged.loc[
        merged["direction_adjudication_required"]
        | merged["direction_research_observation"]
    ].copy()
    queue["review_scope"] = np.where(
        queue["direction_adjudication_required"],
        "manual_decision",
        "research_observation_not_candidate",
    )
    queue["suggested_action"] = np.select(
        [
            queue["legacy_review_conflict"],
            queue["direction_research_observation"],
            queue["review_status"].eq("regime_sensitive"),
            queue["review_status"].eq("unstable"),
        ],
        [
            "manual_recheck_legacy_conflict",
            "observe_regime_below_static_ic_floor",
            "manual_review_regime_direction",
            "manual_review_or_drop_unstable",
        ],
        default="manual_review_direction",
    )
    queue["suggested_direction"] = queue["recommended_direction"]
    queue.loc[
        queue["review_status"].eq("no_significant_signal"), "suggested_direction"
    ] = "none"
    return queue.sort_values(
        ["suggested_action", "factor"], kind="mergesort"
    ).reset_index(drop=True)


def run(
    panel_path: Path | str = DEFAULT_SELECTION_PANEL,
    out_dir: Path | str = DEFAULT_SELECTION_OUT_DIR,
    expression_path: Path | str = PROJECT_ROOT / "config" / "expressions_combined.json",
    *,
    selection_end: str | pd.Timestamp | None = None,
    universe: str = DEFAULT_SELECTION_UNIVERSE,
    target: str = DEFAULT_SELECTION_TARGET,
    factor_paths: Sequence[Path | str] = (),
    watch_only_factors: Iterable[str] = WATCH_ONLY_FACTORS,
) -> dict[str, Any]:
    watch_only_factors = tuple(str(factor) for factor in watch_only_factors)
    panel = _read_selection_panel(panel_path, universe=universe, target=target)
    selection_panel, selection_end_date, evaluation_start = prepare_selection_panel(
        panel,
        universe=universe,
        target=target,
        factor_paths=factor_paths,
        selection_end=selection_end,
    )
    columns = factor_columns(selection_panel)

    expression_map: dict[str, str] = {}
    direction_map: dict[str, str] = {}
    specs = json.loads(Path(expression_path).read_text(encoding="utf-8"))["expressions"]
    for spec in specs:
        expression_map[f"alpha_qg_{spec['id']}"] = spec["expression"].lower()
        direction_map[f"alpha_qg_{spec['id']}"] = str(spec.get("direction", "unknown")).lower()

    ic_table = build_ic_table(selection_panel, columns, expression_map, target=target)
    stability = build_ic_stability_table(selection_panel, columns, target=target)
    ic_table = ic_table.merge(stability, on="factor", how="left", validate="one_to_one")
    coverage = build_factor_coverage_diagnostics(
        selection_panel, columns, target=target
    )
    ic_table = apply_static_selection_policy(ic_table, coverage)
    direction_review = build_direction_review(
        selection_panel, columns, direction_map, target=target
    )
    direction_review = apply_watch_only_policy(direction_review, watch_only_factors)
    ic_table = apply_direction_selection_policy(ic_table, direction_review)
    corr = build_rank_correlation(selection_panel, columns)
    eligible_columns = ic_table.loc[
        ic_table["candidate_eligible"], "factor"
    ].tolist()
    eligible_corr = corr.loc[eligible_columns, eligible_columns]
    threshold, selected = choose_threshold(eligible_corr, ic_table)
    rolling_by_family = build_rolling_ic_by_family(selection_panel, columns, target=target)
    direction_adjudication = build_direction_adjudication_queue(
        direction_review, ic_table
    )
    industry_neutral_candidates = select_top_abs_ic_factors(
        ic_table.loc[ic_table["candidate_eligible"]],
        top_n=len(eligible_columns),
        minimum_abs_ic=0.02,
    )
    cross_seed_factors = select_top_abs_ic_factors(
        ic_table.loc[ic_table["candidate_eligible"]], top_n=5
    )

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    ic_table.to_csv(out_dir / "factor_ic_table.csv", index=False, encoding="utf-8-sig")
    corr.to_parquet(out_dir / "factor_rank_correlation.parquet")
    rolling_by_family.to_csv(out_dir / "rolling_ic_by_family.csv", index=False, encoding="utf-8-sig")
    direction_review.to_csv(out_dir / "direction_review.csv", index=False, encoding="utf-8-sig")
    direction_adjudication.to_csv(
        out_dir / "direction_adjudication_queue.csv", index=False, encoding="utf-8-sig"
    )
    coverage.to_csv(
        out_dir / "factor_coverage_diagnostics.csv", index=False, encoding="utf-8-sig"
    )
    augmentation_plan = {
        "classification": CLASSIFICATION,
        "panel_scope": "full_market",
        "universe": universe,
        "target": target,
        "selection_period_end": str(selection_end_date.date()),
        "label_gap_trading_days": LABEL_GAP_TRADING_DAYS,
        "cs_source_factors": selected,
        "industry_neutral_factors": industry_neutral_candidates,
        "industry_neutral_threshold_abs_ic": 0.02,
        "cross_seed_factors": cross_seed_factors,
        "cross_feature_count": len(cross_seed_factors) * (len(cross_seed_factors) - 1),
        "selection_only": True,
        "automatic_direction_flip": False,
        "regime_review_min_abs_ic": REGIME_REVIEW_MIN_ABS_IC,
        "direction_adjudication_required": True,
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

    static_eligible_count = int(ic_table["static_selection_eligible"].sum())
    candidate_eligible_count = len(eligible_columns)

    payload = {
        "classification": CLASSIFICATION,
        "panel_scope": "full_market",
        "universe": universe,
        "target": target,
        "panel_path": str(Path(panel_path).resolve()),
        "factor_input_files": [str(Path(path).resolve()) for path in factor_paths],
        "selection_period_start": str(selection_panel["date"].min().date()),
        "selection_period_end": str(selection_end_date.date()),
        "evaluation_period_start": str(evaluation_start.date()),
        "label_gap_trading_days": LABEL_GAP_TRADING_DAYS,
        "selected_features_file": "selected_features.json",
        "ic_ir_method": f"Newey-West long-run standard deviation (lag={IC_HAC_LAG})",
        "total_columns": len(columns),
        "static_eligible_count": static_eligible_count,
        "static_excluded_count": len(columns) - static_eligible_count,
        "static_minimum_abs_ic": MIN_ABS_SELECTION_IC,
        "static_exclusion_counts": {
            str(key): int(value)
            for key, value in ic_table["static_exclusion_reason"].value_counts().items()
        },
        "candidate_eligible_count": candidate_eligible_count,
        "candidate_exclusion_counts": {
            str(key): int(value)
            for key, value in ic_table["candidate_exclusion_reason"].value_counts().items()
        },
        "selected_columns": selected,
        "correlation_threshold": threshold,
        "kept_count": len(selected),
        "removed_count": len(columns) - len(selected),
        "recently_reversed_count": int(ic_table["recently_reversed"].fillna(False).sum()),
        "direction_review_file": "direction_review.csv",
        "direction_adjudication_queue_file": "direction_adjudication_queue.csv",
        "direction_adjudication_count": int(len(direction_adjudication)),
        "direction_adjudication_required": True,
        "regime_review_min_abs_ic": REGIME_REVIEW_MIN_ABS_IC,
        "legacy_review_conflict_factors": sorted(LEGACY_REVIEW_CONFLICT_FACTORS),
        "factor_coverage_diagnostics_file": "factor_coverage_diagnostics.csv",
        "feature_augmentation_plan_file": "feature_augmentation_plan.json",
        "watch_only_factors": sorted(str(factor) for factor in watch_only_factors),
        "watch_only_upgrade_rule": WATCH_ONLY_UPGRADE_RULE,
        "selection_rows": int(len(selection_panel)),
        "selection_unique_dates": int(selection_panel["date"].nunique()),
        "selection_unique_codes": int(selection_panel["code"].nunique()),
        "selection_universe_rows": int(
            _as_bool_mask(selection_panel[f"universe_{universe}"]).sum()
        ),
        "universe_version": (
            sorted(selection_panel["universe_version"].dropna().astype(str).unique())
            if "universe_version" in selection_panel.columns
            else []
        ),
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
    parser.add_argument("--panel", default=str(DEFAULT_SELECTION_PANEL))
    parser.add_argument("--out-dir", default=str(DEFAULT_SELECTION_OUT_DIR))
    parser.add_argument("--expressions", default=str(PROJECT_ROOT / "config" / "expressions_combined.json"))
    parser.add_argument("--selection-end", default=None)
    parser.add_argument("--universe", choices=("b1", "b2", "b3"), default=DEFAULT_SELECTION_UNIVERSE)
    parser.add_argument("--target", default=DEFAULT_SELECTION_TARGET)
    parser.add_argument(
        "--factor-files",
        default="",
        help="comma-separated Alpha101/GTJA191 parquet files to join by (date, code)",
    )
    parser.add_argument(
        "--watch-only",
        default=",".join(WATCH_ONLY_FACTORS),
        help="comma-separated factors governed by the watch-only upgrade rule",
    )
    args = parser.parse_args()
    factor_paths = tuple(
        Path(path.strip())
        for path in args.factor_files.split(",")
        if path.strip()
    )
    watch_only = tuple(
        factor.strip() for factor in args.watch_only.split(",") if factor.strip()
    )
    run(
        args.panel,
        args.out_dir,
        args.expressions,
        selection_end=args.selection_end,
        universe=args.universe,
        target=args.target,
        factor_paths=factor_paths,
        watch_only_factors=watch_only,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
