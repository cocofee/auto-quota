import pytest

from src.installation_validator import InstallationValidator
from src.param_validator import ParamValidator


def _validator() -> InstallationValidator:
    return InstallationValidator(ParamValidator._tier_up_score)


def test_installation_validator_exact_dn_match():
    result = _validator().validate({"dn": 100}, {"dn": 100})
    assert result["hard_fail"] is False
    assert result["check_count"] == 1
    assert result["score_sum"] == pytest.approx(1.0, abs=0.01)


def test_installation_validator_generic_quota_score():
    result = _validator().validate({"cable_section": 25}, {"_quota_name": "电缆敷设"})
    assert result["hard_fail"] is False
    assert result["check_count"] == 1
    assert result["score_sum"] == pytest.approx(0.64, abs=0.01)


def test_installation_validator_tier_up_for_cable_section():
    result = _validator().validate({"cable_section": 25}, {"cable_section": 35})
    assert result["hard_fail"] is False
    assert result["score_sum"] > 0.8


def test_installation_validator_hard_fail_when_bill_exceeds_quota():
    result = _validator().validate({"kw": 55}, {"kw": 37})
    assert result["hard_fail"] is True
    assert result["score_sum"] == pytest.approx(0.0, abs=0.01)
