from src.experience_confidence import allows_direct_pass, describe_effective_confidence


def test_user_confirmed_single_confirmation_stays_below_default_retrieval_floor():
    record = {
        "source": "user_confirmed",
        "layer": "verified",
        "confidence": 95,
        "confirm_count": 1,
    }

    factors = describe_effective_confidence(record, now=0)

    assert factors["reviewer_weight"] == 0.76
    assert factors["effective_confidence"] < 60


def test_user_confirmed_three_confirmations_can_reach_direct_pass():
    record = {
        "source": "user_confirmed",
        "layer": "authority",
        "confidence": 98,
        "confirm_count": 3,
    }

    factors = describe_effective_confidence(record, now=0)

    assert factors["reviewer_weight"] == 0.99
    assert factors["effective_confidence"] >= 90
    assert allows_direct_pass(record, threshold=90, min_confirmations=2, now=0) is True
