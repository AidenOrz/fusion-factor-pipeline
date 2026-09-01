"""Preregistered economic acceptance for the frozen B2 C-track signal.

The module consumes frozen evaluation predictions and the full-market B2 price
panel.  It deliberately does not import or modify the label/model/ranking
implementations.  Missing bars are handled with the registered observable
price convention: entry/exit is deferred to the next available price and the
last available close is used for a final settlement when no later bar exists.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bridge.paths import configured_upstream_path, upstream_path, validate_startup_or_exit
from bridge.temporal import assert_selection_evaluation_label_gap


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PANEL = PROJECT_ROOT / "data" / "panel_fusion_b2_adjudicated_2026-08-27.parquet"
DEFAULT_FROZEN = PROJECT_ROOT / "config" / "factor_pool_b2_frozen_2026-08-28.json"
DEFAULT_NEUTRAL_PREDICTIONS = (
    PROJECT_ROOT
    / "runs"
    / "factor_evaluation_v1_b2_frozen_2026-08-28"
    / "huber_b2_neutral_rank"
    / "predictions.parquet"
)
DEFAULT_RAW_PREDICTIONS = (
    PROJECT_ROOT
    / "runs"
    / "factor_evaluation_v1_b2_frozen_2026-08-28"
    / "huber_b2_raw_rank"
    / "predictions.parquet"
)
DEFAULT_OUT = PROJECT_ROOT / "runs" / "c_track_acceptance_v1_b2_frozen_2026-08-28"
DEFAULT_C1000 = configured_upstream_path("kline", "000852.csv")
DEFAULT_C2000 = configured_upstream_path("kline", "000915.csv")
DEFAULT_SSE = configured_upstream_path("kline", "_bench_000001SH.csv")

HORIZON = 20
COHORT_DAYS = 20
TRADING_DAYS_PER_YEAR = 252
COST_MULTIPLIERS = (0.5, 1.0, 2.0, 3.0)
SIDE_FEE = 0.001
SIDE_SLIPPAGE = 0.001
BASE_SIDE_COST = SIDE_FEE + SIDE_SLIPPAGE
PERIODS = {
    "2024Q4": (pd.Timestamp("2024-10-01"), pd.Timestamp("2024-12-31")),
    "2025H1": (pd.Timestamp("2025-01-01"), pd.Timestamp("2025-06-30")),
    "2025H2": (pd.Timestamp("2025-07-01"), pd.Timestamp("2025-12-31")),
}


def _path(value: str | Path) -> Path:
    candidate = Path(value)
    return candidate if candidate.is_absolute() else PROJECT_ROOT / candidate


def _date_range(value: str) -> tuple[pd.Timestamp, pd.Timestamp]:
    parts = str(value).split("..")
    if len(parts) != 2:
        raise ValueError(f"invalid date range: {value!r}")
    start, end = map(pd.Timestamp, parts)
    if start > end:
        raise ValueError("date range start must not exceed end")
    return start, end


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _normalise_keys(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    result["date"] = pd.to_datetime(result["date"], errors="raise")
    raw_code = result["code"].astype(str).str.strip()
    extracted = raw_code.str.extract(r"(\d{6})", expand=False)
    numeric = raw_code.str.replace(r"\.0$", "", regex=True)
    numeric = numeric.where(numeric.str.fullmatch(r"\d+"), extracted)
    result["code"] = numeric.where(numeric.isna(), numeric.str.zfill(6))
    if result["code"].isna().any() or result["code"].eq("").any():
        raise ValueError("date/code input contains an invalid stock code")
    return result


def _require_columns(frame: pd.DataFrame, required: Iterable[str], label: str) -> None:
    missing = sorted(set(required).difference(frame.columns))
    if missing:
        raise ValueError(f"{label} missing columns: {missing}")


def load_frozen_contract(path: str | Path = DEFAULT_FROZEN) -> dict[str, Any]:
    """Validate and return the already frozen C-track contract."""

    frozen = json.loads(_path(path).read_text(encoding="utf-8"))
    if frozen.get("status") != "frozen_for_one_shot_evaluation":
        raise ValueError("frozen config is not marked for one-shot evaluation")
    if frozen.get("panel_scope") != "full_market" or frozen.get("universe") != "b2":
        raise ValueError("C-track requires the full-market B2 frozen contract")
    if frozen.get("automatic_direction_flip") is not False:
        raise ValueError("frozen config must prohibit automatic direction flips")
    track = frozen.get("acceptance_track", {})
    if track.get("primary") != "C_bottom_exclusion":
        raise ValueError("frozen contract is not registered for the C-track")
    if not math.isclose(float(track.get("bottom_exclusion_fraction", -1)), 0.10):
        raise ValueError("C-track bottom exclusion must be 10 percent")
    models = {item.get("id"): item for item in frozen.get("models", []) if isinstance(item, dict)}
    neutral = models.get("huber_b2_neutral_rank")
    if not neutral or neutral.get("target") != "y_neutral_rank20_b2":
        raise ValueError("frozen contract does not register the neutral Huber scorer")
    selection_start, selection_end = _date_range(frozen["selection_period"])
    evaluation_start, evaluation_end = _date_range(frozen["evaluation_period"])
    if int(frozen.get("label_horizon_trading_days", 0)) != HORIZON:
        raise ValueError("frozen label horizon must match the C-track holding horizon")
    if evaluation_start <= selection_end:
        raise ValueError("frozen evaluation range is incompatible with a 20-day horizon")
    frozen["_selection_start"] = selection_start
    frozen["_selection_end"] = selection_end
    frozen["_evaluation_start"] = evaluation_start
    frozen["_evaluation_end"] = evaluation_end
    return frozen


def load_inputs(
    *,
    panel_path: str | Path = DEFAULT_PANEL,
    neutral_predictions_path: str | Path = DEFAULT_NEUTRAL_PREDICTIONS,
    raw_predictions_path: str | Path | None = DEFAULT_RAW_PREDICTIONS,
    frozen_path: str | Path = DEFAULT_FROZEN,
) -> dict[str, Any]:
    """Load only the columns needed by the economic acceptance."""

    frozen = load_frozen_contract(frozen_path)
    panel_columns = [
        "date", "code", "open", "close", "volume", "amount", "is_st",
        "universe_b2", "y_excess20", "float_mcap", "adv20",
    ]
    panel = pd.read_parquet(_path(panel_path), columns=panel_columns)
    _require_columns(panel, panel_columns, "panel")
    panel = _normalise_keys(panel)
    if panel.duplicated(["date", "code"]).any():
        raise ValueError("panel contains duplicate (date, code) keys")
    if not panel["universe_b2"].isin([True, False]).all():
        raise ValueError("panel universe_b2 must be boolean")
    panel["open"] = pd.to_numeric(panel["open"], errors="coerce")
    panel["close"] = pd.to_numeric(panel["close"], errors="coerce")
    panel["adv20"] = pd.to_numeric(panel["adv20"], errors="coerce")
    panel = panel.sort_values(["date", "code"], kind="mergesort").reset_index(drop=True)
    assert_selection_evaluation_label_gap(
        panel["date"],
        frozen["_selection_end"],
        frozen["_evaluation_start"],
        label_horizon_trading_days=HORIZON,
    )

    def read_predictions(path: str | Path, expected_model: str) -> pd.DataFrame:
        prediction = pd.read_parquet(_path(path))
        _require_columns(prediction, ["date", "code", "prediction"], f"{expected_model} predictions")
        prediction = _normalise_keys(prediction)
        if prediction.duplicated(["date", "code"]).any():
            raise ValueError(f"{expected_model} predictions contain duplicate keys")
        prediction["prediction"] = pd.to_numeric(prediction["prediction"], errors="coerce")
        prediction = prediction.dropna(subset=["prediction"])
        if prediction.empty:
            raise ValueError(f"{expected_model} predictions are empty")
        return prediction.sort_values(["date", "code"], kind="mergesort").reset_index(drop=True)

    neutral = read_predictions(neutral_predictions_path, "neutral")
    raw = read_predictions(raw_predictions_path, "raw") if raw_predictions_path is not None else None
    eval_start, eval_end = frozen["_evaluation_start"], frozen["_evaluation_end"]
    neutral = neutral.loc[neutral["date"].between(eval_start, eval_end)].copy()
    if raw is not None:
        raw = raw.loc[raw["date"].between(eval_start, eval_end)].copy()
    if neutral.empty:
        raise ValueError("neutral predictions have no rows in the registered evaluation range")
    known_keys = panel.set_index(["date", "code"])[["universe_b2"]]
    checked = neutral.set_index(["date", "code"]).join(known_keys, how="left")
    if checked["universe_b2"].isna().any():
        raise ValueError("neutral predictions contain keys absent from the panel")
    if not checked["universe_b2"].astype(bool).all():
        raise ValueError("neutral predictions contain rows outside B2")
    return {
        "frozen": frozen,
        "panel": panel,
        "neutral_predictions": neutral,
        "raw_predictions": raw,
        "panel_path": str(_path(panel_path).resolve()),
        "neutral_predictions_path": str(_path(neutral_predictions_path).resolve()),
        "raw_predictions_path": str(_path(raw_predictions_path).resolve()) if raw_predictions_path else None,
    }


def select_bottom_exclusion(
    predictions: pd.DataFrame,
    panel: pd.DataFrame,
    *,
    exclusion_fraction: float = 0.10,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return selected rows and the excluded bottom tail by signal date."""

    _require_columns(predictions, ["date", "code", "prediction"], "predictions")
    _require_columns(panel, ["date", "code", "universe_b2"], "panel")
    prediction_keys = _normalise_keys(predictions)[["date", "code", "prediction"]]
    frame = prediction_keys.merge(
        panel[["date", "code", "universe_b2", "y_excess20", "adv20", "float_mcap"]],
        on=["date", "code"], how="left", validate="one_to_one", suffixes=("", "_panel"),
    )
    frame = frame.loc[frame["universe_b2"].fillna(False)].copy()
    if frame.empty:
        raise ValueError("no B2 rows remain after joining predictions")
    return _partition_bottom_exclusion(frame, exclusion_fraction=exclusion_fraction)


