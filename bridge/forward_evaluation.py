# -*- coding: utf-8 -*-
"""Evaluate forward scores against the preregistered protocol.

The protocol (config/forward_observation_prereg_2026-10-18.json) fixes the
metrics and the pass/halt thresholds before any outcome is known. This module
only computes what the protocol says and applies its rules; it does not choose
metrics after seeing numbers, and it refuses to run against a protocol file that
is not marked as registered-before-results.

Below the protocol minimum the verdict is forced to EXPLORATORY regardless of how
good the numbers look. That is deliberate: with a 20-day label on overlapping
windows, a handful of labelled days cannot support a promotion decision, and
letting a large interim IC slip through as PASS is the exact failure this file
exists to prevent.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

from bridge.factor_selection import ic_ir_hac
from bridge.industry_names import sw2021_level1_name

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PREREG = PROJECT_ROOT / "config" / "forward_observation_prereg_2026-10-18.json"
DEFAULT_SCORES = (
    PROJECT_ROOT / "runs" / "forward_shadow_2026-10-18_pit" / "forward_scores.parquet"
)
DEFAULT_LABELS = PROJECT_ROOT / "runs" / "forward_labels_2026-10-18" / "forward_labels.parquet"
DEFAULT_OUT = PROJECT_ROOT / "runs" / "forward_evaluation_2026-10-18"

MIN_NAMES_PER_DAY = 500
SEAM_DATE = pd.Timestamp("2026-07-24")


def load_prereg(path: Path | str = DEFAULT_PREREG) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if payload.get("status") != "registered_before_results":
        raise ValueError(
            "observation protocol is not registered before results; refusing to evaluate"
        )
    if payload.get("production_authorized") is not False:
        raise ValueError("protocol must keep production_authorized=false")
    return payload


def daily_ic_series(scored: pd.DataFrame, min_names: int = MIN_NAMES_PER_DAY) -> pd.DataFrame:
    """Per-date Spearman IC between prediction and realised excess return."""

    rows: list[dict[str, Any]] = []
    for date, day in scored.groupby("date", sort=True):
        valid = day.dropna(subset=["prediction", "y_excess20"])
        if len(valid) < min_names:
            rows.append({
                "date": date,
                "ic": np.nan,
                "names": int(len(valid)),
                "dropped_reason": "too_few_names",
            })
            continue
        if valid["prediction"].nunique() < 5 or valid["y_excess20"].nunique() < 2:
            rows.append({
                "date": date,
                "ic": np.nan,
                "names": int(len(valid)),
                "dropped_reason": "degenerate_cross_section",
            })
            continue
        ic = float(spearmanr(valid["prediction"], valid["y_excess20"]).statistic)
        rows.append({
            "date": date,
            "ic": ic,
            "names": int(len(valid)),
            "dropped_reason": "",
        })
    return pd.DataFrame(rows).sort_values("date").reset_index(drop=True)


def exclusion_series(scored: pd.DataFrame, fraction: float) -> pd.DataFrame:
    """Bottom-exclusion membership per date, using the registered fraction."""

    frame = scored.dropna(subset=["prediction"]).copy()
    sizes = frame.groupby("date")["code"].transform("size")
    rank_asc = frame.groupby("date")["prediction"].rank(ascending=True, method="first")
    frame["bottom_excluded"] = rank_asc <= np.ceil(fraction * sizes)
    return frame


def turnover_series(excluded: pd.DataFrame) -> pd.Series:
    """Share of yesterday's excluded names still excluded today."""

    by_date = {
        date: set(group.loc[group["bottom_excluded"], "code"])
        for date, group in excluded.groupby("date", sort=True)
    }
    dates = sorted(by_date)
    values: dict[pd.Timestamp, float] = {}
    for previous, current in zip(dates, dates[1:]):
        before = by_date[previous]
        if not before:
            continue
        values[current] = len(before & by_date[current]) / len(before)
    return pd.Series(values, dtype=float)


