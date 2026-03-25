from __future__ import annotations

import json
import shutil
from pathlib import Path

from src.constrained_ranker import apply_constrained_gated_ranker


def test_constrained_ranker_prefers_structured_exact_match_for_spec_query(monkeypatch):
    monkeypatch.setattr("config.CONSTRAINED_GATED_RANKER_ENABLED", True)
    item = {
        "name": "镀锌钢管",
        "description": "DN25 明装",
        "params": {"dn": 25},
        "canonical_features": {
            "family": "pipe_support",
            "entity": "pipe",
            "material": "镀锌钢",
            "install_method": "明装",
            "system": "给排水",
        },
        "query_route": {"route": "installation_spec"},
        "search_books": ["C10"],
    }
    candidates = [
        {
            "quota_id": "C10-1-1",
            "name": "镀锌钢管 DN25 明装",
            "param_match": True,
            "param_score": 0.95,
            "logic_score": 0.96,
            "feature_alignment_score": 0.92,
            "context_alignment_score": 0.90,
            "rerank_score": 0.62,
            "hybrid_score": 0.08,
            "semantic_rerank_score": 0.60,
            "spec_rerank_score": 0.64,
            "family_gate_score": 1.2,
            "candidate_canonical_features": {
                "family": "pipe_support",
                "entity": "pipe",
                "material": "镀锌钢",
                "install_method": "明装",
                "system": "给排水",
            },
            "_ltr_param": {
                "param_main_exact": 1,
                "param_main_rel_dist": 0.0,
                "param_main_direction": 0,
                "param_material_match": 1.0,
            },
        },
        {
            "quota_id": "C10-1-2",
            "name": "镀锌钢管 DN50 明装",
            "param_match": False,
            "param_score": 0.55,
            "logic_score": 0.50,
            "feature_alignment_score": 0.90,
            "context_alignment_score": 0.88,
            "rerank_score": 0.93,
            "hybrid_score": 0.09,
            "semantic_rerank_score": 0.94,
            "spec_rerank_score": 0.90,
            "family_gate_score": 1.1,
            "candidate_canonical_features": {
                "family": "pipe_support",
                "entity": "pipe",
                "material": "镀锌钢",
                "install_method": "明装",
                "system": "给排水",
            },
            "_ltr_param": {
                "param_main_exact": 0,
                "param_main_rel_dist": 0.5,
                "param_main_direction": 1,
                "param_material_match": 1.0,
            },
        },
    ]

    ranked, meta = apply_constrained_gated_ranker(item, candidates, {})

    assert ranked[0]["quota_id"] == "C10-1-1"
    assert meta["gate"] < 0.5
    assert ranked[0]["cgr_probability"] > ranked[1]["cgr_probability"]


def test_constrained_ranker_prefers_semantic_candidate_for_fuzzy_query(monkeypatch):
    monkeypatch.setattr("config.CONSTRAINED_GATED_RANKER_ENABLED", True)
    item = {
        "name": "风口",
        "description": "铝合金单层百叶风口",
        "canonical_features": {
            "family": "air_terminal",
            "entity": "风口",
            "material": "铝合金",
            "system": "通风空调",
        },
        "query_route": {"route": "semantic_description"},
        "search_books": ["C7"],
    }
    candidates = [
        {
            "quota_id": "C7-1-1",
            "name": "百叶风口安装",
            "param_match": True,
            "param_score": 0.72,
            "logic_score": 0.70,
            "feature_alignment_score": 0.78,
            "context_alignment_score": 0.85,
            "rerank_score": 0.96,
            "hybrid_score": 0.07,
            "semantic_rerank_score": 0.97,
            "spec_rerank_score": 0.95,
            "family_gate_score": 0.8,
            "candidate_canonical_features": {
                "family": "air_terminal",
                "entity": "风口",
                "material": "铝合金",
                "system": "通风空调",
            },
            "_ltr_param": {
                "param_main_exact": 0,
                "param_main_rel_dist": 1.0,
                "param_main_direction": 0,
                "param_material_match": 1.0,
            },
        },
        {
            "quota_id": "C7-1-2",
            "name": "风阀安装",
            "param_match": True,
            "param_score": 0.75,
            "logic_score": 0.76,
            "feature_alignment_score": 0.76,
            "context_alignment_score": 0.85,
            "rerank_score": 0.55,
            "hybrid_score": 0.05,
            "semantic_rerank_score": 0.52,
            "spec_rerank_score": 0.50,
            "family_gate_score": 0.1,
            "candidate_canonical_features": {
                "family": "air_valve",
                "entity": "风阀",
                "material": "铝合金",
                "system": "通风空调",
            },
            "_ltr_param": {
                "param_main_exact": 0,
                "param_main_rel_dist": 1.0,
                "param_main_direction": 0,
                "param_material_match": 1.0,
            },
        },
    ]

    ranked, meta = apply_constrained_gated_ranker(item, candidates, {})

    assert ranked[0]["quota_id"] == "C7-1-1"
    assert meta["gate"] > 0.5


