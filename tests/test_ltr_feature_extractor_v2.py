from __future__ import annotations

import json
from pathlib import Path

import config
from src.ltr_feature_extractor import _compute_zscores, _load_genericity_stats, extract_group_features


def test_extract_group_features_parameter_distance_and_upward_nearest():
    item = {
        "name": "镀锌钢管",
        "description": "DN26 沟槽连接",
        "params": {"dn": 26},
        "canonical_features": {"family": "pipe", "entity": "steel_pipe", "material": "镀锌钢"},
    }
    candidates = [
        {
            "quota_id": "Q1",
            "name": "镀锌钢管安装 DN25",
            "param_score": 0.82,
            "logic_score": 0.7,
            "feature_alignment_score": 0.8,
            "hybrid_score": 0.7,
            "rerank_score": 0.7,
            "candidate_canonical_features": {"family": "pipe", "entity": "steel_pipe", "material": "镀锌钢"},
        },
        {
            "quota_id": "Q2",
            "name": "镀锌钢管安装 DN32",
            "param_score": 0.9,
            "logic_score": 0.8,
            "feature_alignment_score": 0.82,
            "hybrid_score": 0.72,
            "rerank_score": 0.72,
            "candidate_canonical_features": {"family": "pipe", "entity": "steel_pipe", "material": "镀锌钢"},
        },
    ]

    rows = extract_group_features(item, candidates, {})
    by_id = {row["quota_id"]: row for row in rows}

    assert by_id["Q1"]["dn_abs_gap"] == 1.0
    assert by_id["Q1"]["dn_direction"] == -1
    assert by_id["Q1"]["dn_is_upward_nearest"] == 0
    assert by_id["Q2"]["dn_abs_gap"] == 6.0
    assert by_id["Q2"]["dn_direction"] == 1
    assert by_id["Q2"]["dn_is_upward_nearest"] == 1


def test_extract_group_features_missing_encoding_and_text_overlap():
    item = {
        "name": "桥架",
        "description": "钢制桥架 安装",
        "params": {},
        "canonical_features": {"family": "bridge", "entity": "cable_tray", "material": "钢制"},
    }
    candidates = [
        {
            "quota_id": "B1",
            "name": "钢制桥架安装",
            "param_score": 0.5,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "hybrid_score": 0.8,
            "rerank_score": 0.8,
            "candidate_canonical_features": {"family": "bridge", "entity": "cable_tray", "material": "钢制"},
        }
    ]

    row = extract_group_features(item, candidates, {})[0]
    assert row["q_has_dn"] == 0
    assert row["d_has_dn"] == 0
    assert row["dn_missing_pattern"] == 3
    assert row["dn_abs_gap"] == -1.0
    assert row["core_term_overlap_count"] >= 1
    assert row["canonical_term_coverage"] > 0


def test_genericity_stats_loading_and_feature_fill():
    config.LTR_GENERICITY_STATS_PATH = Path("tests/fixtures/ltr_genericity_fixture.json")
    _load_genericity_stats.cache_clear()

    row = extract_group_features(
        {"name": "配电箱", "description": "", "params": {}, "canonical_features": {"family": "box"}},
        [{
            "quota_id": "QX",
            "name": "配电箱安装",
            "param_score": 0.5,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "hybrid_score": 0.5,
            "rerank_score": 0.5,
            "candidate_canonical_features": {"family": "box"},
        }],
        {},
    )[0]

    assert row["candidate_genericity_index"] == 1.2
    assert row["candidate_success_ratio"] == 0.2
    assert row["candidate_retrieval_popularity"] == 2.39
    assert row["candidate_specificity_score"] == 0.45


def test_compute_zscores_handles_flat_group():
    assert _compute_zscores([1.0, 1.0, 1.0]) == [0.0, 0.0, 0.0]