def excluded_excess(scored: pd.DataFrame) -> pd.Series:
    """Per-date excluded-set mean excess minus the full-cross-section mean."""

    frame = scored.dropna(subset=["y_excess20", "prediction"]).copy()
    values: dict[pd.Timestamp, float] = {}
    for date, day in frame.groupby("date", sort=True):
        excluded = day.loc[day["bottom_excluded"], "y_excess20"]
        if excluded.empty:
            continue
        values[date] = float(excluded.mean() - day["y_excess20"].mean())
    return pd.Series(values, dtype=float)


def score_diagnostics(scored: pd.DataFrame) -> pd.DataFrame:
    frame = scored.dropna(subset=["prediction"])
    return (
        frame.groupby("date")
        .agg(
            names=("code", "size"),
            score_mean=("prediction", "mean"),
            score_std=("prediction", "std"),
            non_finite=("prediction", lambda s: int((~np.isfinite(s)).sum())),
        )
        .reset_index()
        .sort_values("date")
    )


def industry_exposure(scored: pd.DataFrame) -> dict[str, Any]:
    """Excluded-set industry concentration, under every industry column present.

    The protocol lists this as disclosure-only, but it must still be produced:
    an exclusion rule that keeps dumping one industry is a different product
    from one that spreads across the market, even at identical IC.
    """

    columns = [
        column
        for column in ("industry", "industry_pit_l1")
        if column in scored.columns
    ]
    payload: dict[str, Any] = {}
    for column in columns:
        frame = scored.dropna(subset=[column, "prediction"])
        if frame.empty:
            continue
        shares: dict[str, float] = {}
        daily_max: list[float] = []
        for _, day in frame.groupby("date", sort=True):
            excluded = day.loc[day["bottom_excluded"]]
            if excluded.empty:
                continue
            share = excluded[column].value_counts(normalize=True)
            for name, value in share.items():
                shares.setdefault(str(name), []).append(float(value))
            daily_max.append(float(share.max()))
        payload[column] = {
            "distinct_values": int(frame[column].nunique()),
            "mean_daily_max_single_industry_share": (
                float(np.mean(daily_max)) if daily_max else None
            ),
            "worst_daily_max_single_industry_share": (
                float(np.max(daily_max)) if daily_max else None
            ),
            "top_industries_by_mean_excluded_share": [
                {
                    "industry": (
                        sw2021_level1_name(name)
                        if column == "industry_pit_l1"
                        else name
                    ),
                    "code": name,
                    "mean_share": float(np.mean(values)),
                }
                for name, values in sorted(
                    shares.items(),
                    key=lambda item: float(np.mean(item[1])),
                    reverse=True,
                )[:10]
            ],
        }
    if len(payload) > 1:
        payload["note"] = (
            "Both columns are reported on purpose: the frozen path neutralises with "
            "the snapshot column, and the SW2021 PIT column is a disclosed alternative. "
            "They are different taxonomies and must not be merged."
        )
    return payload


def _mean(series: pd.Series) -> float | None:
    clean = pd.to_numeric(series, errors="coerce").dropna()
    return float(clean.mean()) if len(clean) else None


def _half_split(values: pd.Series) -> tuple[float | None, float | None]:
    clean = pd.to_numeric(values, errors="coerce").dropna()
    if len(clean) < 2:
        return None, None
    middle = len(clean) // 2
    first = float(clean.iloc[:middle].mean()) if middle else None
    second = float(clean.iloc[middle:].mean()) if len(clean) - middle else None
    return first, second