def _partition_bottom_exclusion(
    frame: pd.DataFrame,
    *,
    exclusion_fraction: float,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Apply the registered C-track bottom-tail rule to an already joined frame."""

    if not 0 < exclusion_fraction < 1:
        raise ValueError("exclusion_fraction must be between zero and one")
    selected_parts: list[pd.DataFrame] = []
    excluded_parts: list[pd.DataFrame] = []
    for date, day in frame.groupby("date", sort=True):
        day = day.sort_values(["prediction", "code"], ascending=[True, True], kind="mergesort").copy()
        count = max(1, int(math.ceil(len(day) * exclusion_fraction)))
        excluded = day.iloc[:count].copy()
        selected = day.iloc[count:].copy()
        if selected.empty:
            raise ValueError(f"bottom exclusion removes the complete universe on {date.date()}")
        excluded["signal_role"] = "excluded_bottom"
        selected["signal_role"] = "retained"
        selected["cohort_size"] = int(len(selected))
        selected_parts.append(selected)
        excluded_parts.append(excluded)
    selected = pd.concat(selected_parts, ignore_index=True).sort_values(["date", "code"], kind="mergesort")
    excluded = pd.concat(excluded_parts, ignore_index=True).sort_values(["date", "code"], kind="mergesort")
    return selected.reset_index(drop=True), excluded.reset_index(drop=True)


def _calendar_positions(panel: pd.DataFrame) -> tuple[pd.DatetimeIndex, dict[pd.Timestamp, int]]:
    dates = pd.DatetimeIndex(pd.to_datetime(panel["date"].drop_duplicates()).sort_values())
    return dates, {date: index for index, date in enumerate(dates)}


def resolve_trade_window(
    selected: pd.DataFrame,
    panel: pd.DataFrame,
    *,
    market_dates: pd.DatetimeIndex | None = None,
    horizon: int = HORIZON,
    evaluation_end: pd.Timestamp | None = None,
) -> pd.DataFrame:
    """Resolve planned T+1/T+20 windows against observable panel prices."""

    _require_columns(selected, ["date", "code"], "selected rows")
    _require_columns(panel, ["date", "code", "open", "close"], "price panel")
    if horizon < 1:
        raise ValueError("horizon must be positive")
    panel = _normalise_keys(panel).sort_values(["code", "date"], kind="mergesort")
    if market_dates is None:
        market_dates, positions = _calendar_positions(panel)
    else:
        market_dates = pd.DatetimeIndex(pd.to_datetime(market_dates)).sort_values()
        positions = {date: index for index, date in enumerate(market_dates)}
    if evaluation_end is None:
        evaluation_end = market_dates.max()
    evaluation_end = pd.Timestamp(evaluation_end)
    selected_frame = _normalise_keys(selected).reset_index(drop=True)
    price_frame = _normalise_keys(panel)[["code", "date", "open", "close"]].copy()
    price_frame["_market_pos"] = price_frame["date"].map(positions)
    price_frame = price_frame.loc[
        price_frame["_market_pos"].notna() & price_frame["date"].le(evaluation_end)
    ].copy()

    result = pd.DataFrame(
        {
            "signal_date": selected_frame["date"],
            "code": selected_frame["code"],
            "planned_entry_date": pd.NaT,
            "planned_exit_date": pd.NaT,
            "entry_date": pd.NaT,
            "exit_date": pd.NaT,
            "entry_open": np.nan,
            "exit_close": np.nan,
            "entry_status": "unresolved",
            "exit_status": "unresolved",
        }
    )
    for metadata in ("cohort_size", "adv20", "float_mcap", "signal_role"):
        if metadata in selected_frame.columns:
            result[metadata] = selected_frame[metadata].to_numpy()

    signal_positions = selected_frame["date"].map(positions)
    valid = signal_positions.notna() & signal_positions.add(horizon).lt(len(market_dates))
    result.loc[~valid, "entry_status"] = "outside_calendar"
    valid_index = result.index[valid]
    if len(valid_index) == 0:
        return result.sort_values(["signal_date", "code"], kind="mergesort").reset_index(drop=True)

    signal_pos = signal_positions.loc[valid].astype(int)
    result.loc[valid, "planned_entry_date"] = [market_dates[pos + 1] for pos in signal_pos]
    result.loc[valid, "planned_exit_date"] = [market_dates[pos + horizon] for pos in signal_pos]
    result["_planned_entry_pos"] = -1
    result["_planned_exit_pos"] = -1
    result.loc[valid, "_planned_entry_pos"] = signal_pos.to_numpy() + 1
    result.loc[valid, "_planned_exit_pos"] = signal_pos.to_numpy() + horizon

    # merge_asof performs the next-observable-price lookup in one pass when both
    # sides are sorted by the calendar position.  This replaces a per-trade
    # DataFrame slice/copy and preserves exact-match and deferred-bar semantics.
    open_prices = price_frame.loc[price_frame["open"].gt(0), ["code", "_market_pos", "date", "open"]]
    entry_left = result.loc[valid, ["code", "_planned_entry_pos"]].copy()
    entry_left["_row"] = entry_left.index
    entry_left = entry_left.rename(columns={"_planned_entry_pos": "_target_pos"})
    entry = pd.merge_asof(
        entry_left.sort_values(["_target_pos", "code"], kind="mergesort"),
        open_prices.rename(columns={"_market_pos": "_entry_pos", "date": "_entry_date", "open": "_entry_open"})
        .sort_values(["_entry_pos", "code"], kind="mergesort"),
        left_on="_target_pos", right_on="_entry_pos", by="code", direction="forward",
        allow_exact_matches=True,
    ).set_index("_row")
    entry = entry.loc[entry["_entry_date"].le(evaluation_end) | entry["_entry_date"].isna()]
    entry_ok = entry["_entry_date"].notna()
    result.loc[entry.index[entry_ok], "entry_date"] = entry.loc[entry_ok, "_entry_date"]
    result.loc[entry.index[entry_ok], "entry_open"] = entry.loc[entry_ok, "_entry_open"].astype(float)
    result.loc[entry.index[entry_ok], "entry_status"] = np.where(
        entry.loc[entry_ok, "_entry_pos"].eq(entry.loc[entry_ok, "_target_pos"]),
        "planned", "delayed_missing_bar",
    )
    result.loc[entry.index[~entry_ok], "entry_status"] = "no_entry_price"

    executable = entry.index[entry_ok]
    if len(executable):
        close_prices = price_frame.loc[
            price_frame["close"].gt(0), ["code", "_market_pos", "date", "close"]
        ].rename(columns={"_market_pos": "_exit_pos", "date": "_exit_date", "close": "_exit_close"})
        exit_left = result.loc[executable, ["code", "_planned_exit_pos"]].copy()
        exit_left["_entry_pos"] = entry.loc[executable, "_entry_pos"]
        exit_left["_row"] = exit_left.index
        exit_left = exit_left.rename(columns={"_planned_exit_pos": "_target_pos"})
        exit_forward = pd.merge_asof(
            exit_left.sort_values(["_target_pos", "code"], kind="mergesort"),
            close_prices.sort_values(["_exit_pos", "code"], kind="mergesort"),
            left_on="_target_pos", right_on="_exit_pos", by="code", direction="forward",
            allow_exact_matches=True,
        ).set_index("_row")
        exit_forward = exit_forward.loc[
            exit_forward["_exit_date"].le(evaluation_end) | exit_forward["_exit_date"].isna()
        ]
        exit_ok = exit_forward["_exit_date"].notna()

        # If no close exists on/after the planned exit, settle at the last
        # observable close after entry, matching the registered convention.
        last_close = (
            close_prices.sort_values(["code", "_exit_pos"], kind="mergesort")
            .groupby("code", sort=False, as_index=False)
            .tail(1)
            .set_index("code")
        )
        fallback = exit_forward.loc[~exit_ok, ["code", "_entry_pos"]].join(
            last_close[["_exit_pos", "_exit_date", "_exit_close"]], on="code"
        )
        fallback_ok = fallback["_exit_date"].notna() & fallback["_exit_pos"].ge(fallback["_entry_pos"])
        for idx in fallback.index[fallback_ok]:
            exit_forward.loc[idx, ["_exit_pos", "_exit_date", "_exit_close"]] = fallback.loc[
                idx, ["_exit_pos", "_exit_date", "_exit_close"]
            ]
        exit_ok = exit_forward["_exit_date"].notna()
        result.loc[exit_forward.index[exit_ok], "exit_date"] = exit_forward.loc[exit_ok, "_exit_date"]
        result.loc[exit_forward.index[exit_ok], "exit_close"] = exit_forward.loc[exit_ok, "_exit_close"].astype(float)
        result.loc[exit_forward.index[exit_ok], "exit_status"] = np.select(
            [
                exit_forward.loc[exit_ok, "_exit_pos"].eq(exit_forward.loc[exit_ok, "_target_pos"]),
                exit_forward.loc[exit_ok, "_exit_pos"].gt(exit_forward.loc[exit_ok, "_target_pos"]),
            ],
            ["planned", "delayed_missing_bar"], default="last_available_price",
        )
        result.loc[exit_forward.index[~exit_ok], "exit_status"] = "no_exit_price"
        entry_after_exit = exit_ok & exit_forward["_exit_date"].lt(result.loc[exit_forward.index, "entry_date"])
        after_exit_index = exit_forward.index[entry_after_exit]
        result.loc[after_exit_index, "entry_status"] = "entry_after_exit"
        result.loc[after_exit_index, "exit_status"] = "entry_after_exit"
        result.loc[after_exit_index, "exit_date"] = pd.NaT
        result.loc[after_exit_index, "exit_close"] = np.nan
    if result.empty:
        raise ValueError("trade window resolution produced no rows")
    return result.sort_values(["signal_date", "code"], kind="mergesort").reset_index(drop=True)


def _side_cost(multiplier: float) -> float:
    if multiplier < 0:
        raise ValueError("cost multiplier must be non-negative")
    return BASE_SIDE_COST * float(multiplier)


def _annualized_return(equity: pd.Series) -> float | None:
    clean = pd.to_numeric(equity, errors="coerce").dropna()
    if len(clean) < 2 or clean.iloc[0] <= 0 or clean.iloc[-1] <= 0:
        return None
    return float((clean.iloc[-1] / clean.iloc[0]) ** (TRADING_DAYS_PER_YEAR / (len(clean) - 1)) - 1.0)


def simulate_cohort_portfolio(
    trades: pd.DataFrame,
    panel: pd.DataFrame,
    *,
    market_dates: pd.DatetimeIndex | None = None,
    cost_multiplier: float = 1.0,
    cohort_days: int = COHORT_DAYS,
) -> dict[str, Any]:
    """Simulate overlapping equal-capital cohorts with daily mark-to-market."""

    _require_columns(
        trades,
        ["signal_date", "code", "entry_date", "exit_date", "entry_open", "exit_close"],
        "resolved trades",
    )
    _require_columns(panel, ["date", "code", "close"], "price panel")
    if cohort_days < 1:
        raise ValueError("cohort_days must be positive")
    cost = _side_cost(cost_multiplier)
    frame = trades.copy()
    frame["signal_date"] = pd.to_datetime(frame["signal_date"], errors="raise")
    frame["entry_date"] = pd.to_datetime(frame["entry_date"], errors="coerce")
    frame["exit_date"] = pd.to_datetime(frame["exit_date"], errors="coerce")
    frame = frame.dropna(subset=["entry_date", "exit_date", "entry_open", "exit_close"]).copy()
    if frame.empty:
        raise ValueError("no executable trades remain")
    if "cohort_size" in frame.columns:
        counts = pd.to_numeric(frame["cohort_size"], errors="coerce")
        if counts.isna().any() or counts.le(0).any():
            raise ValueError("cohort_size must be positive")
    else:
        counts = frame.groupby("signal_date")["code"].transform("size")
    frame["cohort_notional"] = 1.0 / float(cohort_days)
    frame["unit_notional"] = frame["cohort_notional"] / counts
    frame["units"] = frame["unit_notional"] / (frame["entry_open"].astype(float) * (1.0 + cost))
    frame["buy_cash"] = frame["unit_notional"] * (1.0 + cost)
    frame["sell_cash"] = frame["units"] * frame["exit_close"].astype(float) * (1.0 - cost)

    panel = _normalise_keys(panel)
    if market_dates is None:
        dates, _ = _calendar_positions(panel)
    else:
        dates = pd.DatetimeIndex(pd.to_datetime(market_dates)).sort_values()
    start = frame["entry_date"].min()
    end = max(frame["exit_date"].max(), frame["entry_date"].max())
    dates = dates[(dates >= start) & (dates <= end)]
    if dates.empty:
        raise ValueError("trade dates do not overlap the price calendar")
    close_pivot = panel.pivot_table(index="date", columns="code", values="close", aggfunc="last").reindex(dates)
    entries = {date: group for date, group in frame.groupby("entry_date", sort=False)}
    exits = {date: group for date, group in frame.groupby("exit_date", sort=False)}
    holdings: dict[str, float] = defaultdict(float)
    last_close: dict[str, float] = {}
    cash = 1.0
    rows: list[dict[str, Any]] = []
    buy_by_date: dict[pd.Timestamp, float] = {}
    sell_by_date: dict[pd.Timestamp, float] = {}
    for date in dates:
        entering = entries.get(date)
        if entering is not None:
            for row in entering.itertuples(index=False):
                holdings[row.code] += float(row.units)
                cash -= float(row.buy_cash)
                buy_by_date[date] = buy_by_date.get(date, 0.0) + float(row.unit_notional)
                last_close.setdefault(row.code, float(row.entry_open))
        prices = close_pivot.loc[date] if date in close_pivot.index else pd.Series(dtype=float)
        for code in list(holdings):
            if holdings[code] == 0:
                continue
            value = prices.get(code, np.nan)
            if pd.notna(value) and float(value) > 0:
                last_close[code] = float(value)
        exiting = exits.get(date)
        if exiting is not None:
            for row in exiting.itertuples(index=False):
                proceeds = float(row.sell_cash)
                cash += proceeds
                holdings[row.code] -= float(row.units)
                sell_by_date[date] = sell_by_date.get(date, 0.0) + float(row.units * row.exit_close)
                if abs(holdings[row.code]) < 1e-12:
                    del holdings[row.code]
        marked = sum(holdings[code] * last_close.get(code, 0.0) for code in holdings)
        equity = cash + marked
        rows.append({
            "date": date,
            "equity": float(equity),
            "cash": float(cash),
            "marked_holdings": float(marked),
            "active_positions": int(len(holdings)),
            "buy_notional": float(buy_by_date.get(date, 0.0)),
            "sell_notional": float(sell_by_date.get(date, 0.0)),
        })
    daily = pd.DataFrame(rows).sort_values("date").reset_index(drop=True)
    daily["daily_return"] = daily["equity"].pct_change().fillna(0.0)
    prior_equity = daily["equity"].shift(1).fillna(daily["equity"].iloc[0])
    daily["two_sided_turnover"] = (daily["buy_notional"] + daily["sell_notional"]) / (2.0 * prior_equity)
    return {
        "daily": daily,
        "trades": frame,
        "cost_multiplier": float(cost_multiplier),
        "side_cost": float(cost),
        "annualized_return": _annualized_return(daily["equity"]),
        "trade_count": int(len(frame)),
        "unresolved_count": int(len(trades) - len(frame)),
        "mean_daily_two_sided_turnover": float(daily["two_sided_turnover"].mean()),
        "total_buy_notional": float(daily["buy_notional"].sum()),
        "total_sell_notional": float(daily["sell_notional"].sum()),
    }


def _rebase_equity(series: pd.Series) -> pd.Series:
    clean = pd.to_numeric(series, errors="coerce")
    first = clean.dropna().iloc[0]
    return clean / first


def _curve_metrics(strategy: pd.DataFrame, benchmark: pd.DataFrame) -> dict[str, Any]:
    joined = strategy[["date", "equity"]].merge(
        benchmark[["date", "equity"]], on="date", how="inner", suffixes=("_strategy", "_benchmark")
    )
    if len(joined) < 2:
        raise ValueError("strategy and benchmark do not have two common dates")
    joined["strategy_equity"] = _rebase_equity(joined["equity_strategy"])
    joined["benchmark_equity"] = _rebase_equity(joined["equity_benchmark"])
    strategy_return = joined["strategy_equity"].iloc[-1] - 1.0
    benchmark_return = joined["benchmark_equity"].iloc[-1] - 1.0
    strategy_ann = _annualized_return(joined["strategy_equity"])
    benchmark_ann = _annualized_return(joined["benchmark_equity"])
    relative = joined["strategy_equity"] / joined["benchmark_equity"]
    relative_drawdown = 1.0 - relative / relative.cummax()
    return {
        "dates": int(len(joined)),
        "start": str(joined["date"].min().date()),
        "end": str(joined["date"].max().date()),
        "strategy_return": float(strategy_return),
        "benchmark_return": float(benchmark_return),
        "excess_return": float(strategy_return - benchmark_return),
        "strategy_annualized_return": strategy_ann,
        "benchmark_annualized_return": benchmark_ann,
        "annualized_excess": (
            float(strategy_ann - benchmark_ann)
            if strategy_ann is not None and benchmark_ann is not None else None
        ),
        "relative_max_drawdown": float(relative_drawdown.max()),
        "relative_curve": joined[["date", "strategy_equity", "benchmark_equity"]],
    }


def _index_equity(index: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    _require_columns(index, ["date", "open", "close"], "index")
    frame = index.copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="raise")
    frame["open"] = pd.to_numeric(frame["open"], errors="coerce")
    frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
    frame = frame.loc[frame["date"].between(start, end)].sort_values("date")
    frame = frame.drop_duplicates("date", keep="last").dropna(subset=["open", "close"])
    if len(frame) < 2:
        raise ValueError("index has fewer than two usable dates in the acceptance range")
    frame["daily_return"] = frame["close"] / frame["close"].shift(1) - 1.0
    frame.iloc[0, frame.columns.get_loc("daily_return")] = frame.iloc[0]["close"] / frame.iloc[0]["open"] - 1.0
    frame["equity"] = (1.0 + frame["daily_return"]).cumprod()
    return frame[["date", "equity", "daily_return"]]


def compute_index_benchmarks(
    index_paths: dict[str, str | Path],
    *,
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> dict[str, pd.DataFrame]:
    """Load passive index curves for the opportunity-cost layer."""

    curves: dict[str, pd.DataFrame] = {}
    for name, path in index_paths.items():
        index = pd.read_csv(_path(path), encoding="utf-8-sig")
        curves[name] = _index_equity(index, start, end)
    available = [curves[name].set_index("date")["equity"].rename(name) for name in ("csi1000", "csi2000")]
    mix = pd.concat(available, axis=1).dropna()
    if len(mix) < 2:
        raise ValueError("CSI 1000/2000 do not overlap for the mixed benchmark")
    mix_equity = (mix / mix.iloc[0]).mean(axis=1)
    mix_returns = mix_equity.pct_change().fillna(mix_equity.iloc[0] - 1.0)
    curves["csi_mix_50_50"] = pd.DataFrame({"date": mix.index, "equity": mix_equity.values, "daily_return": mix_returns.values})
    return curves


def _period_rows(strategy: pd.DataFrame, benchmark: pd.DataFrame) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for name, (start, end) in PERIODS.items():
        subset_s = strategy.loc[strategy["date"].between(start, end)]
        subset_b = benchmark.loc[benchmark["date"].between(start, end)]
        if len(subset_s) < 2 or len(subset_b) < 2:
            rows.append({"period": name, "dates": 0, "excess_return": None, "annualized_excess": None})
            continue
        metrics = _curve_metrics(subset_s, subset_b)
        rows.append({"period": name, "dates": metrics["dates"], "excess_return": metrics["excess_return"], "annualized_excess": metrics["annualized_excess"]})
    return rows


def _excluded_metrics(excluded: pd.DataFrame) -> dict[str, Any]:
    if excluded.empty or "y_excess20" not in excluded:
        return {"rows": 0, "days": 0, "mean_y_excess20": None, "daily_mean": []}
    daily = excluded.groupby("date", sort=True)["y_excess20"].mean().dropna()
    return {
        "rows": int(len(excluded)),
        "days": int(len(daily)),
        "mean_y_excess20": float(daily.mean()) if len(daily) else None,
        "daily_mean": [{"date": str(index.date()), "mean_y_excess20": float(value)} for index, value in daily.items()],
    }


def _membership_change_rate(excluded: pd.DataFrame) -> dict[str, Any]:
    memberships = {date: set(group["code"]) for date, group in excluded.groupby("date", sort=True)}
    values: list[float] = []
    previous: set[str] | None = None
    for current in memberships.values():
        if previous is not None and current:
            values.append(1.0 - len(previous & current) / len(current))
        previous = current
    return {"mean_daily_change_rate": float(np.mean(values)) if values else None, "observations": len(values)}


def _capacity_metrics(trades: pd.DataFrame) -> dict[str, Any]:
    if trades.empty or "adv20" not in trades:
        return {"participation": {}}
    frame = trades.copy()
    frame["adv20"] = pd.to_numeric(frame["adv20"], errors="coerce")
    frame = frame.loc[frame["adv20"].gt(0) & frame["unit_notional"].gt(0)]
    output: dict[str, Any] = {}
    for participation in (0.01, 0.05):
        capacity = participation * frame["adv20"] / frame["unit_notional"]
        output[f"{participation:.0%}"] = {
            "min_capital": float(capacity.min()) if len(capacity) else None,
            "p10_capital": float(capacity.quantile(0.10)) if len(capacity) else None,
            "median_capital": float(capacity.median()) if len(capacity) else None,
            "participation_rate": participation,
            "classification": "descriptive_only",
        }
    return {"participation": output}


def compute_acceptance_metrics(
    strategy_by_cost: dict[float, dict[str, Any]],
    benchmark_by_cost: dict[float, dict[str, Any]],
    excluded: pd.DataFrame,
    *,
    strategy_trades: pd.DataFrame,
    opportunity_curves: dict[str, pd.DataFrame] | None = None,
) -> dict[str, Any]:
    """Compute the registered hard gate and descriptive report items."""

    metrics_by_cost: dict[str, Any] = {}
    for multiplier, strategy in strategy_by_cost.items():
        benchmark = benchmark_by_cost[multiplier]
        curve = _curve_metrics(strategy["daily"], benchmark["daily"])
        metrics_by_cost[f"{multiplier:g}x"] = {
            key: value for key, value in curve.items() if key != "relative_curve"
        }
        metrics_by_cost[f"{multiplier:g}x"]["periods"] = _period_rows(strategy["daily"], benchmark["daily"])
        metrics_by_cost[f"{multiplier:g}x"]["mean_daily_two_sided_turnover"] = strategy["mean_daily_two_sided_turnover"]
    # Gross is explicitly simulated with zero costs below; the 0.5x key is
    # retained for sensitivity and is not used as the gross gate.
    hard = strategy_by_cost.get(0.0)
    if hard is None:
        raise ValueError("strategy_by_cost must contain a zero-cost gross simulation")
    gross_curve = _curve_metrics(hard["daily"], benchmark_by_cost[0.0]["daily"])
    net_1 = metrics_by_cost["1x"]
    net_2 = metrics_by_cost["2x"]
    positive_periods = sum(
        row["excess_return"] is not None and row["excess_return"] > 0
        for row in net_1["periods"]
    )
    hard_gate = {
        "gross_annualized_excess_ge_2pct": gross_curve["annualized_excess"] is not None and gross_curve["annualized_excess"] >= 0.02,
        "net_1x_annualized_excess_ge_1pct": net_1["annualized_excess"] is not None and net_1["annualized_excess"] >= 0.01,
        "net_2x_excess_positive": net_2["excess_return"] is not None and net_2["excess_return"] > 0,
        "at_least_two_positive_periods": positive_periods >= 2,
        "relative_max_drawdown_le_5pct": net_1["relative_max_drawdown"] <= 0.05,
    }
    opportunity: dict[str, Any] = {}
    if opportunity_curves:
        strategy_daily = strategy_by_cost[1.0]["daily"]
        for name, curve in opportunity_curves.items():
            # Keep tabular relative curves out of the JSON metrics payload;
            # they are written as CSV artifacts by ``run_acceptance`` below.
            opportunity[name] = {
                key: value
                for key, value in _curve_metrics(strategy_daily, curve).items()
                if key != "relative_curve"
            }
    mix = opportunity.get("csi_mix_50_50", {})
    opportunity_pass = bool(mix.get("annualized_excess") is not None and mix["annualized_excess"] > 0)
    return {
        "cost_sensitivity": metrics_by_cost,
        "gross": {key: value for key, value in gross_curve.items() if key != "relative_curve"},
        "hard_gate": {**hard_gate, "passed": all(hard_gate.values())},
        "positive_period_count_1x": int(positive_periods),
        "excluded_bottom_metrics": _excluded_metrics(excluded),
        "opportunity_cost": opportunity,
        "opportunity_cost_secondary_gate": {"csi_mix_50_50_net_excess_positive": opportunity_pass, "passed": opportunity_pass},
        "turnover": {
            "mean_daily_two_sided": strategy_by_cost[1.0]["mean_daily_two_sided_turnover"],
            "total_buy_notional_at_unit_capital": strategy_by_cost[1.0]["total_buy_notional"],
            "total_sell_notional_at_unit_capital": strategy_by_cost[1.0]["total_sell_notional"],
            "bottom_membership": _membership_change_rate(excluded),
        },
        "capacity": _capacity_metrics(strategy_trades),
        "classification": "one_shot_internal_historical_economic_acceptance",
        "production_authorized": False,
    }


def _build_signal_trades(
    predictions: pd.DataFrame,
    panel: pd.DataFrame,
    *,
    mode: str,
    top_k: int | None = None,
    exclusion_fraction: float = 0.10,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    prediction_keys = _normalise_keys(predictions)[["date", "code", "prediction"]]
    joined = prediction_keys.merge(
        panel[["date", "code", "universe_b2", "y_excess20", "adv20", "float_mcap"]],
        on=["date", "code"], how="left", validate="one_to_one",
    )
    joined = joined.loc[joined["universe_b2"].fillna(False)].copy()
    if joined.empty:
        raise ValueError("signal predictions have no B2 rows")
    if mode == "c_track":
        return _partition_bottom_exclusion(joined, exclusion_fraction=exclusion_fraction)
    selected_parts: list[pd.DataFrame] = []
    excluded_parts: list[pd.DataFrame] = []
    for date, day in joined.groupby("date", sort=True):
        if mode == "top_k":
            ordered = day.sort_values(["prediction", "code"], ascending=[False, True], kind="mergesort")
            if top_k is None or top_k < 1:
                raise ValueError("top_k must be positive for top_k mode")
            selected = ordered.iloc[: min(top_k, len(ordered))].copy()
            excluded = ordered.iloc[0:0].copy()
        elif mode == "b2_equal_weight":
            ordered = day.sort_values(["prediction", "code"], ascending=[False, True], kind="mergesort")
            selected = day.sort_values("code", kind="mergesort").copy()
            excluded = ordered.iloc[0:0].copy()
        else:
            raise ValueError(f"unsupported signal mode: {mode}")
        if selected.empty:
            raise ValueError(f"signal mode {mode} selected no rows on {date.date()}")
        selected["signal_role"] = "retained"
        # Equal-weight the retained set.  If a retained name later lacks an
        # executable price, its share remains cash rather than being silently
        # reallocated to other names by the simulator.
        selected["cohort_size"] = int(len(selected))
        selected_parts.append(selected)
    selected_frame = pd.concat(selected_parts, ignore_index=True)
    excluded_frame = pd.concat(excluded_parts, ignore_index=True) if excluded_parts else joined.iloc[0:0].copy()
    return selected_frame, excluded_frame


def _build_b2_baseline_selection(
    signal_dates: Iterable[pd.Timestamp], panel: pd.DataFrame
) -> pd.DataFrame:
    """Build the no-exclusion comparator from every B2 row on signal dates."""

    rows = panel.loc[
        panel["date"].isin(pd.DatetimeIndex(signal_dates)) & panel["universe_b2"].fillna(False)
    ].copy()
    if rows.empty:
        raise ValueError("B2 baseline has no rows on the prediction signal dates")
    rows["prediction"] = 0.0
    rows["signal_role"] = "b2_equal_weight"
    rows["cohort_size"] = rows.groupby("date")["code"].transform("size")
    return rows.sort_values(["date", "code"], kind="mergesort").reset_index(drop=True)


def _run_simulation(
    predictions: pd.DataFrame,
    panel: pd.DataFrame,
    *,
    mode: str,
    market_dates: pd.DatetimeIndex,
    evaluation_end: pd.Timestamp,
    top_k: int | None = None,
) -> tuple[dict[float, dict[str, Any]], pd.DataFrame, pd.DataFrame]:
    if mode == "b2_equal_weight":
        selected = _build_b2_baseline_selection(predictions["date"].unique(), panel)
        excluded = panel.iloc[0:0].copy()
    else:
        selected, excluded = _build_signal_trades(predictions, panel, mode=mode, top_k=top_k)
    resolved = resolve_trade_window(
        selected, panel, market_dates=market_dates, horizon=HORIZON, evaluation_end=evaluation_end
    )
    simulations: dict[float, dict[str, Any]] = {}
    for multiplier in (0.0, *COST_MULTIPLIERS):
        simulations[multiplier] = simulate_cohort_portfolio(
            resolved, panel, market_dates=market_dates, cost_multiplier=multiplier
        )
    return simulations, resolved, excluded


def run_acceptance(
    *,
    panel_path: str | Path = DEFAULT_PANEL,
    neutral_predictions_path: str | Path = DEFAULT_NEUTRAL_PREDICTIONS,
    raw_predictions_path: str | Path | None = DEFAULT_RAW_PREDICTIONS,
    frozen_path: str | Path = DEFAULT_FROZEN,
    c1000_path: str | Path | None = DEFAULT_C1000,
    c2000_path: str | Path | None = DEFAULT_C2000,
    sse_path: str | Path | None = DEFAULT_SSE,
    output_dir: str | Path = DEFAULT_OUT,
) -> dict[str, Any]:
    """Run the one-shot economic acceptance and write reproducible artifacts."""

    output = _path(output_dir)
    decision_path = output / "acceptance_decision.json"
    if decision_path.exists():
        raise RuntimeError("C-track acceptance has already produced a decision in this directory")
    if c1000_path is None or c2000_path is None:
        raise ValueError("C-track opportunity-cost benchmarks require 000852.csv and 000915.csv")
    inputs = load_inputs(
        panel_path=panel_path,
        neutral_predictions_path=neutral_predictions_path,
        raw_predictions_path=raw_predictions_path,
        frozen_path=frozen_path,
    )
    panel = inputs["panel"]
    frozen = inputs["frozen"]
    market_dates, _ = _calendar_positions(panel)
    evaluation_end = frozen["_evaluation_end"]
    neutral_sim, neutral_trades, excluded = _run_simulation(
        inputs["neutral_predictions"], panel, mode="c_track", market_dates=market_dates, evaluation_end=evaluation_end
    )
    baseline_sim, baseline_trades, _ = _run_simulation(
        inputs["neutral_predictions"], panel, mode="b2_equal_weight", market_dates=market_dates, evaluation_end=evaluation_end
    )
    strategy_by_cost = {key: value for key, value in neutral_sim.items()}
    benchmark_by_cost = {key: value for key, value in baseline_sim.items()}
    start = min(neutral_trades["entry_date"].dropna().min(), baseline_trades["entry_date"].dropna().min())
    end = max(neutral_trades["exit_date"].dropna().max(), baseline_trades["exit_date"].dropna().max())
    opportunity_curves = compute_index_benchmarks(
        {"csi1000": c1000_path, "csi2000": c2000_path}, start=start, end=end
    )
    if sse_path is not None:
        sse = pd.read_csv(_path(sse_path), encoding="utf-8-sig")
        opportunity_curves["sse_appendix"] = _index_equity(sse, start, end)
    metrics = compute_acceptance_metrics(
        strategy_by_cost,
        benchmark_by_cost,
        excluded,
        strategy_trades=neutral_sim[1.0]["trades"],
        opportunity_curves=opportunity_curves,
    )
    raw_appendix: dict[str, Any] = {}
    for label, prediction in (("neutral", inputs["neutral_predictions"]), ("raw", inputs["raw_predictions"])):
        if prediction is None:
            continue
        rows: dict[str, Any] = {}
        for top_k in (5, 10, 20):
            simulations, trades, _ = _run_simulation(
                prediction, panel, mode="top_k", top_k=top_k, market_dates=market_dates, evaluation_end=evaluation_end
            )
            rows[str(top_k)] = {
                "gross": simulations[0.0]["annualized_return"],
                "net_1x_vs_b2": _curve_metrics(simulations[1.0]["daily"], baseline_sim[1.0]["daily"])["annualized_excess"],
                "trade_count": simulations[1.0]["trade_count"],
                "unresolved_count": simulations[1.0]["unresolved_count"],
                "trades": trades,
            }
        raw_appendix[label] = rows

    output.mkdir(parents=True, exist_ok=True)
    manifest = {
        "classification": "one_shot_internal_historical_economic_acceptance",
        "registered_contract": str(_path(frozen_path).resolve()),
        "panel": inputs["panel_path"],
        "neutral_predictions": inputs["neutral_predictions_path"],
        "raw_predictions": inputs["raw_predictions_path"],
        "evaluation_period": frozen["evaluation_period"],
        "signal_last_date": str(inputs["neutral_predictions"]["date"].max().date()),
        "horizon_trading_days": HORIZON,
        "cohort_days": COHORT_DAYS,
        "bottom_exclusion_fraction": 0.10,
        "cost_multipliers": list(COST_MULTIPLIERS),
        "base_side_cost": BASE_SIDE_COST,
        "execution_semantics": "observable_ohlc; missing bars defer to next available price; final last close settlement",
        "factor_list_mutation_forbidden": True,
        "production_authorized": False,
    }
    _write_json(output / "acceptance_manifest.json", manifest)
    _write_json(output / "acceptance_metrics.json", metrics)
    decision = {
        "classification": metrics["classification"],
        "mechanism_gate": metrics["hard_gate"],
        "opportunity_cost_gate": metrics["opportunity_cost_secondary_gate"],
        "mechanism_conclusion": "passed" if metrics["hard_gate"]["passed"] else "failed_final_for_frozen_candidate",
        "product_value_conclusion": (
            "signal_effective_but_no_product_value"
            if metrics["hard_gate"]["passed"] and not metrics["opportunity_cost_secondary_gate"]["passed"]
            else "passed" if metrics["hard_gate"]["passed"] else "not_applicable"
        ),
        "production_authorized": False,
    }
    _write_json(decision_path, decision)
    neutral_sim[1.0]["daily"].to_csv(output / "c_track_daily.csv", index=False, encoding="utf-8-sig")
    baseline_sim[1.0]["daily"].to_csv(output / "b2_equal_weight_daily.csv", index=False, encoding="utf-8-sig")
    neutral_sim[1.0]["trades"].to_csv(output / "c_track_trades.csv", index=False, encoding="utf-8-sig")
    excluded.to_csv(output / "excluded_bottom.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(
        [
            {"benchmark": name, "annualized_excess": value.get("annualized_excess"), "excess_return": value.get("excess_return")}
            for name, value in metrics["opportunity_cost"].items()
        ]
    ).to_csv(output / "opportunity_cost.csv", index=False, encoding="utf-8-sig")
    for name, curve in opportunity_curves.items():
        curve.to_csv(output / f"benchmark_{name}.csv", index=False, encoding="utf-8-sig")
        relative = _curve_metrics(neutral_sim[1.0]["daily"], curve)["relative_curve"]
        relative.to_csv(output / f"relative_curve_{name}.csv", index=False, encoding="utf-8-sig")
    relative_b2 = _curve_metrics(neutral_sim[1.0]["daily"], baseline_sim[1.0]["daily"])["relative_curve"]
    relative_b2.to_csv(output / "relative_curve_b2_equal_weight.csv", index=False, encoding="utf-8-sig")
    topk_summary = {
        label: {
            top_k: {key: value for key, value in row.items() if key != "trades"}
            for top_k, row in rows.items()
        }
        for label, rows in raw_appendix.items()
    }
    _write_json(output / "topk_appendix.json", topk_summary)
    topk_rows = []
    for label, rows in topk_summary.items():
        for top_k, row in rows.items():
            topk_rows.append({"scorer": label, "top_k": int(top_k), **row})
    pd.DataFrame(topk_rows).to_csv(output / "topk_appendix.csv", index=False, encoding="utf-8-sig")
    lines = [
        "# C 轨经济验收结果",
        "",
        "> 一次性内部历史经济验收；不构成生产批准或实盘授权。",
        "",
        f"- 机制层结论：**{decision['mechanism_conclusion']}**",
        f"- 机会成本层：**{decision['product_value_conclusion']}**",
        f"- 有效信号日：`{manifest['signal_last_date']}`",
        "",
        "## 机制门槛",
        "",
        pd.DataFrame([metrics["hard_gate"]]).to_markdown(index=False),
        "",
        "## 成本敏感性",
        "",
        pd.DataFrame(
            [
                {"cost": cost, "annualized_excess": row.get("annualized_excess"), "excess_return": row.get("excess_return"), "relative_max_drawdown": row.get("relative_max_drawdown")}
                for cost, row in metrics["cost_sensitivity"].items()
            ]
        ).to_markdown(index=False),
        "",
        "## 机会成本",
        "",
        pd.DataFrame(
            [{"benchmark": name, "annualized_excess": value.get("annualized_excess"), "excess_return": value.get("excess_return")} for name, value in metrics["opportunity_cost"].items()]
        ).to_markdown(index=False),
        "",
        "## 限制",
        "",
        "- 面板没有独立的停牌、涨跌停成交和退市字段；缺失 OHLC 仅作为可观察的停牌/退市风险代理。",
        "- 容量、换手和 Top-K 结果是登记报告项或附录，不参与机制门槛。",
    ]
    (output / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"manifest": manifest, "metrics": metrics, "decision": decision, "output_dir": str(output.resolve()), "topk_appendix": raw_appendix}


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the preregistered frozen B2 C-track economic acceptance")
    parser.add_argument("--panel", default=str(DEFAULT_PANEL))
    parser.add_argument("--neutral-predictions", default=str(DEFAULT_NEUTRAL_PREDICTIONS))
    parser.add_argument("--raw-predictions", default=str(DEFAULT_RAW_PREDICTIONS))
    parser.add_argument("--frozen", default=str(DEFAULT_FROZEN))
    parser.add_argument("--c1000", default=str(DEFAULT_C1000) if DEFAULT_C1000 else None)
    parser.add_argument("--c2000", default=str(DEFAULT_C2000) if DEFAULT_C2000 else None)
    parser.add_argument("--sse", default=str(DEFAULT_SSE) if DEFAULT_SSE else None)
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT))
    args = parser.parse_args()
    validate_startup_or_exit(("kline",))
    result = run_acceptance(
        panel_path=args.panel,
        neutral_predictions_path=args.neutral_predictions,
        raw_predictions_path=args.raw_predictions,
        frozen_path=args.frozen,
        c1000_path=args.c1000,
        c2000_path=args.c2000,
        sse_path=args.sse,
        output_dir=args.out_dir,
    )
    print(json.dumps({"decision": result["decision"], "output_dir": result["output_dir"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
