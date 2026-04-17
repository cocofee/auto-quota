# -*- coding: utf-8 -*-

from src.match_core import calculate_confidence, infer_confidence_family_alignment


def test_calculate_confidence_keeps_non_family_param_mismatch_low():
    confidence = calculate_confidence(
        0.90,
        param_match=False,
        name_bonus=0.20,
        rerank_score=0.92,
        family_aligned=False,
        family_hard_conflict=False,
    )

    assert confidence <= 58


def test_calculate_confidence_allows_family_aligned_param_mismatch_into_yellow():
    confidence = calculate_confidence(
        0.90,
        param_match=False,
        name_bonus=0.20,
        rerank_score=0.92,
        family_aligned=True,
        family_hard_conflict=False,
    )

    assert confidence > 58
    assert confidence <= 82


def test_calculate_confidence_keeps_hard_conflict_param_mismatch_low():
    confidence = calculate_confidence(
        0.95,
        param_match=False,
        name_bonus=0.30,
        rerank_score=0.98,
        family_aligned=True,
        family_hard_conflict=True,
    )

    assert confidence == 55


def test_calculate_confidence_hard_conflict_without_supporting_signals_stays_at_base():
    confidence = calculate_confidence(
        0.40,
        param_match=False,
        name_bonus=0.0,
        rerank_score=0.0,
        family_aligned=True,
        family_hard_conflict=True,
    )

    assert confidence == 20


def test_infer_confidence_family_alignment_accepts_strong_feature_aligned_fallback():
    candidate = {
        "param_score": 0.61,
        "name_bonus": 0.31,
        "feature_alignment_score": 0.94,
        "feature_alignment_comparable_count": 3,
        "feature_alignment_exact_anchor_count": 2,
        "context_alignment_score": 0.90,
        "context_alignment_comparable_count": 2,
    }

    assert infer_confidence_family_alignment(candidate) is True


def test_infer_confidence_family_alignment_rejects_low_anchor_surface_match():
    candidate = {
        "param_score": 0.61,
        "name_bonus": 0.05,
        "feature_alignment_score": 0.94,
        "feature_alignment_comparable_count": 3,
        "feature_alignment_exact_anchor_count": 2,
        "context_alignment_score": 0.90,
        "context_alignment_comparable_count": 2,
    }

    assert infer_confidence_family_alignment(candidate) is False
