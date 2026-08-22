from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import bridge.factor_selection as factor_selection
from bridge.factor_selection import (
    apply_direction_selection_policy,
    apply_static_selection_policy,
    apply_watch_only_policy,
    build_direction_adjudication_queue,
    build_direction_review,
    build_factor_coverage_diagnostics,
    prepare_selection_panel,
    validate_selection_contract,
)


def _panel(days: int = 70) -> pd.DataFrame:
    rows = []
    dates = pd.bdate_range("2024-01-01", periods=days)
    for date in dates:
        for index in range(6):
            rows.append(
                {
                    "date": date,
                    "code": f"{index + 1:06d}",
                    "panel_scope": "full_market",
                    "universe_b1": True,
                    "universe_b2": index < 4,
                    "universe_b3": index < 2,
                    "universe_version": "test-v1",
                    "y_neutral_rank20_b2": float(index),
                    "alpha_qg_mom_20": float(index),
                }
            )
    return pd.DataFrame(rows)


def test_selection_contract_rejects_missing_scope_and_events():
    panel = _panel()
    missing_scope = panel.drop(columns="panel_scope")
    with pytest.raises(ValueError, match="panel_scope"):
        validate_selection_contract(
            missing_scope, universe="b2", target="y_neutral_rank20_b2"
        )

    events = panel.assign(panel_scope="events")
    with pytest.raises(ValueError, match="full_market"):
        validate_selection_contract(
            events, universe="b2", target="y_neutral_rank20_b2"
        )


def test_prepare_selection_panel_filters_b2_and_key_joins_external_factor(tmp_path: Path):
    panel = _panel()
    factor_path = tmp_path / "gtja.parquet"
    factor = panel[["date", "code"]].copy()
    factor["gtja_094"] = factor["code"].astype(int)
    factor.to_parquet(factor_path, index=False)

    selected, _, _ = prepare_selection_panel(
        panel,
        universe="b2",
        target="y_neutral_rank20_b2",
        factor_paths=(factor_path,),
    )

    assert selected["code"].nunique() == 4
    assert selected["gtja_094"].notna().all()
    assert selected["gtja_094"].equals(selected["code"].astype(int))


def test_external_factor_duplicate_keys_are_rejected(tmp_path: Path):
    panel = _panel()
    factor_path = tmp_path / "duplicate.parquet"
    factor = panel[["date", "code"]].copy()
    factor["gtja_094"] = 1.0
    factor = pd.concat([factor, factor.iloc[[0]]], ignore_index=True)
    factor.to_parquet(factor_path, index=False)

    with pytest.raises(ValueError, match="duplicate"):
        prepare_selection_panel(
            panel,
            universe="b2",
            target="y_neutral_rank20_b2",
            factor_paths=(factor_path,),
        )


def test_low_distinctness_is_reported_without_rescuing_factor():
    panel = _panel()
    panel["gtja_094"] = np.where(panel["code"].isin(["000001", "000002"]), 1.0, -1.0)
    diagnostics = build_factor_coverage_diagnostics(
        panel.loc[panel["universe_b2"]],
        ["gtja_094"],
        target="y_neutral_rank20_b2",
    )
    row = diagnostics.iloc[0]
    assert row["coverage"] == 1.0
    assert row["median_daily_unique"] == 2.0
    assert row["diagnosis"] == "low_cross_sectional_distinctness"


def test_watch_only_upgrade_is_human_review_only():
    review = pd.DataFrame(
        {
            "factor": ["alpha004", "gtja_183", "other"],
            "recommended_direction": ["short", "long", "long"],
            "recent_ic_mean": [0.04, 0.02, 0.05],
            "historical_ic_mean": [0.06, -0.04, 0.05],
            "review_status": ["stable", "stable", "stable"],
            "manual_review_required": [False, False, False],
        }
    )
    result = apply_watch_only_policy(review, ["alpha004", "gtja_183"])

    assert result.loc[result["factor"] == "alpha004", "review_status"].item() == (
        "pre_registered_candidate"
    )
    assert result.loc[result["factor"] == "gtja_183", "review_status"].item() == "watch_only"
    assert result["recommended_direction"].tolist() == ["short", "long", "long"]
    assert result["manual_review_required"].tolist() == [True, False, False]


