"""Audit family/info-type LOFO overlap and build a versioned candidate pool.

This module only materializes a reviewable candidate list.  It never mutates
the adjudicated 2026-08-27 configuration and never infers or flips direction.
The registered rule is intentionally explicit: manual short exemptions take
precedence, then family and info-type removal candidates are unioned.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FEATURES = PROJECT_ROOT / "config" / "factor_pool_b2_adjudicated_2026-08-27_features.json"
DEFAULT_GROUPING = PROJECT_ROOT / "docs" / "B2_INFO_TYPE_REVIEW_2026-08-27.csv"
DEFAULT_DECISIONS = PROJECT_ROOT / "runs" / "factor_ablation_v4_b2_huber_cross_label_2026-08-28" / "lofo_group_decisions.csv"
DEFAULT_AUDIT = PROJECT_ROOT / "docs" / "B2_CROSS_OVERLAP_AUDIT_2026-08-28.csv"
DEFAULT_CANDIDATE_FEATURES = PROJECT_ROOT / "config" / "factor_pool_b2_post_lofo_candidate_2026-08-28_features.json"
DEFAULT_CANDIDATE_GROUPING = PROJECT_ROOT / "docs" / "B2_POST_LOFO_CANDIDATE_GROUPING_2026-08-28.csv"
DEFAULT_CANDIDATE_CONFIG = PROJECT_ROOT / "config" / "factor_pool_b2_post_lofo_candidate_2026-08-28.json"

MANUAL_EXEMPTIONS = {
    "manual__alpha_qg_f001__short": "人工批准的 f001 short：momentum 整组删除不得覆盖已裁决方向",
    "manual__alpha_qg_f008__short": "人工批准的 f008 short：momentum 整组删除不得覆盖已裁决方向",
}

EXPLICIT_NON_EXEMPTIONS = {
    "manual__gtja_090__long": (
        "维持出局且不新增豁免：该因子 B2 单因子 IC=0.0351、符号一致性=1.00、"
        "recent IC=0.0291，但只有单因子证据；所属 gtja191 family 在两条 Huber "
        "主线上移除后均显著改善。按‘单因子强度不等于模型边际贡献’原则，组级反证优先"
    ),
}

REMOVAL_DECISIONS = {
    "removal_candidate_improves",
    "removal_candidate_indistinguishable",
}


def _read_features(path: str | Path) -> list[str]:
    values = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(values, list) or not values or not all(isinstance(value, str) for value in values):
        raise ValueError("features file must contain a non-empty JSON list of strings")
    if len(values) != len(set(values)):
        raise ValueError("features file contains duplicate factors")
    return values


def _portable_path(path: str | Path) -> str:
    resolved = Path(path).resolve()
    try:
        return resolved.relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return str(resolved)


def _read_grouping(path: str | Path, features: list[str]) -> pd.DataFrame:
    frame = pd.read_csv(path, encoding="utf-8-sig", dtype=str).fillna("")
    required = {"factor", "source_factor", "family", "info_type_group"}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"grouping file missing columns: {missing}")
    if frame["factor"].duplicated().any() or set(frame["factor"]) != set(features):
        raise ValueError("grouping factors must exactly match the adjudicated feature list")
    return frame


def _read_decisions(path: str | Path) -> pd.DataFrame:
    frame = pd.read_csv(path, encoding="utf-8-sig", dtype=str).fillna("")
    required = {"group_kind", "group", "decision"}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"LOFO decisions missing columns: {missing}")
    key = frame["group_kind"].astype(str) + "::" + frame["group"].astype(str)
    if key.duplicated().any():
        raise ValueError("LOFO decisions contain duplicate group keys")
    return frame.set_index(["group_kind", "group"])


def _decision(decisions: pd.DataFrame, kind: str, group: str) -> str:
    try:
        return str(decisions.loc[(kind, group), "decision"])
    except KeyError as exc:
        raise ValueError(f"missing LOFO decision for {kind}={group}") from exc


def build_cross_overlap_audit(
    features_path: str | Path = DEFAULT_FEATURES,
    grouping_path: str | Path = DEFAULT_GROUPING,
    decisions_path: str | Path = DEFAULT_DECISIONS,
) -> pd.DataFrame:
    """Return one auditable rule application row for every registered factor."""

    features = _read_features(features_path)
    grouping = _read_grouping(grouping_path, features)
    decisions = _read_decisions(decisions_path)
    family_candidates = {
        group for group in grouping["family"].unique()
        if _decision(decisions, "family", str(group)) in REMOVAL_DECISIONS
    }
    info_candidates = {
        group for group in grouping["info_type_group"].unique()
        if _decision(decisions, "info_type_group", str(group)) in REMOVAL_DECISIONS
    }
    rows: list[dict[str, Any]] = []
    for record in grouping.to_dict(orient="records"):
        factor = record["factor"]
        family = record["family"]
        info_group = record["info_type_group"]
        family_decision = _decision(decisions, "family", family)
        info_decision = _decision(decisions, "info_type_group", info_group)
        family_action = "remove_candidate" if family in family_candidates else "retain"
        info_action = "remove_candidate" if info_group in info_candidates else "retain"
        exemption = MANUAL_EXEMPTIONS.get(factor, "")
        if exemption:
            final_action = "retain"
            reason = exemption
        elif factor in EXPLICIT_NON_EXEMPTIONS:
            final_action = "remove"
            reason = EXPLICIT_NON_EXEMPTIONS[factor]
        elif family_action == "remove_candidate" and info_action == "remove_candidate":
            final_action = "remove"
            reason = f"family={family} 与 info_type_group={info_group} 均为预注册移除候选，按并集删除"
        elif family_action == "remove_candidate":
            final_action = "remove"
            reason = f"family={family} 为预注册移除候选；info_type_group={info_group} 保留不覆盖 family 结论"
        elif info_action == "remove_candidate":
            final_action = "remove"
            reason = f"info_type_group={info_group} 为预注册移除候选；family={family} 保留不覆盖 info-type 结论"
        else:
            final_action = "retain"
            reason = "family 与 info_type_group 均无移除候选证据"
        rows.append({
            "factor": factor,
            "source_factor": record["source_factor"],
            "family": family,
            "info_type": record.get("info_type", ""),
            "info_type_group": info_group,
            "family_decision": family_decision,
            "info_decision": info_decision,
            "family_action": family_action,
            "info_action": info_action,
            "manual_exemption": exemption,
            "final_action": final_action,
            "reason": reason,
        })
    return pd.DataFrame(rows)


def materialize_candidate_pool(
    audit: pd.DataFrame,
    *,
    features_path: str | Path = DEFAULT_FEATURES,
    audit_path: str | Path = DEFAULT_AUDIT,
    candidate_features_path: str | Path = DEFAULT_CANDIDATE_FEATURES,
    candidate_grouping_path: str | Path = DEFAULT_CANDIDATE_GROUPING,
    candidate_config_path: str | Path = DEFAULT_CANDIDATE_CONFIG,
) -> dict[str, Any]:
    """Write audit, 35-factor list, candidate grouping, and metadata config."""

    features = _read_features(features_path)
    if audit["factor"].tolist() != features:
        raise ValueError("audit order/content must exactly match the source feature list")
    candidate = audit.loc[audit["final_action"].eq("retain"), "factor"].tolist()
    removed = audit.loc[audit["final_action"].eq("remove"), "factor"].tolist()
    if len(candidate) + len(removed) != len(features):
        raise ValueError("audit actions do not partition the source feature list")
    audit_path = Path(audit_path)
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    audit.to_csv(audit_path, index=False, encoding="utf-8-sig")
    candidate_features_path = Path(candidate_features_path)
    candidate_features_path.parent.mkdir(parents=True, exist_ok=True)
    candidate_features_path.write_text(json.dumps(candidate, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    candidate_grouping_path = Path(candidate_grouping_path)
    candidate_grouping_path.parent.mkdir(parents=True, exist_ok=True)
    audit.loc[audit["final_action"].eq("retain"), ["factor", "source_factor", "family", "info_type", "info_type_group"]].to_csv(
        candidate_grouping_path, index=False, encoding="utf-8-sig"
    )
    candidate_config = {
        "schema_version": 1,
        "status": "post_lofo_candidate_pool_not_frozen",
        "decision_date": "2026-08-28",
        "source_config": "config/factor_pool_b2_adjudicated_2026-08-27.json",
        "source_features_file": _portable_path(features_path),
        "candidate_features_file": _portable_path(candidate_features_path),
        "cross_overlap_audit_file": _portable_path(audit_path),
        "candidate_grouping_file": _portable_path(candidate_grouping_path),
        "panel_scope": "full_market",
        "universe": "b2",
        "selection_period": "2023-06-30..2024-09-12",
        "label_gap_trading_days": 20,
        "feature_count": len(candidate),
        "removed_count": len(removed),
        "removed_family_candidates": sorted(audit.loc[(audit["family_action"] == "remove_candidate"), "family"].unique().tolist()),
        "removed_info_type_candidates": sorted(audit.loc[(audit["info_action"] == "remove_candidate"), "info_type_group"].unique().tolist()),
        "manual_exemptions": sorted(MANUAL_EXEMPTIONS),
        "explicit_non_exemptions": sorted(EXPLICIT_NON_EXEMPTIONS),
        "rule_priority": ["manual_exemption", "family_or_info_type_removal_union", "retain"],
        "confirmation_status": "pending_selection_only_confirmation",
        "automatic_direction_flip": False,
        "production_config_mutation": False,
        "failure_policy": "one registered put-back of the culprit family, then return to descriptive stage",
    }
    candidate_config_path = Path(candidate_config_path)
    candidate_config_path.parent.mkdir(parents=True, exist_ok=True)
    candidate_config_path.write_text(json.dumps(candidate_config, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {
        "audit_file": str(audit_path.resolve()),
        "candidate_features_file": str(candidate_features_path.resolve()),
        "candidate_grouping_file": str(candidate_grouping_path.resolve()),
        "candidate_config_file": str(candidate_config_path.resolve()),
        "feature_count": len(candidate),
        "removed_count": len(removed),
        "manual_exemptions": sorted(MANUAL_EXEMPTIONS),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the B2 family/info-type overlap audit")
    parser.add_argument("--features", default=str(DEFAULT_FEATURES))
    parser.add_argument("--grouping", default=str(DEFAULT_GROUPING))
    parser.add_argument("--decisions", default=str(DEFAULT_DECISIONS))
    parser.add_argument("--audit", default=str(DEFAULT_AUDIT))
    parser.add_argument("--candidate-features", default=str(DEFAULT_CANDIDATE_FEATURES))
    parser.add_argument("--candidate-grouping", default=str(DEFAULT_CANDIDATE_GROUPING))
    parser.add_argument("--candidate-config", default=str(DEFAULT_CANDIDATE_CONFIG))
    args = parser.parse_args()
    audit = build_cross_overlap_audit(args.features, args.grouping, args.decisions)
    result = materialize_candidate_pool(
        audit,
        features_path=args.features,
        audit_path=args.audit,
        candidate_features_path=args.candidate_features,
        candidate_grouping_path=args.candidate_grouping,
        candidate_config_path=args.candidate_config,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
