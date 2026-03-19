from src.param_validator import ParamValidator


def test_check_params_rejects_system_conflict_via_installation_validator():
    validator = ParamValidator()

    is_match, score, detail = validator._check_params(
        {},
        {},
        bill_canonical_features={"system": "电气", "entity": "电缆"},
        quota_canonical_features={"system": "给排水", "entity": "配管"},
    )

    assert is_match is False
    assert score == 0.0
    assert "系统冲突" in detail


def test_check_params_rejects_trait_conflict_via_installation_validator():
    validator = ParamValidator()

    is_match, score, detail = validator._check_params(
        {},
        {},
        bill_canonical_features={"entity": "消火栓", "traits": ["单栓"]},
        quota_canonical_features={"entity": "消火栓", "traits": ["双栓"]},
    )

    assert is_match is False
    assert score == 0.0
    assert "特征冲突" in detail
