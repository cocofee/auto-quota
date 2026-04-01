from src.installation_validator import InstallationValidator


def test_installation_validator_rejects_outlet_grounding_conflict():
    validator = InstallationValidator(lambda current, target: 0.8)
    result = validator.validate(
        {"outlet_grounding": "带接地"},
        {"outlet_grounding": "不带接地"},
    )

    assert result["hard_fail"] is True
    assert any("插座接地冲突" in detail for detail in result["details"])
