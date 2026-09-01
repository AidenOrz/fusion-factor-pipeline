"""Trading-calendar helpers for forward-label separation."""

from __future__ import annotations

import pandas as pd


def _trading_dates(dates: pd.Series | pd.DatetimeIndex) -> pd.DatetimeIndex:
    calendar = pd.DatetimeIndex(pd.to_datetime(dates).unique()).sort_values()
    if calendar.empty:
        raise ValueError("trading calendar is empty")
    return calendar


def first_label_safe_evaluation_date(
    dates: pd.Series | pd.DatetimeIndex,
    selection_end: pd.Timestamp | str,
    *,
    label_horizon_trading_days: int,
) -> pd.Timestamp | None:
    """Return the first date after a complete forward-label purge gap."""

    if label_horizon_trading_days < 1:
        raise ValueError("label_horizon_trading_days must be positive")
    calendar = _trading_dates(dates)
    cutoff = pd.Timestamp(selection_end)
    position = calendar.get_indexer([cutoff])[0]
    if position < 0:
        raise ValueError("selection end is absent from the trading calendar")
    first_evaluation_position = position + label_horizon_trading_days + 1
    if first_evaluation_position >= len(calendar):
        return None
    return calendar[first_evaluation_position]


def assert_selection_evaluation_label_gap(
    dates: pd.Series | pd.DatetimeIndex,
    selection_end: pd.Timestamp | str,
    evaluation_start: pd.Timestamp | str,
    *,
    label_horizon_trading_days: int,
) -> None:
    """Reject evaluation windows whose labels overlap the selection period."""

    calendar = _trading_dates(dates)
    expected = first_label_safe_evaluation_date(
        calendar,
        selection_end,
        label_horizon_trading_days=label_horizon_trading_days,
    )
    if expected is None:
        raise ValueError("trading calendar ends before a label-safe evaluation date")
    start = pd.Timestamp(evaluation_start)
    position = calendar.get_indexer([start])[0]
    if position < 0:
        raise ValueError("evaluation start is absent from the trading calendar")
    if start < expected:
        raise ValueError(
            "selection/evaluation label gap is too short: "
            f"requires at least {label_horizon_trading_days} intervening trading days "
            f"and evaluation start {expected.date()}, got {start.date()}"
        )
