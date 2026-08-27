import json

import pandas as pd
import pytest

from bridge.manual_factor_pool import load_pool, materialize_manual_pool, resolve_feature_columns


def _write_inputs(tmp_path):
    selected = ["stable_factor", "alpha_qg_f001", "alpha_qg_f005", "alpha_qg_f006", "flagged_factor"]
    selected_path = tmp_path / "selected.json"
    selected_path.write_text(json.dumps(selected), encoding="utf-8")
    review = pd.DataFrame(
        {
            "factor": ["stable_factor", "alpha_qg_f001", "alpha_qg_f005", "alpha_qg_f006", "flagged_factor"],
            "manual_review_required": [False, True, True, True, True],
        }
    )
    review_path = tmp_path / "review.csv"
    review.to_csv(review_path, index=False)
    pool = {
        "activation": {
            "explicit_cli_only": True,
            "automatic_direction_flip": False,
            "raw_expression_config_unchanged": True,
        },
        "source": {
            "selected_features_file": str(selected_path),
            "direction_review_file": str(review_path),
        },
        "manual_candidates": [
            {
                "source_column": "alpha_qg_f001",
                "output_column": "manual__f001__short",
                "direction": "short",
                "transform": "negate",
            },
            {
                "source_column": "alpha_qg_f005",
                "output_column": "manual__f005__short",
                "direction": "short",
                "transform": "negate",
            },
            {
                "source_column": "alpha_qg_f006",
                "output_column": "manual__f006__short",
                "direction": "short",
                "transform": "negate",
            },
        ],
    }
    pool_path = tmp_path / "pool.json"
    pool_path.write_text(json.dumps(pool), encoding="utf-8")
    return pool_path


def test_manual_pool_requires_explicit_non_automatic_activation(tmp_path):
    pool_path = _write_inputs(tmp_path)
    pool = load_pool(pool_path)
    columns, candidates, stats = resolve_feature_columns(pool, pool_path=pool_path)
    assert columns == [
        "stable_factor",
        "manual__f001__short",
        "manual__f005__short",
        "manual__f006__short",
    ]
    assert len(candidates) == 3
    assert stats["manual_review_required_count"] == 4


def test_manual_pool_materializes_only_explicit_signed_columns(tmp_path):
    pool_path = _write_inputs(tmp_path)
    panel_path = tmp_path / "panel.parquet"
    pd.DataFrame(
        {
            "date": pd.to_datetime(["2024-01-01", "2024-01-02"]),
            "stable_factor": [1.0, 2.0],
            "alpha_qg_f001": [0.25, -0.5],
            "alpha_qg_f005": [1.0, 2.0],
            "alpha_qg_f006": [3.0, 4.0],
        }
    ).to_parquet(panel_path, index=False)
    out_panel = tmp_path / "out.parquet"
    features_path = tmp_path / "features.json"
    result = materialize_manual_pool(panel_path, pool_path, out_panel, features_path)
    output = pd.read_parquet(out_panel)
    assert output["manual__f001__short"].tolist() == [-0.25, 0.5]
    assert "flagged_factor" not in result["manual_output_columns"]
    assert json.loads(features_path.read_text(encoding="utf-8"))[-3:] == [
        "manual__f001__short",
        "manual__f005__short",
        "manual__f006__short",
    ]


def test_manual_pool_rejects_automatic_flip(tmp_path):
    pool_path = _write_inputs(tmp_path)
    payload = json.loads(pool_path.read_text(encoding="utf-8"))
    payload["activation"]["automatic_direction_flip"] = True
    pool_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="automatic direction flips"):
        load_pool(pool_path)


