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
