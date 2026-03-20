import pytest

from src.installation_validator import InstallationValidator
from src.param_validator import ParamValidator


def _validator() -> InstallationValidator:
    return InstallationValidator(ParamValidator._tier_up_score)


def test_installation_validator_rejects_sanitary_mount_and_flush_conflict():
    result = _validator().validate(
        {"sanitary_mount_mode": "挂墙式", "sanitary_flush_mode": "感应"},
        {"sanitary_mount_mode": "立式", "sanitary_flush_mode": "脚踏"},
    )

    assert result["hard_fail"] is True
    assert any("sanitary_mount_mode冲突" in detail for detail in result["details"])


def test_installation_validator_rejects_lamp_type_conflict():
    result = _validator().validate(
        {"lamp_type": "灯带"},
        {"lamp_type": "吸顶灯"},
    )

    assert result["hard_fail"] is True
    assert any("lamp_type冲突" in detail for detail in result["details"])


def test_installation_validator_accepts_support_action_subset_match():
    result = _validator().validate(
        {"support_scope": "管道支架", "support_action": "制作"},
        {"support_scope": "管道支架", "support_action": "制作安装"},
    )

    assert result["hard_fail"] is False
    assert result["score_sum"] == pytest.approx(1.9, abs=0.01)
    assert any("support_action:" in detail for detail in result["details"])


def test_installation_validator_keeps_partial_support_action_for_incomplete_quota():
    result = _validator().validate(
        {"support_scope": "管道支架", "support_action": "制作安装"},
        {"support_scope": "管道支架", "support_action": "制作"},
    )

    assert result["hard_fail"] is False
    assert result["score_sum"] == pytest.approx(1.35, abs=0.01)


def test_installation_validator_accepts_compatible_install_method():
    result = _validator().validate(
        {"install_method": "明装"},
        {"install_method": "挂墙"},
    )

    assert result["hard_fail"] is False
    assert result["score_sum"] == pytest.approx(1.0, abs=0.01)
    assert any("安装方式:" in detail for detail in result["details"])


def test_installation_validator_rejects_conflicting_install_method():
    result = _validator().validate(
        {"install_method": "明装"},
        {"install_method": "嵌入"},
    )

    assert result["hard_fail"] is True
    assert any("安装方式冲突" in detail for detail in result["details"])


def test_installation_validator_accepts_bridge_bucket_upward_match():
    result = _validator().validate(
        {"bridge_wh_sum": 300},
        {"bridge_wh_sum": 400},
    )

    assert result["hard_fail"] is False
    assert result["score_sum"] > 0.8
    assert any("桥架宽高和300mm→400mm 向上取档" in detail for detail in result["details"])


def test_installation_validator_rejects_half_perimeter_downward_mismatch():
    result = _validator().validate(
        {"half_perimeter": 1500},
        {"half_perimeter": 1000},
    )

    assert result["hard_fail"] is True
    assert any("半周长1500mm>1000mm" in detail for detail in result["details"])


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


def test_installation_validator_rejects_explicit_cable_type_conflict():
    result = _validator().validate(
        {"cable_type": "控制电缆"},
        {"cable_type": "电力电缆"},
    )

    assert result["hard_fail"] is True
    assert any("线缆类型冲突" in detail for detail in result["details"])


def test_installation_validator_rejects_explicit_cable_head_type_conflict():
    result = _validator().validate(
        {"cable_head_type": "终端头"},
        {"cable_head_type": "中间头"},
    )

    assert result["hard_fail"] is True
    assert any("电缆头类型冲突" in detail for detail in result["details"])


def test_installation_validator_rejects_explicit_conduit_type_conflict():
    result = _validator().validate(
        {"conduit_type": "JDG"},
        {"conduit_type": "SC"},
    )

    assert result["hard_fail"] is True
    assert any("配管类型冲突" in detail for detail in result["details"])


def test_installation_validator_soft_penalizes_wire_type_conflict():
    result = _validator().validate(
        {"wire_type": "BPYJV"},
        {"wire_type": "YJV"},
    )

    assert result["hard_fail"] is False
    assert result["score_sum"] == pytest.approx(0.35, abs=0.01)
    assert any("线缆型号冲突" in detail for detail in result["details"])


def test_installation_validator_rejects_box_mount_mode_conflict():
    result = _validator().validate(
        {"box_mount_mode": "落地式"},
        {"box_mount_mode": "悬挂/嵌入式"},
    )

    assert result["hard_fail"] is True
    assert any("配电箱安装方式冲突" in detail for detail in result["details"])


def test_installation_validator_rejects_bridge_type_conflict():
    result = _validator().validate(
        {"bridge_type": "槽式"},
        {"bridge_type": "托盘式"},
    )

    assert result["hard_fail"] is True
    assert any("桥架类型冲突" in detail for detail in result["details"])


def test_installation_validator_rejects_valve_connection_family_conflict():
    result = _validator().validate(
        {"valve_connection_family": "法兰阀门"},
        {"valve_connection_family": "螺纹阀门"},
    )

    assert result["hard_fail"] is True
    assert any("阀门连接家族冲突" in detail for detail in result["details"])
