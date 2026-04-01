from src.param_validator import ParamValidator


def test_feature_alignment_treats_switch_outlet_entity_as_soft_match():
    validator = ParamValidator()
    result = validator._score_feature_alignment(
        bill_canonical_features={
            "entity": "\u63d2\u5ea7",
            "canonical_name": "\u63d2\u5ea7",
            "system": "\u7535\u6c14",
        },
        candidate_features={
            "entity": "\u5f00\u5173\u63d2\u5ea7",
            "canonical_name": "\u5f00\u5173\u63d2\u5ea7",
            "system": "\u7535\u6c14",
        },
    )

    assert result["hard_conflict"] is False
    assert "实体兼容" in result["detail"]
    assert result["score"] > 0.55