def evaluate(
    *,
    scores_path: Path | str = DEFAULT_SCORES,
    labels_path: Path | str = DEFAULT_LABELS,
    prereg_path: Path | str = DEFAULT_PREREG,
    out_dir: Path | str = DEFAULT_OUT,
) -> dict[str, Any]:
    prereg = load_prereg(prereg_path)
    scores = pd.read_parquet(scores_path)
    labels = pd.read_parquet(labels_path)
    for frame in (scores, labels):
        frame["date"] = pd.to_datetime(frame["date"], errors="raise")
        frame["code"] = frame["code"].astype(str).str.zfill(6)

    # A scores file that already carries a label column would make this merge
    # emit y_excess20_x / y_excess20_y and then fail much later with a confusing
    # KeyError. Labels are the authority here, so drop any incoming copy first.
    scores = scores.drop(columns=["y_excess20"], errors="ignore")
    merged = scores.merge(
        labels[["date", "code", "y_excess20"]], on=["date", "code"], how="left"
    )
    fraction = float(prereg["subject"]["bottom_exclusion_fraction"])
    merged = exclusion_series(merged, fraction)

    ic_table = daily_ic_series(merged)
    labelled = ic_table.dropna(subset=["ic"])
    diagnostics = score_diagnostics(scores)
    turnover = turnover_series(merged)
    excess = excluded_excess(merged)

    ic_values = labelled["ic"] if len(labelled) else pd.Series(dtype=float)
    first_half, second_half = _half_split(ic_values)
    mean_ic = _mean(ic_values)
    hac_ir = (
        ic_ir_hac(ic_values.to_numpy(dtype=float))
        if len(ic_values) >= 2
        else None
    )
    win_rate = float((ic_values > 0).mean()) if len(ic_values) else None

    rules = prereg["decision_rules"]
    minimum_days = int(rules["minimum_labelled_days_for_decision"])
    pass_floor = float(prereg["reference_values_from_frozen_evaluation"][
        "mean_daily_spearman_ic"
    ]) * 0.5
    halt_checks = {
        "ic_non_positive": mean_ic is not None and mean_ic <= 0,
        "ic_below_negative_floor": mean_ic is not None and mean_ic < -pass_floor,
        "implausibly_large_ic": mean_ic is not None and abs(mean_ic) > 0.40,
        "any_day_fewer_than_500_names": bool(
            (diagnostics["names"] < MIN_NAMES_PER_DAY).any()
        ),
        "non_finite_predictions": bool((diagnostics["non_finite"] > 0).any()),
        "score_mean_out_of_band": bool(
            (
                (diagnostics["score_mean"] < 0.40)
                | (diagnostics["score_mean"] > 0.60)
            ).any()
        ),
        "score_std_out_of_band": bool(
            (
                (diagnostics["score_std"] < 0.02)
                | (diagnostics["score_std"] > 0.10)
            ).any()
        ),
    }
    pass_checks = {
        "ic_positive": mean_ic is not None and mean_ic > 0,
        "ic_above_half_of_evaluation": mean_ic is not None and mean_ic >= pass_floor,
        "win_rate_at_least_half": win_rate is not None and win_rate >= 0.50,
        # Each comparison is wrapped in bool() on purpose: numpy returns
        # np.bool_, which json.dumps cannot serialise and would silently
        # degrade to the string "True" through default=str.
        "halves_same_sign": bool(
            first_half is not None
            and second_half is not None
            and np.sign(first_half) == np.sign(second_half) == np.sign(mean_ic or 0.0)
        ),
    }

    if len(labelled) < minimum_days:
        verdict = "EXPLORATORY"
        verdict_reason = (
            "labelled days " + str(len(labelled)) + " < protocol minimum " + str(minimum_days)
        )
    elif any(halt_checks.values()):
        verdict = "HALT"
        verdict_reason = "halt condition: " + ", ".join(
            name for name, hit in halt_checks.items() if hit
        )
    elif all(pass_checks.values()):
        verdict = "PASS_CONTINUE"
        verdict_reason = "all pass conditions met"
    else:
        verdict = "INCONCLUSIVE"
        verdict_reason = "not all pass conditions met: " + ", ".join(
            name for name, ok in pass_checks.items() if not ok
        )

    pre_seam = labelled.loc[labelled["date"] <= SEAM_DATE, "ic"]
    post_seam = labelled.loc[labelled["date"] > SEAM_DATE, "ic"]
    payload = {
        "classification": "forward_evaluation",
        "protocol_file": str(prereg_path),
        "protocol_status": prereg["status"],
        "scores_file": str(scores_path),
        "labels_file": str(labels_path),
        "scoring_model_id": prereg["subject"]["scoring_model_id"],
        "window": {
            "declared": [
                prereg["observation_window"]["declared_start"],
                prereg["observation_window"]["declared_end"],
            ],
            "scored_days": int(scores["date"].nunique()),
            "labelled_days": int(len(labelled)),
            "dropped_days": int(ic_table["ic"].isna().sum()),
        },
        "primary_endpoint": {
            "name": "forward_daily_spearman_ic",
            "mean": mean_ic,
            "std": float(ic_values.std(ddof=1)) if len(ic_values) > 1 else None,
            "win_rate": win_rate,
            "hac_ic_ir": hac_ir,
            "first_half_mean": first_half,
            "second_half_mean": second_half,
            "pre_seam_mean": _mean(pre_seam),
            "post_seam_mean": _mean(post_seam),
            "pre_seam_days": int(len(pre_seam)),
            "post_seam_days": int(len(post_seam)),
        },
        "secondary_endpoints": {
            "excluded_group_forward_excess_mean": _mean(excess),
            "excluded_group_forward_excess_positive_share": (
                float((excess > 0).mean()) if len(excess) else None
            ),
            "excluded_group_forward_excess_days": int(len(excess)),
            "bottom_exclusion_turnover_mean": _mean(turnover),
            "bottom_exclusion_turnover_days": int(len(turnover)),
            "score_mean_range": [
                float(diagnostics["score_mean"].min()),
                float(diagnostics["score_mean"].max()),
            ],
            "score_std_range": [
                float(diagnostics["score_std"].min()),
                float(diagnostics["score_std"].max()),
            ],
            "industry_exposure": industry_exposure(merged),
        },
        "checks": {
            "pass_checks": pass_checks,
            "halt_checks": halt_checks,
            "pass_floor_ic": pass_floor,
            "minimum_labelled_days": minimum_days,
        },
        "verdict": verdict,
        "verdict_reason": verdict_reason,
        "verdict_is_exploratory": verdict == "EXPLORATORY",
        "coverage_cliff_disclosed": True,
        "post_seam_universe_is_not_investable": True,
        "production_authorized": False,
    }

    output = Path(out_dir)
    output.mkdir(parents=True, exist_ok=True)
    (output / "forward_evaluation.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str) + chr(10),
        encoding="utf-8",
    )
    ic_table.to_csv(output / "forward_daily_ic.csv", index=False, encoding="utf-8-sig")
    diagnostics.to_csv(output / "score_diagnostics.csv", index=False, encoding="utf-8-sig")
    turnover.rename("turnover").reset_index().rename(columns={"index": "date"}).to_csv(
        output / "exclusion_turnover.csv", index=False, encoding="utf-8-sig"
    )
    excess.rename("excluded_excess").reset_index().rename(
        columns={"index": "date"}
    ).to_csv(output / "excluded_excess.csv", index=False, encoding="utf-8-sig")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate forward scores under the protocol")
    parser.add_argument("--scores", default=str(DEFAULT_SCORES))
    parser.add_argument("--labels", default=str(DEFAULT_LABELS))
    parser.add_argument("--prereg", default=str(DEFAULT_PREREG))
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT))
    args = parser.parse_args()
    payload = evaluate(
        scores_path=args.scores,
        labels_path=args.labels,
        prereg_path=args.prereg,
        out_dir=args.out_dir,
    )
    print(json.dumps(
        {
            "verdict": payload["verdict"],
            "verdict_reason": payload["verdict_reason"],
            "labelled_days": payload["window"]["labelled_days"],
            "mean_ic": payload["primary_endpoint"]["mean"],
            "excluded_excess": payload["secondary_endpoints"][
                "excluded_group_forward_excess_mean"
            ],
        },
        ensure_ascii=False,
        indent=2,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
