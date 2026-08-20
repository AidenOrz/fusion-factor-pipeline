"""Forward cross-sectional labels for full-market ranking research."""

from __future__ import annotations

import numpy as np
import pandas as pd


def _forward_returns(frame: pd.DataFrame, horizon: int) -> pd.DataFrame:
    derived = [
        "entry_open",
        "entry_date",
        "exit_close",
        "exit_date",
        "_date_pos",
        "stock_ret20",
        "benchmark_ret20",
        "y_excess20",
    ]
    result = (
        frame.drop(columns=derived, errors="ignore")
        .copy()
        .sort_values(["code", "date"])
        .reset_index(drop=True)
    )
    benchmark = (
        result[["date", "benchmark_open", "benchmark_close"]]
        .drop_duplicates("date")
        .sort_values("date")
        .reset_index(drop=True)
    )
    market_dates = pd.DatetimeIndex(benchmark["date"].dropna().unique()).sort_values()
    date_to_position = pd.Series(
        np.arange(len(market_dates), dtype=float), index=market_dates
    )
    result["_date_pos"] = result["date"].map(date_to_position)

    def shifted_market_date(values: pd.Series, offset: int) -> pd.Series:
        positions = values.to_numpy(dtype=float) + float(offset)
        valid = np.isfinite(positions) & (positions >= 0) & (positions < len(market_dates))
        shifted = pd.Series(pd.NaT, index=values.index, dtype="datetime64[ns]")
        if valid.any():
            shifted.loc[valid] = market_dates.take(positions[valid].astype(int)).to_numpy()
        return shifted

    result["entry_date"] = shifted_market_date(result["_date_pos"], 1)
    result["exit_date"] = shifted_market_date(result["_date_pos"], horizon)

    prices = result[["code", "date", "open", "close"]].drop_duplicates(
        ["code", "date"], keep="last"
    )
    result = result.merge(
        prices[["code", "date", "open"]].rename(
            columns={"date": "entry_date", "open": "entry_open"}
        ),
        on=["code", "entry_date"],
        how="left",
        validate="many_to_one",
    )
    result = result.merge(
        prices[["code", "date", "close"]].rename(
            columns={"date": "exit_date", "close": "exit_close"}
        ),
        on=["code", "exit_date"],
        how="left",
        validate="many_to_one",
    )
    result["stock_ret20"] = result["exit_close"] / result["entry_open"] - 1.0
    benchmark["benchmark_ret20"] = (
        benchmark["benchmark_close"].shift(-horizon)
        / benchmark["benchmark_open"].shift(-1)
        - 1.0
    )
    result = result.merge(
        benchmark[["date", "benchmark_ret20"]],
        on="date",
        how="left",
        validate="many_to_one",
    )
    valid = (
        result["entry_open"].notna()
        & result["exit_close"].notna()
        & result["benchmark_ret20"].notna()
    )
    result["stock_ret20"] = result["stock_ret20"].where(valid)
    result["benchmark_ret20"] = result["benchmark_ret20"].where(valid)
    result["y_excess20"] = result["stock_ret20"] - result["benchmark_ret20"]
    return result.drop(columns="_date_pos").sort_values(["date", "code"]).reset_index(drop=True)


def _percentile_rank(values: pd.Series) -> pd.Series:
    return values.rank(method="average", pct=True)


def _neutral_residual(day: pd.DataFrame, target: str) -> pd.Series:
    """Fit target on intercept, industry dummies and log float cap."""
    required = {target, "industry", "float_mcap"}
    if not required.issubset(day.columns):
        return pd.Series(np.nan, index=day.index, dtype=float)
    valid = day[target].notna() & day["industry"].notna() & day["float_mcap"].gt(0)
    if valid.sum() < 10:
        return pd.Series(np.nan, index=day.index, dtype=float)
    work = day.loc[valid]
    industry = pd.get_dummies(work["industry"].astype(str), dtype=float)
    x = pd.concat(
        [
            pd.Series(1.0, index=work.index, name="intercept"),
            industry,
            np.log(work[["float_mcap"]].astype(float)).rename(columns={"float_mcap": "log_float_mcap"}),
        ],
        axis=1,
    )
    values = work[target].to_numpy(dtype=float)
    coefficients, *_ = np.linalg.lstsq(x.to_numpy(dtype=float), values, rcond=None)
    residual = pd.Series(np.nan, index=day.index, dtype=float)
    residual.loc[work.index] = values - x.to_numpy(dtype=float) @ coefficients
    return residual


def attach_cross_sectional_labels(
    frame: pd.DataFrame,
    *,
    horizon: int = 20,
    universe_columns: tuple[str, ...] = ("universe_b1", "universe_b2", "universe_b3"),
) -> pd.DataFrame:
    """Attach raw and industry/size-neutral percentile-rank labels per domain."""
    if horizon != 20:
        raise ValueError("current label schema is fixed to the 20-trading-day horizon")
    required = {"date", "code", "open", "close", "benchmark_open", "benchmark_close"}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"label frame missing columns: {sorted(missing)}")
    result = _forward_returns(frame, horizon)
    result["label_horizon"] = int(horizon)
    for universe in universe_columns:
        if universe not in result.columns:
            continue
        raw_name = universe.removeprefix("universe_")
        mask = result[universe].fillna(False)
        result[f"y_raw_rank20_{raw_name}"] = np.nan
        result[f"y_neutral_resid20_{raw_name}"] = np.nan
        result[f"y_neutral_rank20_{raw_name}"] = np.nan
        for date, indices in result.loc[mask].groupby("date", sort=False).groups.items():
            day = result.loc[indices]
            raw = _percentile_rank(day["y_excess20"])
            residual = _neutral_residual(day, "y_excess20")
            result.loc[indices, f"y_raw_rank20_{raw_name}"] = raw.to_numpy()
            result.loc[indices, f"y_neutral_resid20_{raw_name}"] = residual.to_numpy()
            result.loc[indices, f"y_neutral_rank20_{raw_name}"] = _percentile_rank(residual).to_numpy()
    return result.sort_values(["date", "code"]).reset_index(drop=True)
