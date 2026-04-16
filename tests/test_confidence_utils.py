from src.confidence_utils import apply_confidence_penalty


def test_apply_confidence_penalty_uses_probabilistic_joint_decay():
    confidence = 80.0

    confidence = apply_confidence_penalty(confidence, -10)
    confidence = apply_confidence_penalty(confidence, -10)

    assert confidence == 64.8
