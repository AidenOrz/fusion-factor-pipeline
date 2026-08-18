"""Shanghai Composite benchmark evaluation.

Acceptance criterion (user decision): a candidate only needs to beat the
Shanghai Composite Index (上证指数, 000001.SH). ``legacy_score`` is no longer
a hard gate because its historical backtest engine had known accounting
issues; it remains a diagnostic reference only.
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

WORKSPACE = Path(__file__).resolve().parents[2]
DEFAULT_DAILY_PANEL = (
    WORKSPACE / "a-share-factor-engine-v6" / "artifacts" / "panel" / "panel.parquet"
)
DEFAULT_EVENTS = (
    WORKSPACE / "a-share-factor-engine-v6" / "artifacts" / "signals_m6" / "events.parquet"
)
DEFAULT_INDEX = WORKSPACE / "a-share-kline-data" / "_bench_000001SH.csv"
CLASSIFICATION = "post_hoc_descriptive_only"
HORIZON = 20
TRADING_DAYS_PER_YEAR = 252


def _ndcg_at_k(scores: pd.Series, targets: pd.Series, k: int) -> float | None:
    """Compute bounded NDCG from cross-sectional target ranks.

    EXRET20 can be negative, so relevance is represented by the target's
    percentile rank within the day.  This keeps the diagnostic comparable
    across dates without exponentiating raw returns.
    """
    valid = pd.DataFrame({"score": scores, "target": targets}).dropna()
    if valid.empty:
        return None
    relevance = valid["target"].rank(method="average", pct=True).to_numpy(dtype=float)
    predicted_order = np.argsort(-valid["score"].to_numpy(dtype=float), kind="mergesort")
    predicted = relevance[predicted_order][:k]
    ideal = np.sort(relevance)[::-1][:k]
    discounts = 1.0 / np.log2(np.arange(2, len(predicted) + 2))
    dcg = float(np.sum((2.0**predicted - 1.0) * discounts))
    ideal_discounts = 1.0 / np.log2(np.arange(2, len(ideal) + 2))
    ideal_dcg = float(np.sum((2.0**ideal - 1.0) * ideal_discounts))
    if ideal_dcg <= 0:
        return 1.0
    return dcg / ideal_dcg


def ndcg_at_k(scores: pd.Series, targets: pd.Series, k: int) -> float | None:
    """Public NDCG helper for model-quality diagnostics."""
    return _ndcg_at_k(scores, targets, k)


def ndcg_random_baseline(targets: pd.Series, k: int) -> float | None:
    """Expected NDCG for a uniformly random ordering of one cross-section."""
    valid = pd.to_numeric(targets, errors="coerce").dropna()
    if valid.empty:
        return None
    relevance = valid.rank(method="average", pct=True).to_numpy(dtype=float)
    gains = 2.0**relevance - 1.0
    width = min(int(k), len(gains))
    discounts = 1.0 / np.log2(np.arange(2, width + 2))
    ideal_dcg = float(np.sum(np.sort(gains)[::-1][:width] * discounts))
    if ideal_dcg <= 0:
        return 1.0
    expected_dcg = float(gains.mean() * discounts.sum())
    return expected_dcg / ideal_dcg


def load_index(path: Path | str = DEFAULT_INDEX) -> pd.DataFrame:
    frame = pd.read_csv(path, dtype={"code": str}, encoding="utf-8-sig")
    frame["date"] = pd.to_datetime(frame["date"], errors="raise")
    return frame.sort_values("date").reset_index(drop=True)


def attach_forward_returns(
    daily: pd.DataFrame,
    index: pd.DataFrame,
    *,
    horizon: int = HORIZON,
    stock_column: str = "code",
) -> pd.DataFrame:
    if horizon < 1:
        raise ValueError("horizon must be positive")
    daily = daily.copy()
    daily["date"] = pd.to_datetime(daily["date"], errors="raise")
    daily = daily.sort_values([stock_column, "date"]).reset_index(drop=True)
    daily["fwd_ret"] = (
        daily.groupby(stock_column, sort=False)["close"].shift(-horizon)
        / daily["close"]
        - 1.0
    )
    idx = index[["date", "close"]].copy()
    idx["index_fwd_ret"] = idx["close"].shift(-horizon) / idx["close"] - 1.0
    result = daily.merge(idx[["date", "index_fwd_ret"]], on="date", how="left")
    result["excess_vs_sse"] = result["fwd_ret"] - result["index_fwd_ret"]
    return result


def evaluate_scorers_vs_benchmark(
    events: pd.DataFrame,
    daily_with_returns: pd.DataFrame,
    scorers: list[str],
    *,
    horizon: int = HORIZON,
    top_ks: tuple[int, ...] = (1, 3, 5, 10, 20),
) -> dict[str, Any]:
    if horizon < 1:
        raise ValueError("horizon must be positive")
    returns = daily_with_returns[
        ["date", "code", "fwd_ret", "index_fwd_ret", "excess_vs_sse"]
    ].drop_duplicates(["date", "code"], keep="last")
    frame = events.merge(returns, on=["date", "code"], how="left", validate="many_to_one")
    out: dict[str, Any] = {"classification": CLASSIFICATION, "horizon": horizon, "scorers": {}}
    for scorer in scorers:
        ranked = frame.dropna(subset=[scorer, "fwd_ret", "index_fwd_ret"]).sort_values(
            ["date", scorer, "code"], ascending=[True, False, True], kind="mergesort"
        )
        ranked["rank_position"] = ranked.groupby("date", sort=False).cumcount() + 1
        rows = []
        for top_k in top_ks:
            selected = ranked.loc[ranked["rank_position"].le(top_k)]
            daily_groups = selected.groupby("date")
            strategy_daily = daily_groups["fwd_ret"].mean()
            index_daily = daily_groups["index_fwd_ret"].mean()
            excess_daily = daily_groups["excess_vs_sse"].mean()
            win_rate = float((strategy_daily > index_daily).mean()) if len(strategy_daily) else 0.0
            ndcg_values = [
                value
                for _, day in ranked.groupby("date", sort=False)
                if (value := _ndcg_at_k(day[scorer], day["fwd_ret"], top_k)) is not None
            ]
            # These are arithmetic annualisations of overlapping HORIZON-day
            # observations, not compounded daily live-trading returns.
            annualized_excess = (
                float(excess_daily.mean()) * TRADING_DAYS_PER_YEAR / horizon
                if len(excess_daily)
                else None
            )
            rows.append(
                {
                    "top_k": top_k,
                    "rows": int(len(selected)),
                    "dates": int(selected["date"].nunique()),
                    "strategy_mean_fwd_ret": round(float(strategy_daily.mean()), 6),
                    "sse_mean_fwd_ret": round(float(index_daily.mean()), 6),
                    "excess_mean": round(float(excess_daily.mean()), 6),
                    "daily_beat_sse_rate": round(win_rate, 6),
                    "ndcg": round(float(np.mean(ndcg_values)), 6) if ndcg_values else None,
                    "annualized_excess_approx": (
                        round(annualized_excess, 6) if annualized_excess is not None else None
                    ),
                    "annualized_excess": (
                        round(annualized_excess, 6) if annualized_excess is not None else None
                    ),
                    "annualized_excess_method": "mean overlapping forward excess * 252 / horizon",
                }
            )
        out["scorers"][scorer] = rows
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate scorers against SSE benchmark")
    parser.add_argument("--daily-panel", default=str(DEFAULT_DAILY_PANEL))
    parser.add_argument("--events", default=str(DEFAULT_EVENTS))
    parser.add_argument("--index", default=str(DEFAULT_INDEX))
    parser.add_argument("--horizon", type=int, default=HORIZON)
    args = parser.parse_args()

    daily = pd.read_parquet(args.daily_panel)
    daily["date"] = pd.to_datetime(daily["date"], errors="raise")
    daily["code"] = daily["code"].astype(str).str.zfill(6)
    index = load_index(args.index)
    returns = attach_forward_returns(daily, index, horizon=args.horizon)
    events = pd.read_parquet(args.events)
    events["date"] = pd.to_datetime(events["date"], errors="raise")
    events["code"] = events["code"].astype(str).str.zfill(6)
    result = evaluate_scorers_vs_benchmark(
        events, returns, ["legacy_score", "composite_score"], horizon=args.horizon
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