def test_manual_pool_joins_external_features_by_key(tmp_path):
    selected_path = tmp_path / "selected.json"
    selected_path.write_text(json.dumps(["stable_factor"]), encoding="utf-8")
    review_path = tmp_path / "review.csv"
    pd.DataFrame(
        {
            "factor": ["stable_factor", "external_factor"],
            "manual_review_required": [False, False],
        }
    ).to_csv(review_path, index=False)
    factor_path = tmp_path / "external.parquet"
    pd.DataFrame(
        {
            "date": pd.to_datetime(["2024-01-02", "2024-01-01"]),
            "code": ["000002", "000001"],
            "external_factor": [20.0, 10.0],
        }
    ).to_parquet(factor_path, index=False)
    pool_path = tmp_path / "pool.json"
    pool_path.write_text(
        json.dumps(
            {
                "activation": {
                    "explicit_cli_only": True,
                    "automatic_direction_flip": False,
                    "raw_expression_config_unchanged": True,
                },
                "approval_policy": {"require_explicit_human_approval": True},
                "source": {
                    "selected_features_file": str(selected_path),
                    "direction_review_file": str(review_path),
                    "factor_files": [str(factor_path)],
                },
                "manual_candidates": [
                    {
                        "source_column": "external_factor",
                        "output_column": "manual__external_factor__long",
                        "direction": "long",
                        "transform": "identity",
                        "human_approved": True,
                        "decision": "register_long",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    panel_path = tmp_path / "panel.parquet"
    pd.DataFrame(
        {
            "date": pd.to_datetime(["2024-01-01", "2024-01-02"]),
            "code": ["000001", "000002"],
            "stable_factor": [1.0, 2.0],
        }
    ).to_parquet(panel_path, index=False)
    out_panel = tmp_path / "out.parquet"
    features_path = tmp_path / "features.json"

    result = materialize_manual_pool(panel_path, pool_path, out_panel, features_path)

    output = pd.read_parquet(out_panel).sort_values(["date", "code"])
    assert output["manual__external_factor__long"].tolist() == [10.0, 20.0]
    assert result["external_columns_joined"] == ["external_factor"]


def test_manual_pool_rejects_duplicate_external_keys(tmp_path):
    selected_path = tmp_path / "selected.json"
    selected_path.write_text(json.dumps(["stable_factor"]), encoding="utf-8")
    review_path = tmp_path / "review.csv"
    pd.DataFrame(
        {"factor": ["stable_factor"], "manual_review_required": [False]}
    ).to_csv(review_path, index=False)
    factor_path = tmp_path / "external.parquet"
    pd.DataFrame(
        {
            "date": pd.to_datetime(["2024-01-01", "2024-01-01"]),
            "code": ["000001", "000001"],
            "external_factor": [1.0, 2.0],
        }
    ).to_parquet(factor_path, index=False)
    pool_path = tmp_path / "pool.json"
    pool_path.write_text(
        json.dumps(
            {
                "activation": {
                    "explicit_cli_only": True,
                    "automatic_direction_flip": False,
                    "raw_expression_config_unchanged": True,
                },
                "source": {
                    "selected_features_file": str(selected_path),
                    "direction_review_file": str(review_path),
                    "factor_files": [str(factor_path)],
                },
                "manual_candidates": [
                    {
                        "source_column": "external_factor",
                        "output_column": "manual__external_factor__long",
                        "direction": "long",
                        "transform": "identity",
                        "human_approved": True,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    panel_path = tmp_path / "panel.parquet"
    pd.DataFrame(
        {
            "date": pd.to_datetime(["2024-01-01"]),
            "code": ["000001"],
            "stable_factor": [1.0],
        }
    ).to_parquet(panel_path, index=False)

    with pytest.raises(ValueError, match="duplicate|Duplicate"):
        materialize_manual_pool(
            panel_path,
            pool_path,
            tmp_path / "out.parquet",
            tmp_path / "features.json",
        )


def test_scoped_manual_pool_rejects_legacy_panel(tmp_path):
    pool_path = _write_inputs(tmp_path)
    payload = json.loads(pool_path.read_text(encoding="utf-8"))
    payload["scope"] = {
        "panel_scope": "full_market",
        "universe": "b2",
        "target": "y_neutral_rank20_b2",
    }
    pool_path.write_text(json.dumps(payload), encoding="utf-8")
    panel_path = tmp_path / "legacy.parquet"
    pd.DataFrame(
        {
            "date": pd.to_datetime(["2024-01-01"]),
            "code": ["000001"],
            "panel_scope": ["events"],
            "universe_b2": [True],
            "y_neutral_rank20_b2": [0.5],
            "stable_factor": [1.0],
            "alpha_qg_f001": [0.1],
            "alpha_qg_f005": [0.2],
            "alpha_qg_f006": [0.3],
        }
    ).to_parquet(panel_path, index=False)

    with pytest.raises(ValueError, match="non-full-market|full-market"):
        materialize_manual_pool(
            panel_path,
            pool_path,
            tmp_path / "out.parquet",
            tmp_path / "features.json",
        )
