from __future__ import annotations

import json

import pandas as pd

from bridge.factor_audit import build_cross_overlap_audit, materialize_candidate_pool


def _fixtures(tmp_path):
    features = [
        "manual__alpha_qg_f001__short",
        "manual__alpha_qg_f008__short",
        "alpha_qg_f009",
        "manual__alpha_qg_corr_pv_10__short",
        "keep_factor",
    ]
    features_path = tmp_path / "features.json"
    features_path.write_text(json.dumps(features), encoding="utf-8")
    grouping = pd.DataFrame(
        {
            "factor": features,
            "source_factor": features,
            "family": ["llm", "llm", "llm", "starter", "alpha101"],
            "info_type": ["momentum", "momentum", "momentum_volatility", "correlation", "reversal"],
            "info_type_group": ["momentum", "momentum", "momentum", "correlation", "reversal"],
        }
    )
    grouping_path = tmp_path / "grouping.csv"
    grouping.to_csv(grouping_path, index=False)
    decisions = pd.DataFrame(
        {
            "group_kind": ["family", "family", "family", "info_type_group", "info_type_group", "info_type_group"],
            "group": ["alpha101", "llm", "starter", "correlation", "momentum", "reversal"],
            "decision": ["retain_positive_marginal", "retain_positive_marginal", "removal_candidate_indistinguishable", "retain_positive_marginal", "removal_candidate_improves", "retain_positive_marginal"],
        }
    )
    decisions_path = tmp_path / "decisions.csv"
    decisions.to_csv(decisions_path, index=False)
    return features_path, grouping_path, decisions_path, features


def test_cross_overlap_audit_prioritizes_manual_short_exemptions(tmp_path):
    features_path, grouping_path, decisions_path, _ = _fixtures(tmp_path)
    audit = build_cross_overlap_audit(features_path, grouping_path, decisions_path)
    rows = audit.set_index("factor")
    assert rows.loc["manual__alpha_qg_f001__short", "final_action"] == "retain"
    assert rows.loc["manual__alpha_qg_f008__short", "final_action"] == "retain"
    assert rows.loc["alpha_qg_f009", "final_action"] == "remove"
    assert rows.loc["manual__alpha_qg_corr_pv_10__short", "final_action"] == "remove"
    assert rows.loc["keep_factor", "final_action"] == "retain"


def test_materialize_candidate_pool_preserves_source_order_and_is_versioned(tmp_path):
    features_path, grouping_path, decisions_path, features = _fixtures(tmp_path)
    audit = build_cross_overlap_audit(features_path, grouping_path, decisions_path)
    result = materialize_candidate_pool(
        audit,
        features_path=features_path,
        audit_path=tmp_path / "audit.csv",
        candidate_features_path=tmp_path / "candidate.json",
        candidate_grouping_path=tmp_path / "candidate_grouping.csv",
        candidate_config_path=tmp_path / "candidate_config.json",
    )
    candidate = json.loads((tmp_path / "candidate.json").read_text(encoding="utf-8"))
    assert candidate == [features[0], features[1], features[4]]
    assert result["feature_count"] == 3
    config = json.loads((tmp_path / "candidate_config.json").read_text(encoding="utf-8"))
    assert config["status"] == "post_lofo_candidate_pool_not_frozen"
    assert config["production_config_mutation"] is False
