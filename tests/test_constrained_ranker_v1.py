from __future__ import annotations

from src.constrained_ranker import apply_constrained_gated_ranker


def test_cgr_v1_filters_high_conf_wrong_book_candidate():
    item = {
        "name": "water pipe",
        "description": "indoor water pipe dn25 hot melt",
        "params": {"dn": 25},
        "canonical_features": {
            "family": "pipe_support",
            "entity": "pipe",
            "material": "composite_pipe",
            "install_method": "hot_melt",
            "system": "water",
        },
        "search_books": ["C10"],
        "hard_book_constraints": ["C10"],
        "query_route": {"route": "installation_spec"},
    }
    context = {
        "search_books": ["C10"],
        "hard_book_constraints": ["C10"],
        "classification": {
            "allow_cross_book_escape": False,
        },
    }
    candidates = [
        {
            "quota_id": "C8-9-1",
            "name": "industrial pipe dn25",
            "param_match": True,
            "param_score": 0.92,
            "logic_score": 0.90,
            "feature_alignment_score": 0.88,
            "context_alignment_score": 0.82,
            "rerank_score": 0.97,
            "hybrid_score": 0.96,
            "semantic_rerank_score": 0.98,
            "spec_rerank_score": 0.95,
            "family_gate_score": 1.2,
            "candidate_canonical_features": {
                "family": "pipe_support",
                "entity": "pipe",
                "material": "composite_pipe",
                "install_method": "hot_melt",
                "system": "water",
            },
            "_ltr_param": {
                "param_main_exact": 1,
                "param_main_rel_dist": 0.0,
                "param_main_direction": 0,
                "param_material_match": 1.0,
            },
        },
        {
            "quota_id": "C10-2-1",
            "name": "water pipe dn25",
            "param_match": True,
            "param_score": 0.94,
            "logic_score": 0.93,
            "feature_alignment_score": 0.90,
            "context_alignment_score": 0.88,
            "rerank_score": 0.70,
            "hybrid_score": 0.68,
            "semantic_rerank_score": 0.72,
            "spec_rerank_score": 0.75,
            "family_gate_score": 1.2,
            "candidate_canonical_features": {
                "family": "pipe_support",
                "entity": "pipe",
                "material": "composite_pipe",
                "install_method": "hot_melt",
                "system": "water",
            },
            "_ltr_param": {
                "param_main_exact": 1,
                "param_main_rel_dist": 0.0,
                "param_main_direction": 0,
                "param_material_match": 1.0,
            },
        },
    ]

    ranked, meta = apply_constrained_gated_ranker(item, candidates, context)

    assert ranked[0]["quota_id"] == "C10-2-1"
    assert ranked[0]["cgr_feasible"] is True
    assert ranked[1]["cgr_feasible"] is False
    assert ranked[1]["cgr_high_conf_wrong_book"] is True
    assert meta["empty_feasible_set"] is False


def test_cgr_v1_prefers_pipe_run_over_pipe_fitting_with_semantic_trap():
    item = {
        "name": "composite pipe",
        "description": "indoor water pipe dn25 hot melt",
        "params": {"dn": 25},
        "canonical_features": {
            "family": "pipe_support",
            "entity": "pipe",
            "material": "composite_pipe",
            "install_method": "hot_melt",
            "system": "water",
        },
        "search_books": ["C10"],
        "query_route": {"route": "installation_spec"},
    }
    candidates = [
        {
            "quota_id": "C10-3-1",
            "name": "pipe run dn25 hot melt",
            "param_match": True,
            "param_score": 0.96,
            "logic_score": 0.94,
            "feature_alignment_score": 0.92,
            "context_alignment_score": 0.90,
            "rerank_score": 0.62,
            "hybrid_score": 0.60,
            "semantic_rerank_score": 0.63,
            "spec_rerank_score": 0.68,
            "family_gate_score": 1.1,
            "candidate_canonical_features": {
                "family": "pipe_support",
                "entity": "pipe",
                "material": "composite_pipe",
                "install_method": "hot_melt",
                "system": "water",
            },
            "_ltr_param": {
                "param_main_exact": 1,
                "param_main_rel_dist": 0.0,
                "param_main_direction": 0,
                "param_material_match": 1.0,
            },
        },
        {
            "quota_id": "C10-3-2",
            "name": "pipe fitting dn25 hot melt",
            "param_match": False,
            "param_score": 0.58,
            "logic_score": 0.55,
            "feature_alignment_score": 0.74,
            "context_alignment_score": 0.70,
            "rerank_score": 0.96,
            "hybrid_score": 0.95,
            "semantic_rerank_score": 0.97,
            "spec_rerank_score": 0.94,
            "family_gate_score": -0.8,
            "candidate_canonical_features": {
                "family": "pipe_fitting",
                "entity": "fitting",
                "material": "composite_pipe",
                "install_method": "hot_melt",
                "system": "water",
            },
            "_ltr_param": {
                "param_main_exact": 0,
                "param_main_rel_dist": 0.4,
                "param_main_direction": 1,
                "param_material_match": 1.0,
            },
        },
    ]

    ranked, meta = apply_constrained_gated_ranker(item, candidates, {})

    assert ranked[0]["quota_id"] == "C10-3-1"
    assert meta["gate"] < 0.5
    assert ranked[1]["cgr_soft_conflict_penalty"] > ranked[0]["cgr_soft_conflict_penalty"]
    assert ranked[0]["cgr_str_score"] > ranked[1]["cgr_str_score"]
