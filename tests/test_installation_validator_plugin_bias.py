from src.installation_validator import InstallationValidator


def test_installation_validator_adds_plugin_preference_bias():
    validator = InstallationValidator(lambda current, target: 0.8)
    result = validator.validate(
        bill_params={},
        quota_params={},
        plugin_hints={
            "preferred_books": ["C4"],
            "preferred_quota_names": ["悬挂、嵌入式"],
            "avoided_quota_names": ["落地式"],
        },
        candidate_quota_id="C4-1-1",
        candidate_quota_name="成套配电箱安装 悬挂、嵌入式",
    )

    assert result["score_sum"] > 0.0
    assert any("plugin优先册" in detail for detail in result["details"])
    assert any("plugin优先名称命中" in detail for detail in result["details"])
