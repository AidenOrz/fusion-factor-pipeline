"""Leakage-aware feature transformations for the fusion factor panel.

The helpers in this module operate only on information available on the same
trading date.  Target-derived choices (for example the list of factors used
for industry neutralisation or cross terms) must be supplied explicitly by a
selection-period report; this module never computes those choices from the
full panel.
"""

from __future__ import annotations

from itertools import combinations
from typing import Iterable

import numpy as np
import pandas as pd


def _require_columns(frame: pd.DataFrame, columns: Iterable[str], operation: str) -> None:
    missing = [column for column in columns if column not in frame.columns]
    if missing:
        raise ValueError(f"{operation}: missing columns {missing}")
    if "date" not in frame.columns:
        raise ValueError(f"{operation}: frame must contain a date column")


def add_cross_sectional_features(
    frame: pd.DataFrame,
    columns: Iterable[str],
    *,
    lower_quantile: float = 0.01,
    upper_quantile: float = 0.99,
) -> tuple[pd.DataFrame, list[str]]:
    """Add per-date rank, z-score, and winsorised versions of factors.

    Missing values remain missing.  A constant but valid cross-section gets a
    z-score of zero, while a cross-section with no valid values stays NaN.
    """
    if not 0 <= lower_quantile < upper_quantile <= 1:
        raise ValueError("winsor quantiles must satisfy 0 <= lower < upper <= 1")
    columns = list(dict.fromkeys(columns))
    _require_columns(frame, columns, "add_cross_sectional_features")
    result = frame.copy()
    added: list[str] = []
    grouped = result.groupby("date", sort=False)
    for column in columns:
        values = pd.to_numeric(result[column], errors="coerce")
        rank_name = f"{column}_cs_rank"
        z_name = f"{column}_cs_z"
        winsor_name = f"{column}_cs_winsor"
        result[rank_name] = grouped[column].rank(pct=True, method="average")

        def _zscore(series: pd.Series) -> pd.Series:
            numeric = pd.to_numeric(series, errors="coerce")
            valid = numeric.notna()
            if not valid.any():
                return pd.Series(np.nan, index=series.index, dtype=float)
            # Match pandas' conventional cross-sectional sample z-score.
            std = numeric.std(ddof=1)
            if not np.isfinite(std) or std == 0:
                out = pd.Series(np.nan, index=series.index, dtype=float)
                out.loc[valid] = 0.0
                return out
            return (numeric - numeric.mean()) / std

        result[z_name] = grouped[column].transform(_zscore)

        def _winsorize(series: pd.Series) -> pd.Series:
            numeric = pd.to_numeric(series, errors="coerce")
            valid = numeric.dropna()
            if valid.empty:
                return numeric
            low = valid.quantile(lower_quantile)
            high = valid.quantile(upper_quantile)
            return numeric.clip(lower=low, upper=high)

        result[winsor_name] = grouped[column].transform(_winsorize)
        added.extend([rank_name, z_name, winsor_name])
    return result, added


def add_industry_neutral_features(
    frame: pd.DataFrame,
    columns: Iterable[str],
    *,
    industry_column: str = "industry_code",
    min_observations: int = 5,
) -> tuple[pd.DataFrame, list[str]]:
    """Regress each selected factor on same-day industry dummies.

    Unknown industries (NaN or ``-1``) are deliberately left NaN rather than
    being treated as one synthetic industry.  The residual is assigned only
    to rows used in the regression, so the operation is deterministic and
    does not mutate the caller's frame.
    """
    columns = list(dict.fromkeys(columns))
    _require_columns(frame, [*columns, industry_column], "add_industry_neutral_features")
    result = frame.copy()
    added: list[str] = []
    industry_as_text = result[industry_column].astype(str)
    invalid_industry = result[industry_column].eq(-1) | industry_as_text.isin({"-1", "-1.0"})
    valid_industry = result[industry_column].notna() & ~invalid_industry

    for column in columns:
        output_name = f"{column}_ind_neutral"
        residual = pd.Series(np.nan, index=result.index, dtype=float)
        values = pd.to_numeric(result[column], errors="coerce")
        for _, indices in result.groupby("date", sort=False).groups.items():
            idx = pd.Index(indices)
            usable = idx[valid_industry.loc[idx] & values.loc[idx].notna()]
            if len(usable) < min_observations:
                continue
            industries = result.loc[usable, industry_column].astype(str)
            design = pd.get_dummies(industries, dtype=float)
            design.insert(0, "__intercept__", 1.0)
            x = design.to_numpy(dtype=float)
            y = values.loc[usable].to_numpy(dtype=float)
            try:
                beta, *_ = np.linalg.lstsq(x, y, rcond=None)
            except np.linalg.LinAlgError:
                continue
            residual.loc[usable] = y - x @ beta
        result[output_name] = residual
        added.append(output_name)
    return result, added


def add_pairwise_cross_features(
    frame: pd.DataFrame,
    columns: Iterable[str],
    *,
    include_division: bool = True,
) -> tuple[pd.DataFrame, list[str]]:
    """Add pairwise products and guarded ratios for an explicit factor list."""
    columns = list(dict.fromkeys(columns))
    _require_columns(frame, columns, "add_pairwise_cross_features")
    if len(columns) < 2:
        return frame.copy(), []
    result = frame.copy()
    added: list[str] = []
    for left, right in combinations(columns, 2):
        left_values = pd.to_numeric(result[left], errors="coerce")
        right_values = pd.to_numeric(result[right], errors="coerce")
        product_name = f"{left}_x_{right}"
        result[product_name] = (left_values * right_values).replace([np.inf, -np.inf], np.nan)
        added.append(product_name)
        if include_division:
            ratio_name = f"{left}_div_{right}"
            denominator = right_values.mask(right_values.abs().lt(np.finfo(float).eps))
            result[ratio_name] = (left_values / denominator).replace([np.inf, -np.inf], np.nan)
            added.append(ratio_name)
    return result, added