def test_constrained_ranker_uses_trained_model_when_available(monkeypatch):
    temp_root = Path("output/_tmp_cgr_model_ranker_test")
    shutil.rmtree(temp_root, ignore_errors=True)
    temp_root.mkdir(parents=True, exist_ok=True)
    try:
        model_path = temp_root / "cgr_model.json"
        model_path.write_text(
            json.dumps({
                "temperature": 0.85,
                "accept_threshold": 0.5,
                "min_top1_prob": 0.3,
                "semantic_expert": {
                    "feature_names": ["rerank_score"],
                    "weights": [5.0],
                    "bias": 0.0,
                    "means": [0.0],
                    "scales": [1.0],
                },
                "structural_expert": {
                    "feature_names": ["param_score"],
                    "weights": [0.0],
                    "bias": 0.0,
                    "means": [0.0],
                    "scales": [1.0],
                },
                "gate": {
                    "feature_names": [],
                    "weights": [],
                    "bias": 8.0,
                    "means": [],
                    "scales": [],
                },
                "accept_head": {
                    "feature_names": ["p1"],
                    "weights": [1.0],
                    "bias": 0.0,
                    "means": [0.0],
                    "scales": [1.0],
                },
            }, ensure_ascii=False),
            encoding="utf-8",
        )
        monkeypatch.setattr("config.CGR_MODEL_PATH", model_path)

        item = {
            "name": "镀锌钢管",
            "description": "DN25 明装",
            "params": {"dn": 25},
            "canonical_features": {
                "family": "pipe_support",
                "entity": "pipe",
                "material": "镀锌钢",
                "install_method": "明装",
                "system": "给排水",
            },
            "query_route": {"route": "installation_spec"},
            "search_books": ["C10"],
        }
        candidates = [
            {
                "quota_id": "C10-1-1",
                "name": "镀锌钢管 DN25 明装",
                "param_match": True,
                "param_score": 0.95,
                "logic_score": 0.96,
                "feature_alignment_score": 0.92,
                "context_alignment_score": 0.90,
                "rerank_score": 0.62,
                "hybrid_score": 0.08,
                "semantic_rerank_score": 0.60,
                "spec_rerank_score": 0.64,
                "family_gate_score": 1.2,
                "candidate_canonical_features": {
                    "family": "pipe_support",
                    "entity": "pipe",
                    "material": "镀锌钢",
                    "install_method": "明装",
                    "system": "给排水",
                },
                "_ltr_param": {
                    "param_main_exact": 1,
                    "param_main_rel_dist": 0.0,
                    "param_main_direction": 0,
                    "param_material_match": 1.0,
                },
            },
            {
                "quota_id": "C10-1-2",
                "name": "镀锌钢管 DN50 明装",
                "param_match": False,
                "param_score": 0.55,
                "logic_score": 0.50,
                "feature_alignment_score": 0.90,
                "context_alignment_score": 0.88,
                "rerank_score": 0.93,
                "hybrid_score": 0.09,
                "semantic_rerank_score": 0.94,
                "spec_rerank_score": 0.90,
                "family_gate_score": 1.1,
                "candidate_canonical_features": {
                    "family": "pipe_support",
                    "entity": "pipe",
                    "material": "镀锌钢",
                    "install_method": "明装",
                    "system": "给排水",
                },
                "_ltr_param": {
                    "param_main_exact": 0,
                    "param_main_rel_dist": 0.5,
                    "param_main_direction": 1,
                    "param_material_match": 1.0,
                },
            },
        ]

        ranked, meta = apply_constrained_gated_ranker(item, candidates, {})

        assert meta["gate"] > 0.99
        assert ranked[0]["quota_id"] == "C10-1-2"
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)
