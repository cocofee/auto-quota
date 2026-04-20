import math

import config

from src.experience_confidence import allows_direct_pass, describe_effective_confidence


def test_single_confirmation_stays_recallable_but_cannot_direct_pass():
    record = {
        "source": "openclaw_approved",
        "layer": "authority",
        "confidence": 95,
        "confirm_count": 1,
    }

    factors = describe_effective_confidence(record, now=0)

    assert factors["reviewer_weight"] == 1.0
    assert factors["confirm_count_weight"] == 0.72
    assert factors["effective_confidence"] == 68
    assert factors["effective_confidence"] >= 60
    assert allows_direct_pass(record, threshold=90, min_confirmations=2, now=0) is False


def test_user_confirmed_many_confirmations_can_reach_direct_pass():
    record = {
        "source": "user_confirmed",
        "layer": "authority",
        "confidence": 98,
        "confirm_count": 20,
    }

    factors = describe_effective_confidence(record, now=0)

    assert factors["reviewer_weight"] == 0.99
    assert factors["confirm_count_weight"] == round(1.0 - math.exp(-6.0), 6)
    assert factors["effective_confidence"] >= 90
    assert allows_direct_pass(record, threshold=90, min_confirmations=2, now=0) is True


def test_project_import_single_confirmation_stays_recallable_above_default_floor():
    record = {
        "source": "project_import",
        "layer": "verified",
        "confidence": 90,
        "confirm_count": 1,
    }

    factors = describe_effective_confidence(record, now=0)

    assert factors["reviewer_weight"] == 0.93
    assert factors["confirm_count_weight"] == 0.72
    assert factors["effective_confidence"] == 60
    assert factors["effective_confidence"] >= 60
    assert factors["effective_confidence"] < config.EXPERIENCE_DIRECT_THRESHOLD
