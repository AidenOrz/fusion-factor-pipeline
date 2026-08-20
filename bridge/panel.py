"""Factor panel builder for the full-market ranking pipeline.

The default research scope is the full daily v6 panel.  The legacy events
scope remains available only for reproducing event-conditioned diagnostics.
Every expression is evaluated fail-fast: a missing required column raises,
NaN/inf is sanitized and counted, and factors exceeding the missing-rate
threshold are rejected instead of silently diluted.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd

from bridge.data import DataContractError, code_to_ts_code
from bridge.feature_engineering import (
    add_cross_sectional_features,
    add_industry_neutral_features,
    add_pairwise_cross_features,
)
from bridge.ops import FactorEvaluationError, evaluate_builtin, sanitize
from bridge.labels import attach_cross_sectional_labels
from bridge.universe import DEFAULT_MCAP, attach_universe_masks, universe_summary

WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_EVENTS = (
    WORKSPACE_ROOT
    / "a-share-factor-engine-v6"
    / "artifacts"
    / "signals_m6"
    / "events.parquet"
)
DEFAULT_PANEL = (
    WORKSPACE_ROOT / "a-share-factor-engine-v6" / "artifacts" / "panel" / "panel.parquet"
)
QUANTGPT_ROOT = WORKSPACE_ROOT / "QuantGPT"

MISSING_RATE_LIMIT = 0.20
EXPRESSION_FEATURES = [
    "SQZ_ATR_REL__score",
    "SQZ_RANGE_ABS__score",
    "TRG_VOL_EXP__score",
    "POS_EMA20_BAND__score",
]


def load_events(events_path: Path | str = DEFAULT_EVENTS) -> pd.DataFrame:
    frame = pd.read_parquet(events_path)
    frame["date"] = pd.to_datetime(frame["date"], errors="raise")
    frame["code"] = frame["code"].astype(str).str.zfill(6)
    frame["ts_code"] = frame["code"].map(code_to_ts_code)
    frame = frame.sort_values(["date", "code"]).reset_index(drop=True)
    return frame


def attach_vwap_and_contract(frame: pd.DataFrame) -> pd.DataFrame:
    """Attach vwap with the volume-unit contract (v6 kline volume is in lots)."""
    result = frame.copy()
    result["vol"] = result["volume"].astype(float) * 100.0
    result["vwap"] = result["amount"].astype(float) / result["vol"].replace(0, np.nan)
    valid = result["vol"].gt(0) & result["amount"].notna()
    ratio = result.loc[valid, "vwap"] / result.loc[valid, "close"]
    bad = ratio[(ratio < 0.2) | (ratio > 5.0)]
    if len(bad):
        raise DataContractError(
            f"vwap unit check failed for {len(bad)} rows; volume was not converted from lots"
        )
    result["trade_date"] = result["date"].dt.normalize()
    result["pct_chg"] = result.groupby("code", sort=False)["close"].pct_change()
    result["is_st"] = result["name"].astype(str).str.contains(r"ST|退", regex=True)
    first_dates = result.groupby("code")["trade_date"].transform("min")
    result["days_since_ipo"] = (result["trade_date"] - first_dates).dt.days
    result["adj_factor"] = 1.0  # source prices are forward-adjusted
    return result


def attach_industry(frame: pd.DataFrame, panel_path: Path | str = DEFAULT_PANEL) -> pd.DataFrame:
    panel = pd.read_parquet(
        panel_path,
        columns=["date", "code", "industry"],
        filters=[("code", "in", sorted(frame["code"].unique()))],
    )
    panel["date"] = pd.to_datetime(panel["date"], errors="raise")
    panel["code"] = panel["code"].astype(str).str.zfill(6)
    panel = panel.drop_duplicates(["date", "code"], keep="last")
    result = frame.merge(panel, on=["date", "code"], how="left", validate="many_to_one")
    result["industry_code"] = result["industry"].astype("category").cat.codes
    return result


def evaluate_expression(frame: pd.DataFrame, spec: dict[str, Any]) -> pd.Series:
    engine = spec.get("engine", "builtin")
    expression = spec["expression"]
    if engine == "builtin":
        series, report = evaluate_builtin(frame, expression, expression_id=spec.get("id", "expr"))
    elif engine == "quantgpt":
        series = _evaluate_quantgpt(frame, expression, spec.get("id", "expr"))
        series, report = sanitize(series, spec.get("id", "expr"))
    else:
        raise FactorEvaluationError(f"Unknown expression engine: {engine!r}")
    if report.missing_rate > MISSING_RATE_LIMIT:
        raise FactorEvaluationError(
            f"expression {spec.get('id')!r} missing_rate={report.missing_rate:.3f} "
            f"exceeds limit {MISSING_RATE_LIMIT}; refusing to accept a silently diluted factor"
        )
    return series


def _evaluate_quantgpt(frame: pd.DataFrame, expression: str, expression_id: str) -> pd.Series:
    if str(QUANTGPT_ROOT) not in sys.path:
        sys.path.insert(0, str(QUANTGPT_ROOT))
    try:
        from quantgpt.expression_parser import parse_expression
    except Exception as exc:  # pragma: no cover - environment dependent
        raise FactorEvaluationError(f"QuantGPT parser unavailable: {exc}") from exc
    work = frame.drop(columns=["trade_date"], errors="ignore").rename(
        columns={"date": "trade_date", "code": "stock_code"}
    )
    required_hint = {"vwap", "amount", "volume", "close", "open", "high", "low"}
    missing = {token for token in required_hint if token in expression.lower()} - set(work.columns)
    if missing:
        raise FactorEvaluationError(
            f"expression {expression_id!r} references columns {sorted(missing)} that are missing"
        )
    try:
        factor_fn = parse_expression(expression)
        values = factor_fn(work)
    except Exception as exc:
        raise FactorEvaluationError(
            f"QuantGPT evaluation failed for {expression_id!r}: {exc}"
        ) from exc
    return pd.Series(np.asarray(values, dtype=float), index=frame.index)


def build_daily_feature_frame(panel_path: Path | str = DEFAULT_PANEL) -> pd.DataFrame:
    """Compute time-series expressions on the FULL daily panel, not on sparse events."""
    panel = pd.read_parquet(panel_path)
    panel["date"] = pd.to_datetime(panel["date"], errors="raise")
    panel["code"] = panel["code"].astype(str).str.zfill(6)
    panel = panel.sort_values(["code", "date"]).reset_index(drop=True)
    panel["vol"] = panel["volume"].astype(float) * 100.0
    panel["vwap"] = panel["amount"].astype(float) / panel["vol"].replace(0, np.nan)
    valid = panel["vol"].gt(0) & panel["amount"].notna()
    ratio = panel.loc[valid, "vwap"] / panel.loc[valid, "close"]
    if ((ratio < 0.2) | (ratio > 5.0)).any():
        raise DataContractError("daily panel vwap unit check failed")
    panel["ts_code"] = panel["code"].map(code_to_ts_code)
    panel["trade_date"] = panel["date"].dt.normalize()
    panel["pct_chg"] = panel.groupby("code", sort=False)["close"].pct_change()
    panel["is_st"] = panel["name"].astype(str).str.contains(r"ST|退", regex=True)
    first_dates = panel.groupby("code")["trade_date"].transform("min")
    panel["days_since_ipo"] = (panel["trade_date"] - first_dates).dt.days
    panel["adj_factor"] = 1.0
    if "industry" in panel.columns:
        panel["industry_code"] = panel["industry"].astype("category").cat.codes
    return panel


def build_fusion_panel(
    expressions: list[dict[str, Any]],
    *,
    events_path: Path | str = DEFAULT_EVENTS,
    panel_path: Path | str = DEFAULT_PANEL,
    out_path: Path | str,
    shares_path: Path | str | None = None,
    mcap_path: Path | str | None = DEFAULT_MCAP,
    scope: str = "full_market",
    add_labels: bool = True,
    extra_factors: list[Path | str] | None = None,
    add_cs_features: bool = False,
    cs_factors: list[str] | None = None,
    industry_neutral_factors: list[str] | None = None,
    cross_factors: list[str] | None = None,
) -> dict[str, Any]:
    if scope not in {"full_market", "events"}:
        raise ValueError("scope must be 'full_market' or 'events'")
    daily = build_daily_feature_frame(panel_path)
    if shares_path is not None:
        from bridge.refdata import shares_to_market_cap

        shares = pd.read_parquet(shares_path)
        daily = shares_to_market_cap(daily, shares)
    extra_columns: list[str] = []
    for extra_path in extra_factors or []:
        extra = pd.read_parquet(extra_path)
        extra["date"] = pd.to_datetime(extra["date"], errors="raise")
        extra["code"] = extra["code"].astype(str).str.zfill(6)
        factor_cols = [
            column
            for column in extra.columns
            if (column.startswith("alpha") or column.startswith("gtja"))
            and column not in daily.columns
        ]
        daily = daily.merge(
            extra[["date", "code", *factor_cols]],
            on=["date", "code"],
            how="left",
            validate="many_to_one",
        )
        extra_columns.extend(factor_cols)
    reports: list[dict[str, Any]] = []
    expression_columns: list[str] = []
    for spec in expressions:
        series = evaluate_expression(daily, spec)
        # alpha_ prefix keeps the panel consumable by aurumq-rl FactorPanelLoader.
        column = f"alpha_qg_{spec['id']}"
        daily[column] = series
        expression_columns.append(column)
        missing_rate = float(series.isna().mean())
        reports.append(
            {
                "id": spec["id"],
                "expression": spec["expression"],
                "engine": spec.get("engine", "builtin"),
                "missing_rate": round(missing_rate, 6),
                "n_rows": int(len(series)),
                "n_missing": int(series.isna().sum()),
            }
        )

    # The factor lists for target-derived transformations are explicit inputs.
    # They must come from a selection-period report, never from ICs calculated
    # over this full panel.
    base_factor_columns = [
        *[column for column in EXPRESSION_FEATURES if column in daily.columns],
        *expression_columns,
        *extra_columns,
        *[column for column in ("market_cap", "total_shares") if column in daily.columns],
    ]
    derived_columns: list[str] = []
    if add_cs_features:
        daily, added = add_cross_sectional_features(daily, cs_factors or base_factor_columns)
        derived_columns.extend(added)
    if industry_neutral_factors:
        daily, added = add_industry_neutral_features(daily, industry_neutral_factors)
        derived_columns.extend(added)
    if cross_factors:
        daily, added = add_pairwise_cross_features(daily, cross_factors)
        derived_columns.extend(added)

    if scope == "full_market":
        frame = attach_universe_masks(daily, mcap_path=mcap_path)
        if add_labels:
            frame = attach_cross_sectional_labels(frame)
    else:
        frame = load_events(events_path)
    merge_keys = ["date", "code"]
    available = [
        c
        for c in [
            "trade_date",
            "industry_code",
            "vwap",
            "pct_chg",
            "vol",
            "adj_factor",
            "is_st",
            "days_since_ipo",
            "total_shares",
            "market_cap",
            *expression_columns,
            *extra_columns,
            *derived_columns,
        ]
        if c in daily.columns
    ]
    if scope == "events":
        frame = frame.merge(
            daily[merge_keys + available],
            on=merge_keys,
            how="left",
            validate="many_to_one",
        )

    keep = [
        "ts_code",
        "trade_date",
        "date",
        "code",
        "open",
        "high",
        "low",
        "close",
        "pct_chg",
        "vol",
        "amount",
        "vwap",
        "adj_factor",
        "is_st",
        "days_since_ipo",
        "industry_code",
        "total_shares",
        "market_cap",
        "l0_eligible",
        "l1a_wide_event",
        "l1b_legacy_v55",
        "legacy_score",
        "composite_score",
        "EXRET5",
        "EXRET20",
        "EXRET60",
        "MFE20",
        "MFE60",
        "MAE20",
        *EXPRESSION_FEATURES,
        *[f"alpha_qg_{spec['id']}" for spec in expressions],
        *extra_columns,
        *derived_columns,
    ]
    if scope == "full_market":
        full_market_keep = [
            "ts_code",
            "trade_date",
            "date",
            "code",
            "name",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "amount",
            "vwap",
            "pct_chg",
            "vol",
            "adj_factor",
            "is_st",
            "days_since_ipo",
            "bar_number",
            "industry",
            "industry_code",
            "benchmark_open",
            "benchmark_close",
            "float_mcap",
            "adv20",
            "universe_b1",
            "universe_b2",
            "universe_b3",
            "universe_version",
            "universe_adv_threshold",
            "stock_ret20",
            "benchmark_ret20",
            "y_excess20",
            "label_horizon",
            "y_raw_rank20_b1",
            "y_raw_rank20_b2",
            "y_raw_rank20_b3",
            "y_neutral_resid20_b1",
            "y_neutral_resid20_b2",
            "y_neutral_resid20_b3",
            "y_neutral_rank20_b1",
            "y_neutral_rank20_b2",
            "y_neutral_rank20_b3",
            "atr_relative",
            "range_relative",
            "vol_ratio",
            "position_ema20",
            "ret_5d",
            "ret_20d",
            "realized_vol_120d_d1",
            "amount_dry_ratio",
            *expression_columns,
            *extra_columns,
            *derived_columns,
        ]
        output = frame[[column for column in full_market_keep if column in frame.columns]].copy()
        output["panel_scope"] = "full_market"
    else:
        keep = list(dict.fromkeys(column for column in keep if column in frame.columns))
        output = frame[keep].copy()
        output["panel_scope"] = "events"
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    output.to_parquet(out_path, index=False)
    summary_path = None
    if scope == "full_market" and {"universe_b1", "universe_b2", "universe_b3"}.intersection(output.columns):
        summary_path = out_path.with_name(f"{out_path.stem}_universe_summary.csv")
        universe_summary(output).to_csv(summary_path, index=False, encoding="utf-8-sig")
    return {
        "out_path": str(out_path),
        "scope": scope,
        "rows": int(len(output)),
        "dates": int(output["trade_date"].nunique()),
        "stocks": int(output["code"].nunique()),
        "universe_summary_path": str(summary_path) if summary_path else None,
        "expression_reports": reports,
        "derived_features": derived_columns,
        "cs_factors": cs_factors or (base_factor_columns if add_cs_features else []),
    }


def load_expression_specs(path: Path | str) -> list[dict[str, Any]]:
    return json.loads(Path(path).read_text(encoding="utf-8"))["expressions"]


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Build the fusion factor panel")
    parser.add_argument("--expressions", default="config/expressions.json")
    parser.add_argument("--out", default="data/panel_fusion_full_market.parquet")
    parser.add_argument("--events", default=str(DEFAULT_EVENTS))
    parser.add_argument("--panel", default=str(DEFAULT_PANEL))
    parser.add_argument("--shares", default=None)
    parser.add_argument("--mcap", default=str(DEFAULT_MCAP))
    parser.add_argument(
        "--scope",
        choices=["full_market", "events"],
        default="full_market",
        help="full daily market scope for the main system, or legacy events diagnostic scope",
    )
    parser.add_argument(
        "--no-labels",
        action="store_true",
        help="skip forward return and cross-sectional rank labels",
    )
    parser.add_argument("--extra-factors", default=None, help="comma separated factor parquet paths")
    parser.add_argument(
        "--add-cs-features",
        action="store_true",
        help="add per-date rank, z-score, and winsorised versions of the panel factors",
    )
    parser.add_argument(
        "--augmentation-plan",
        default=None,
        help="selection-period feature_augmentation_plan.json; supplies explicit factor lists",
    )
    parser.add_argument(
        "--industry-neutral-factors",
        default=None,
        help="comma separated factors fixed by a selection-period report",
    )
    parser.add_argument(
        "--cross-factors",
        default=None,
        help="comma separated factors fixed by a selection-period report (pairwise x/div)",
    )
    args = parser.parse_args()

    specs = load_expression_specs(args.expressions)
    extra_factors = (
        [Path(item.strip()) for item in args.extra_factors.split(",") if item.strip()]
        if args.extra_factors
        else None
    )
    plan = (
        json.loads(Path(args.augmentation_plan).read_text(encoding="utf-8"))
        if args.augmentation_plan
        else None
    )
    if plan is not None:
        if plan.get("selection_only") is not True or plan.get("automatic_direction_flip") is not False:
            raise ValueError(
                "augmentation plan must be selection-only and prohibit direction flips"
            )
        args.add_cs_features = True
        cs_factors = list(plan.get("cs_source_factors", []))
        industry_neutral_factors = list(plan.get("industry_neutral_factors", []))
        cross_factors = list(plan.get("cross_seed_factors", []))
    else:
        cs_factors = None
        industry_neutral_factors = (
            [item.strip() for item in args.industry_neutral_factors.split(",") if item.strip()]
            if args.industry_neutral_factors
            else None
        )
        cross_factors = (
            [item.strip() for item in args.cross_factors.split(",") if item.strip()]
            if args.cross_factors
            else None
        )
    result = build_fusion_panel(
        specs,
        events_path=args.events,
        panel_path=args.panel,
        out_path=args.out,
        shares_path=args.shares,
        mcap_path=args.mcap,
        scope=args.scope,
        add_labels=not args.no_labels,
        extra_factors=extra_factors,
        add_cs_features=args.add_cs_features,
        cs_factors=cs_factors,
        industry_neutral_factors=industry_neutral_factors,
        cross_factors=cross_factors,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
