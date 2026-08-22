import json

import config

from src.confidence_calibrator import (
    DEFAULT_CONFIDENCE_SPEC,
    calibrate_confidence_probability,
    get_confidence_calibration_spec,
)
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
    non_family = calculate_confidence(
        0.90,
        param_match=False,
        name_bonus=0.20,
        rerank_score=0.92,
        family_aligned=False,
        family_hard_conflict=False,
    )
    family_aligned = calculate_confidence(
        0.90,
        param_match=False,
        name_bonus=0.20,
        rerank_score=0.92,
        family_aligned=True,
        family_hard_conflict=False,
    )

    assert family_aligned > non_family
    assert 58 < family_aligned <= 82


def test_calculate_confidence_hard_conflict_feature_reduces_param_mismatch_confidence():
    family_aligned = calculate_confidence(
        0.95,
        param_match=False,
        name_bonus=0.30,
        rerank_score=0.98,
        family_aligned=True,
        family_hard_conflict=False,
    )
    hard_conflict = calculate_confidence(
        0.95,
        param_match=False,
        name_bonus=0.30,
        rerank_score=0.98,
        family_aligned=True,
        family_hard_conflict=True,
    )

    assert hard_conflict < family_aligned
    assert hard_conflict < 75


def test_calculate_confidence_hard_conflict_without_supporting_signals_stays_near_floor():
    confidence = calculate_confidence(
        0.40,
        param_match=False,
        name_bonus=0.0,
        rerank_score=0.0,
        family_aligned=True,
        family_hard_conflict=True,
    )

    assert confidence <= 15


def test_calculate_confidence_does_not_assume_perfect_gap_when_gap_is_omitted():
    no_gap = calculate_confidence(
        0.95,
        param_match=True,
        name_bonus=0.20,
        rerank_score=0.98,
    )
    explicit_gap = calculate_confidence(
        0.95,
        param_match=True,
        name_bonus=0.20,
        rerank_score=0.98,
        score_gap=0.30,
    )

    assert explicit_gap > no_gap


def test_isotonic_calibration_remains_monotonic():
    spec = get_confidence_calibration_spec()
    calibrated = [
        calibrate_confidence_probability(raw_probability, spec=spec)
        for raw_probability in (0.10, 0.30, 0.50, 0.70, 0.90)
    ]

    assert calibrated == sorted(calibrated)


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


def test_get_confidence_calibration_spec_loads_external_artifact(tmp_path, monkeypatch):
    calibration_path = tmp_path / "confidence_calibration.json"
    calibration_path.write_text(
        json.dumps(
            {
                "model": {
                    "intercept": -2.0,
                    "weights": {
                        "param_match": 1.5,
                        "param_score": 1.2,
                    },
                },
                "isotonic": [
                    {"raw": 0.0, "calibrated": 0.1},
                    {"raw": 0.5, "calibrated": 0.6},
                    {"raw": 1.0, "calibrated": 0.9},
                ],
                "ambiguous_short_ceiling": 68,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(config, "CONFIDENCE_CALIBRATOR_ENABLED", True)
    monkeypatch.setattr(config, "CONFIDENCE_CALIBRATOR_PATH", calibration_path)
    get_confidence_calibration_spec.cache_clear()

    spec = get_confidence_calibration_spec()

    assert spec.intercept == -2.0
    assert spec.weights["param_match"] == 1.5
    assert spec.weights["param_score"] == 1.2
    assert spec.isotonic_points == ((0.0, 0.1), (0.5, 0.6), (1.0, 0.9))
    assert spec.ambiguous_short_ceiling == 68
    assert spec.max_score == 90

    get_confidence_calibration_spec.cache_clear()


def test_get_confidence_calibration_spec_falls_back_to_default_when_artifact_missing(monkeypatch):
    monkeypatch.setattr(config, "CONFIDENCE_CALIBRATOR_ENABLED", True)
    monkeypatch.setattr(config, "CONFIDENCE_CALIBRATOR_PATH", config.DATA_DIR / "missing-confidence.json")
    get_confidence_calibration_spec.cache_clear()

    spec = get_confidence_calibration_spec()

    assert spec == DEFAULT_CONFIDENCE_SPEC

    get_confidence_calibration_spec.cache_clear()


def test_get_confidence_calibration_spec_honors_disabled_switch(monkeypatch, tmp_path):
    calibration_path = tmp_path / "confidence.json"
    calibration_path.write_text(
        json.dumps({
            "model": {"intercept": 5.0, "weights": {}},
            "isotonic": [
                {"raw": 0.0, "calibrated": 0.1},
                {"raw": 1.0, "calibrated": 0.9},
            ],
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(config, "CONFIDENCE_CALIBRATOR_ENABLED", False)
    monkeypatch.setattr(config, "CONFIDENCE_CALIBRATOR_PATH", calibration_path)
    get_confidence_calibration_spec.cache_clear()

    assert get_confidence_calibration_spec() == DEFAULT_CONFIDENCE_SPEC

    get_confidence_calibration_spec.cache_clear()
