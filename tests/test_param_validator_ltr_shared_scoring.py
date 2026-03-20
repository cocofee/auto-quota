# -*- coding: utf-8 -*-

import pytest

from src.candidate_scoring import compute_candidate_rank_score
from src.param_validator import ParamValidator


def test_extract_ltr_features_uses_shared_candidate_rank_score_gap():
    validator = ParamValidator.__new__(ParamValidator)
    validator._ltr_model = None

    candidates = [
        {
            "quota_id": "A1",
            "name": "管道安装 DN100",
            "bm25_score": 0.8,
            "vector_score": 0.7,
            "hybrid_score": 0.75,
            "rerank_score": 0.62,
            "param_score": 0.86,
            "param_match": True,
            "param_tier": 2,
            "name_bonus": 0.18,
            "logic_score": 0.80,
            "feature_alignment_score": 0.82,
            "context_alignment_score": 0.76,
        },
        {
            "quota_id": "A2",
            "name": "管道安装 DN150",
            "bm25_score": 0.6,
            "vector_score": 0.5,
            "hybrid_score": 0.58,
            "rerank_score": 0.48,
            "param_score": 0.81,
            "param_match": True,
            "param_tier": 2,
            "name_bonus": 0.12,
            "logic_score": 0.74,
            "feature_alignment_score": 0.78,
            "context_alignment_score": 0.72,
        },
    ]

    features = validator._extract_ltr_features(candidates, "管道安装 DN100")

    top_gap = compute_candidate_rank_score(candidates[0]) - compute_candidate_rank_score(candidates[1])
    assert features[0][14] == pytest.approx(0.0)
    assert features[1][14] == pytest.approx(top_gap)


def test_extract_ltr_features_uses_best_shared_rank_candidate_not_input_order():
    validator = ParamValidator.__new__(ParamValidator)
    validator._ltr_model = None

    candidates = [
        {
            "quota_id": "B1",
            "name": "管道安装 DN150",
            "bm25_score": 0.85,
            "vector_score": 0.82,
            "hybrid_score": 0.84,
            "rerank_score": 0.90,
            "param_score": 0.76,
            "param_match": True,
            "param_tier": 2,
            "name_bonus": 0.08,
            "logic_score": 0.66,
            "feature_alignment_score": 0.70,
            "context_alignment_score": 0.68,
        },
        {
            "quota_id": "B2",
            "name": "管道安装 DN100",
            "bm25_score": 0.78,
            "vector_score": 0.74,
            "hybrid_score": 0.77,
            "rerank_score": 0.72,
            "param_score": 0.94,
            "param_match": True,
            "param_tier": 2,
            "name_bonus": 0.16,
            "logic_score": 0.92,
            "feature_alignment_score": 0.88,
            "context_alignment_score": 0.84,
        },
    ]

    features = validator._extract_ltr_features(candidates, "管道安装 DN100")

    best_score = max(compute_candidate_rank_score(candidate) for candidate in candidates)
    assert features[1][14] == pytest.approx(0.0)
    assert features[0][14] == pytest.approx(best_score - compute_candidate_rank_score(candidates[0]))
