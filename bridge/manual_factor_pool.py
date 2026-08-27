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
from typing import Any, Sequence

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


def _parquet_columns(path: Path) -> list[str]:
    """Read a parquet schema without materialising a multi-GB factor file."""
    try:
        import pyarrow.parquet as pq

        return list(pq.ParquetFile(path).schema.names)
    except Exception:
        return list(pd.read_parquet(path, engine="auto").columns)


def _merge_factor_files(
    panel: pd.DataFrame,
    factor_paths: Sequence[Path | str],
    required_columns: Sequence[str],
) -> tuple[pd.DataFrame, list[str]]:
    """Attach requested external columns with explicit key joins.

    The full-market panel contains the base/v6 fields but not the complete
    Alpha101/GTJA191 output.  Reading only the requested columns and joining
    on ``(date, code)`` avoids positional alignment and keeps this materializer
    compatible with the selection panel contract.
    """
    needed = {str(column) for column in required_columns if str(column) not in panel.columns}
    if not needed:
        return panel, []
    paths = [_project_path(path) for path in factor_paths]
    if not paths:
        raise ValueError(
            "panel is missing selected factor columns and pool.source.factor_files is empty: "
            f"{sorted(needed)}"
        )

    file_columns: list[tuple[Path, list[str]]] = []
    unresolved = set(needed)
    for path in paths:
        if not path.is_file():
            raise FileNotFoundError(f"factor parquet does not exist: {path}")
        available = set(_parquet_columns(path))
        selected = sorted(unresolved.intersection(available))
        if selected:
            file_columns.append((path, selected))
            unresolved.difference_update(selected)
    if unresolved:
        raise ValueError(
            "external factor files do not contain required columns: "
            f"{sorted(unresolved)}"
        )

    keys = panel[["date", "code"]].copy()
    keys["date"] = pd.to_datetime(keys["date"], errors="raise")
    keys["code"] = keys["code"].astype(str).str.zfill(6)
    if keys.duplicated(["date", "code"]).any():
        raise ValueError("panel has duplicate (date, code) keys")

    try:
        import duckdb

        connection = duckdb.connect(database=":memory:")
        connection.register("pool_keys", keys)
        selections = ["k.date", "k.code"]
        joins: list[str] = []
        for index, (path, columns) in enumerate(file_columns):
            alias = f"f{index}"
            duplicate = connection.execute(
                "SELECT 1 FROM read_parquet(?) GROUP BY date, code "
                "HAVING COUNT(*) > 1 LIMIT 1",
                [str(path)],
            ).fetchall()
            if duplicate:
                connection.close()
                raise ValueError(f"factor parquet has duplicate (date, code) keys: {path}")
            joins.append(
                f"LEFT JOIN read_parquet(?) AS {alias} ON "
                f"CAST({alias}.date AS DATE) = CAST(k.date AS DATE) "
                f"AND LPAD(CAST({alias}.code AS VARCHAR), 6, '0') = CAST(k.code AS VARCHAR)"
            )
            selections.extend(f'{alias}."{column}" AS "{column}"' for column in columns)
        query = "SELECT " + ", ".join(selections) + " FROM pool_keys AS k " + " ".join(joins)
        factor = connection.execute(
            query, [str(path) for path, _ in file_columns]
        ).fetch_df()
        connection.close()
    except ImportError:
        factor = keys.copy()
        for path, columns in file_columns:
            source = pd.read_parquet(path, columns=["date", "code", *columns])
            source["date"] = pd.to_datetime(source["date"], errors="raise")
            source["code"] = source["code"].astype(str).str.zfill(6)
            if source.duplicated(["date", "code"]).any():
                raise ValueError(f"factor parquet has duplicate (date, code) keys: {path}")
            factor = factor.merge(
                source, on=["date", "code"], how="left", validate="one_to_one"
            )

    factor["date"] = pd.to_datetime(factor["date"], errors="raise")
    factor["code"] = factor["code"].astype(str).str.zfill(6)
    if factor.duplicated(["date", "code"]).any():
        raise ValueError("factor parquet files contain duplicate (date, code) keys")
    merged = panel.merge(
        factor, on=["date", "code"], how="left", validate="one_to_one", sort=False
    )
    return merged, sorted(needed)


def _validate_pool_scope(panel: pd.DataFrame, pool: dict[str, Any]) -> None:
    """Reject a B2 pool when it is accidentally pointed at a legacy panel."""
    scope = pool.get("scope")
    if not scope:
        return
    expected = {
        "panel_scope": "full_market",
        "universe": "b2",
        "target": "y_neutral_rank20_b2",
    }
    for key, value in expected.items():
        if scope.get(key) != value:
            raise ValueError(
                f"manual pool scope must use {key}={value!r}, got {scope.get(key)!r}"
            )
    required = {"panel_scope", "universe_b2", "y_neutral_rank20_b2"}
    missing = sorted(required.difference(panel.columns))
    if missing:
        raise ValueError(
            "manual pool requires the full-market B2 panel contract; "
            f"missing columns: {missing}"
        )
    scope_values = panel["panel_scope"].dropna().astype(str).str.lower().unique()
    if set(scope_values) != {"full_market"}:
        raise ValueError(
            "manual pool refuses non-full-market panel_scope values: "
            f"{sorted(scope_values)}"
        )


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
    approval_policy = payload.get("approval_policy", {})
    require_human_approval = bool(
        approval_policy.get("require_explicit_human_approval", False)
    )
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
        if require_human_approval and candidate.get("human_approved") is not True:
            raise ValueError(
                f"manual candidate {source!r} must carry human_approved=true"
            )
        decision = str(candidate.get("decision", "")).lower()
        if decision and decision not in {f"register_{direction}", "register"}:
            raise ValueError(
                f"manual candidate decision for {source!r} does not match {direction!r}: {decision!r}"
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
    explicitly_approved = {
        str(item["source_column"])
        for item in candidates
        if item.get("human_approved") is True
    }
    missing_review_rows = sorted(
        set(candidate_sources).difference(manual_required | explicitly_approved)
    )
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
    _validate_pool_scope(panel, pool)
    base_columns = feature_columns[: len(feature_columns) - len(candidates)]
    source_columns = [str(item["source_column"]) for item in candidates]
    factor_files = pool.get("source", {}).get("factor_files", [])
    if not isinstance(factor_files, list) or not all(
        isinstance(path, (str, Path)) for path in factor_files
    ):
        raise ValueError("pool.source.factor_files must be a list of paths")
    panel, joined_columns = _merge_factor_files(
        panel,
        factor_files,
        [*base_columns, *source_columns],
    )
    missing = sorted(set(source_columns).difference(panel.columns))
    missing_base = sorted(set(base_columns).difference(panel.columns))
    if missing or missing_base:
        raise ValueError(
            "manual pool sources missing after factor joins: "
            f"candidates={missing}, base_features={missing_base}"
        )
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
        "external_factor_files": [str(_project_path(path)) for path in factor_files],
        "external_columns_joined": joined_columns,
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
