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


def test_installation_validator_tier_up_for_cable_cores():
    result = _validator().validate({"cable_cores": 5}, {"cable_cores": 6})
    assert result["hard_fail"] is False
    assert result["score_sum"] > 0.8


def test_installation_validator_exact_match_for_port_count():
    result = _validator().validate({"port_count": 24}, {"port_count": 24})
    assert result["hard_fail"] is False
    assert result["score_sum"] == pytest.approx(1.0, abs=0.01)


def test_installation_validator_rejects_system_conflict_from_canonical_features():
    result = _validator().validate(
        {"dn": 100},
        {"dn": 100},
        bill_canonical_features={"system": "电气"},
        quota_canonical_features={"system": "给排水"},
    )

    assert result["hard_fail"] is True
    assert any("系统冲突" in detail for detail in result["details"])


def test_installation_validator_rejects_entity_conflict_from_canonical_features():
    result = _validator().validate(
        {"dn": 100},
        {"dn": 100},
        bill_canonical_features={"entity": "电缆"},
        quota_canonical_features={"entity": "配管"},
    )

    assert result["hard_fail"] is True
    assert any("实体冲突" in detail for detail in result["details"])


def test_installation_validator_rejects_trait_conflict_from_canonical_features():
    result = _validator().validate(
        {},
        {},
        bill_canonical_features={"traits": ["单栓"]},
        quota_canonical_features={"traits": ["双栓"]},
    )

    assert result["hard_fail"] is True
    assert any("特征冲突" in detail for detail in result["details"])


def test_installation_validator_rejects_specific_component_entity_conflict():
    result = _validator().validate(
        {},
        {},
        bill_canonical_features={"entity": "风阀"},
        quota_canonical_features={"entity": "风管"},
    )

    assert result["hard_fail"] is True
    assert any("实体冲突" in detail for detail in result["details"])


def test_installation_validator_rejects_extended_trait_conflict():
    result = _validator().validate(
        {},
        {},
        bill_canonical_features={"traits": ["刚性"]},
        quota_canonical_features={"traits": ["柔性"]},
    )

    assert result["hard_fail"] is True
    assert any("特征冲突" in detail for detail in result["details"])


def test_installation_validator_rejects_valve_subtype_conflict():
    result = _validator().validate(
        {"dn": 100},
        {"dn": 100},
        bill_canonical_features={"entity": "闸阀"},
        quota_canonical_features={"entity": "止回阀"},
    )

    assert result["hard_fail"] is True
    assert any("实体冲突" in detail for detail in result["details"])


def test_installation_validator_rejects_phase_or_hole_trait_conflict():
    result = _validator().validate(
        {},
        {},
        bill_canonical_features={"entity": "插座", "traits": ["单相", "五孔"]},
        quota_canonical_features={"entity": "插座", "traits": ["三相", "三孔"]},
    )

    assert result["hard_fail"] is True
    assert any("特征冲突" in detail for detail in result["details"])