def test_static_policy_drops_insufficient_and_weak_ic_before_redundancy():
    ic_table = pd.DataFrame(
        {
            "factor": ["strong", "weak", "missing_ic", "low_distinctness"],
            "ic_mean": [0.03, 0.009, np.nan, 0.20],
        }
    )
    diagnostics = pd.DataFrame(
        {
            "factor": ["strong", "weak", "missing_ic", "low_distinctness"],
            "diagnosis": [
                "adequate_for_daily_ic",
                "adequate_for_daily_ic",
                "sparse_row_coverage",
                "low_cross_sectional_distinctness",
            ],
        }
    )

    result = apply_static_selection_policy(ic_table, diagnostics).set_index("factor")

    assert bool(result.loc["strong", "static_selection_eligible"])
    assert result.loc["weak", "static_exclusion_reason"] == "abs_ic_below_0.01"
    assert result.loc["missing_ic", "static_exclusion_reason"] == "insufficient_daily_ic_days"
    assert result.loc["low_distinctness", "static_exclusion_reason"] == (
        "insufficient_cross_sectional_data"
    )


def test_low_amplitude_direction_reversal_is_not_manual_regime_review(monkeypatch):
    monkeypatch.setattr(
        factor_selection,
        "daily_ic_series",
        lambda *_args, **_kwargs: pd.Series([-0.008] * 10 + [0.005] * 10),
    )

    result = build_direction_review(
        pd.DataFrame(), ["noise_factor"], recent_window=10, min_days=5
    ).iloc[0]

    assert bool(result["raw_regime_reversal"])
    assert not bool(result["regime_amplitude_sufficient"])
    assert result["review_status"] == "no_significant_signal"
    assert not bool(result["manual_review_required"])


def test_material_direction_reversal_requires_manual_regime_review(monkeypatch):
    monkeypatch.setattr(
        factor_selection,
        "daily_ic_series",
        lambda *_args, **_kwargs: pd.Series([-0.04] * 10 + [0.03] * 10),
    )

    result = build_direction_review(
        pd.DataFrame(), ["regime_factor"], recent_window=10, min_days=5
    ).iloc[0]

    assert bool(result["raw_regime_reversal"])
    assert bool(result["regime_amplitude_sufficient"])
    assert result["review_status"] == "regime_sensitive"
    assert bool(result["manual_review_required"])


def test_direction_policy_excludes_unresolved_factors_and_exports_adjudication():
    ic_table = pd.DataFrame(
        {
            "factor": ["stable", "alpha004", "alpha055", "watch_factor", "weak_signal", "regime_observation"],
            "static_selection_eligible": [True, True, True, True, True, False],
            "static_exclusion_reason": ["eligible", "eligible", "eligible", "eligible", "eligible", "abs_ic_below_0.01"],
        }
    )
    direction_review = pd.DataFrame(
        {
            "factor": ["stable", "alpha004", "alpha055", "watch_factor", "weak_signal", "regime_observation"],
            "review_status": ["stable", "watch_only", "stable", "watch_only", "no_significant_signal", "regime_sensitive"],
            "manual_review_required": [False, False, False, False, False, True],
            "watch_only": [False, True, False, True, False, False],
            "recommended_direction": ["long", "short", "long", "short", "none", "short"],
        }
    )

    policy = apply_direction_selection_policy(ic_table, direction_review)
    rows = policy.set_index("factor")

    assert bool(rows.loc["stable", "candidate_eligible"])
    assert not bool(rows.loc["alpha004", "candidate_eligible"])
    assert not bool(rows.loc["alpha055", "candidate_eligible"])
    assert rows.loc["watch_factor", "candidate_exclusion_reason"] == "watch_only_not_triggered"
    assert not bool(rows.loc["weak_signal", "candidate_eligible"])
    assert not bool(rows.loc["regime_observation", "direction_adjudication_required"])
    assert bool(rows.loc["regime_observation", "direction_research_observation"])
    assert bool(rows.loc["alpha055", "legacy_review_conflict"])
    assert not bool(rows.loc["weak_signal", "direction_adjudication_required"])

    queue = build_direction_adjudication_queue(direction_review, policy)
    assert set(queue["factor"]) == {"alpha004", "alpha055", "regime_observation"}
    assert queue.loc[queue["factor"] == "alpha055", "suggested_direction"].item() == "long"
    assert queue.loc[queue["factor"] == "regime_observation", "review_scope"].item() == (
        "research_observation_not_candidate"
    )
