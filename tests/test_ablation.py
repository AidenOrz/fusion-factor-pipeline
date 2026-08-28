from __future__ import annotations

import json

import pandas as pd

from bridge.ablation import _classify_delta, _group_decisions, _info_type_group, register_experiment


def test_compound_info_types_are_registered_in_broad_groups():
    assert _info_type_group("momentum_volatility", "quantgpt", "alpha_qg_f009") == "momentum"
    assert _info_type_group("momentum_reversal", "quantgpt", "alpha_qg_f008") == "momentum"
    assert _info_type_group("other", "benchmark", "gtja_182") == "correlation"
    assert _info_type_group("other", "industry_neutral", "alpha020") == "momentum"


def test_register_experiment_requires_matching_grouping(tmp_path):
    features = tmp_path / "features.json"
    grouping = tmp_path / "grouping.csv"
    features.write_text(json.dumps(["factor_a", "factor_b"]), encoding="utf-8")
    pd.DataFrame(
        {
            "factor": ["factor_a", "factor_b"],
            "source_factor": ["factor_a", "factor_b"],
            "family": ["alpha101", "gtja191"],
            "info_type": ["momentum", "volatility"],
            "info_type_group": ["momentum", "volatility"],
        }
    ).to_csv(grouping, index=False)
    result = register_experiment(features, grouping, tmp_path / "run")
    assert result["registered_before_results"] is True
    manifest = json.loads((tmp_path / "run" / "experiment_manifest.json").read_text(encoding="utf-8"))
    assert manifest["label_gap_trading_days"] == 20
    assert manifest["info_type_groups"] == ["momentum", "volatility"]
    assert [model["id"] for model in manifest["primary_models"]] == [
        "huber_b2_raw_rank", "huber_b2_neutral_rank"
    ]


def test_classify_delta_preserves_direction_and_noise_boundary():
    assert _classify_delta(0.003, 0.002) == ("removal_improves", "negative")
    assert _classify_delta(-0.003, 0.002) == ("removal_harms", "positive")
    assert _classify_delta(0.002, 0.002) == ("indistinguishable", "indistinguishable")


def test_group_decisions_do_not_call_improvement_a_retention_signal():
    summary = pd.DataFrame(
        [
            {"group_kind": "family", "group": "momentum", "model": "raw", "delta_ic": 0.01, "noise_std": 0.002},
            {"group_kind": "family", "group": "momentum", "model": "neutral", "delta_ic": 0.001, "noise_std": 0.002},
            {"group_kind": "family", "group": "alpha101", "model": "raw", "delta_ic": -0.01, "noise_std": 0.002},
            {"group_kind": "family", "group": "alpha101", "model": "neutral", "delta_ic": -0.001, "noise_std": 0.002},
        ]
    )
    decisions = _group_decisions(summary).set_index("group")
    assert decisions.loc["momentum", "decision"] == "removal_candidate_improves"
    assert decisions.loc["alpha101", "decision"] == "retain_positive_marginal"
