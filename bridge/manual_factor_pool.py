"""Materialize an explicitly human-approved factor candidate pool.

This module deliberately does not infer or repair directions.  It only applies
the signed transforms listed in a manual pool configuration when the caller
explicitly invokes the CLI.  The original panel and expression configuration
remain unchanged.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_POOL = PROJECT_ROOT / "config" / "factor_pool_v2_manual_review.json"
DEFAULT_PANEL = PROJECT_ROOT / "data" / "panel_fusion_full.parquet"
DEFAULT_OUT_PANEL = PROJECT_ROOT / "data" / "panel_fusion_v2_manual_review.parquet"
DEFAULT_FEATURES = PROJECT_ROOT / "config" / "factor_pool_v2_manual_review_features.json"


def _project_path(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else PROJECT_ROOT / path


def load_pool(path: Path | str = DEFAULT_POOL) -> dict[str, Any]:
    pool_path = Path(path)
    payload = json.loads(pool_path.read_text(encoding="utf-8"))
    activation = payload.get("activation", {})
    if activation.get("explicit_cli_only") is not True:
        raise ValueError("manual factor pool must require explicit CLI activation")
    if activation.get("automatic_direction_flip") is not False:
        raise ValueError("manual factor pool must prohibit automatic direction flips")
    if activation.get("raw_expression_config_unchanged") is not True:
        raise ValueError("manual factor pool must leave raw expression config unchanged")

    candidates = payload.get("manual_candidates")
    if not isinstance(candidates, list) or not candidates:
        raise ValueError("manual factor pool must contain manual_candidates")
    output_columns: set[str] = set()
    source_columns: set[str] = set()
    for candidate in candidates:
        required = {"source_column", "output_column", "direction", "transform"}
        missing = required.difference(candidate)
        if missing:
            raise ValueError(f"manual candidate missing fields: {sorted(missing)}")
        source = str(candidate["source_column"])
        output = str(candidate["output_column"])
        direction = str(candidate["direction"]).lower()
        transform = str(candidate["transform"]).lower()
        if direction not in {"long", "short"}:
            raise ValueError(f"unsupported manual direction for {source!r}: {direction!r}")
        expected_transform = "identity" if direction == "long" else "negate"
        if transform != expected_transform:
            raise ValueError(
                f"manual transform for {source!r} must be {expected_transform!r}, got {transform!r}"
            )
        if source in source_columns:
            raise ValueError(f"duplicate manual source column: {source}")
        if output in output_columns:
            raise ValueError(f"duplicate manual output column: {output}")
        source_columns.add(source)
        output_columns.add(output)
    return payload


def resolve_feature_columns(
    pool: dict[str, Any],
    *,
    pool_path: Path | str = DEFAULT_POOL,
) -> tuple[list[str], list[dict[str, Any]], dict[str, int]]:
    source = pool.get("source", {})
    selected_path = _project_path(source.get("selected_features_file", ""))
    review_path = _project_path(source.get("direction_review_file", ""))
    if not selected_path.is_file() or not review_path.is_file():
        raise FileNotFoundError(
            f"manual pool references missing selection artifacts: {selected_path}, {review_path}"
        )
    selected = json.loads(selected_path.read_text(encoding="utf-8"))
    if not isinstance(selected, list) or not all(isinstance(item, str) for item in selected):
        raise ValueError("selected_features_file must contain a JSON list of strings")
    review = pd.read_csv(review_path, encoding="utf-8-sig")
    required_review_columns = {"factor", "manual_review_required"}
    if not required_review_columns.issubset(review.columns):
        raise ValueError(f"direction review missing {sorted(required_review_columns)}")
    manual_required = set(
        review.loc[
            review["manual_review_required"].astype(str).str.lower().eq("true"),
            "factor",
        ].astype(str)
    )
    policy = pool.get("base_feature_policy", {})
    excluded_statuses = {
        str(status) for status in policy.get("excluded_review_statuses", [])
    }
    if excluded_statuses and "review_status" in review.columns:
        manual_required.update(
            review.loc[review["review_status"].astype(str).isin(excluded_statuses), "factor"].astype(str)
        )
    base = [column for column in selected if column not in manual_required]
    candidates = list(pool["manual_candidates"])
    candidate_sources = [str(item["source_column"]) for item in candidates]
    missing_review_rows = sorted(set(candidate_sources).difference(manual_required))
    if missing_review_rows:
        raise ValueError(
            "manual candidates must correspond to manual_review_required rows: "
            f"{missing_review_rows}"
        )
    feature_columns = [*base, *[str(item["output_column"]) for item in candidates]]
    if len(feature_columns) != len(set(feature_columns)):
        raise ValueError("resolved manual feature columns are not unique")
    stats = {
        "selected_feature_count": len(selected),
        "manual_review_required_count": len(manual_required),
        "base_feature_count": len(base),
        "manual_candidate_count": len(candidates),
        "resolved_feature_count": len(feature_columns),
    }
    return feature_columns, candidates, stats


def materialize_manual_pool(
    panel_path: Path | str = DEFAULT_PANEL,
    pool_path: Path | str = DEFAULT_POOL,
    out_panel: Path | str = DEFAULT_OUT_PANEL,
    features_path: Path | str = DEFAULT_FEATURES,
) -> dict[str, Any]:
    pool = load_pool(pool_path)
    feature_columns, candidates, stats = resolve_feature_columns(pool, pool_path=pool_path)
    panel = pd.read_parquet(panel_path)
    source_columns = [str(item["source_column"]) for item in candidates]
    missing = sorted(set(source_columns).difference(panel.columns))
    if missing:
        raise ValueError(f"manual factor sources missing from panel: {missing}")
    for candidate in candidates:
        source = str(candidate["source_column"])
        output = str(candidate["output_column"])
        values = pd.to_numeric(panel[source], errors="coerce")
        if str(candidate["transform"]).lower() == "negate":
            values = -values
        panel[output] = values.replace([np.inf, -np.inf], np.nan)

    out_panel = Path(out_panel)
    features_path = Path(features_path)
    out_panel.parent.mkdir(parents=True, exist_ok=True)
    features_path.parent.mkdir(parents=True, exist_ok=True)
    panel.to_parquet(out_panel, index=False)
    features_path.write_text(json.dumps(feature_columns, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {
        **stats,
        "panel_path": str(out_panel),
        "features_path": str(features_path),
        "manual_output_columns": [str(item["output_column"]) for item in candidates],
        "automatic_direction_flip": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Materialize an explicit manual factor pool")
    parser.add_argument("--pool", default=str(DEFAULT_POOL))
    parser.add_argument("--panel", default=str(DEFAULT_PANEL))
    parser.add_argument("--out-panel", default=str(DEFAULT_OUT_PANEL))
    parser.add_argument("--features-out", default=str(DEFAULT_FEATURES))
    args = parser.parse_args()
    result = materialize_manual_pool(args.panel, args.pool, args.out_panel, args.features_out)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
