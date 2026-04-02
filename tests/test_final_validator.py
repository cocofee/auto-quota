from src.final_validator import FinalValidator
from src.price_validator import PriceValidator


def test_unit_conflict_marks_red_light_without_capping_score():
    result = {
        "bill_item": {"name": "给水管道", "description": "", "unit": "m"},
        "quotas": [{"quota_id": "Q1", "name": "给水阀门安装", "unit": "台"}],
        "confidence": 91,
        "match_source": "search",
    }

    FinalValidator(province="测试省份", auto_correct=False).validate_result(result)

    assert result["confidence"] == 91
    assert result["confidence_score"] == 91
    assert result["review_risk"] == "high"
    assert result["light_status"] == "red"
    assert result["final_validation"]["status"] == "vetoed"
    assert result["final_validation"]["issues"][0]["type"] == "unit_conflict"


def test_unit_conflict_pure_veto_does_not_recommend_next_candidate():
    result = {
        "bill_item": {"name": "给水管道", "description": "", "unit": "m"},
        "quotas": [{"quota_id": "Q1", "name": "给水阀门安装", "unit": "台"}],
        "candidate_snapshots": [
            {"quota_id": "Q1", "name": "给水阀门安装", "unit": "台"},
            {"quota_id": "Q2", "name": "镀锌钢管安装", "unit": "m"},
        ],
        "confidence": 88,
        "match_source": "search",
    }

    FinalValidator(province="测试省份", auto_correct=False).validate_result(result)

    assert result["quotas"][0]["quota_id"] == "Q1"
    assert result["quotas"][0]["unit"] == "台"
    assert result["review_risk"] == "high"
    assert result["light_status"] == "red"
    assert result["final_validation"]["status"] == "vetoed"
    assert result["final_validation"]["vetoed"] is True
    assert result["final_validation"]["advisory_applied"] is False
    assert result["final_validation"]["recommended_fallback_quota_id"] == ""
    assert result["final_review_correction"] == {}


def test_review_conflict_without_veto_stays_manual_review():
    result = {
        "bill_item": {"name": "镀锌钢管", "description": "", "unit": "m"},
        "quotas": [{"quota_id": "Q1", "name": "阀门安装", "unit": "m"}],
        "candidate_snapshots": [
            {"quota_id": "Q1", "name": "阀门安装", "unit": "m"},
            {"quota_id": "Q2", "name": "法兰阀门安装", "unit": "m"},
        ],
        "confidence": 80,
        "match_source": "search",
    }

    validator = FinalValidator(province="测试省份", auto_correct=False)
    validator._collect_review_errors_for_quota = lambda item, quota_name, quota_id="": [  # type: ignore[method-assign]
        {"type": "category_mismatch", "reason": "类别冲突"}
    ]
    validator.validate_result(result)

    assert result["quotas"][0]["quota_id"] == "Q1"
    assert result["confidence"] == 80
    assert result["review_risk"] == "high"
    assert result["light_status"] == "red"
    assert result["final_validation"]["status"] == "manual_review"
    assert result["final_validation"]["issues"][0]["type"] == "category_mismatch"


def test_anchor_conflict_marks_manual_review_and_red_light():
    result = {
        "bill_item": {
            "name": "桥架安装",
            "description": "",
            "unit": "m",
            "canonical_features": {
                "entity": "桥架",
                "system": "电气",
            },
        },
        "quotas": [{
            "quota_id": "Q1",
            "name": "塑料配管敷设",
            "unit": "m",
            "candidate_canonical_features": {
                "entity": "配管",
                "system": "给排水",
            },
        }],
        "confidence": 89,
        "match_source": "search",
    }

    validator = FinalValidator(province="测试省份", auto_correct=False)
    validator._collect_review_errors_for_quota = lambda item, quota_name, quota_id="": []  # type: ignore[method-assign]
    validator.validate_result(result)

    assert result["confidence"] == 89
    assert result["confidence_score"] == 89
    assert result["review_risk"] == "high"
    assert result["light_status"] == "red"
    assert result["final_validation"]["status"] == "vetoed"
    assert result["final_validation"]["issues"][0]["type"] == "anchor_conflict"


def test_anchor_conflict_skips_when_features_missing():
    result = {
        "bill_item": {"name": "桥架安装", "description": "", "unit": "m"},
        "quotas": [{"quota_id": "Q1", "name": "桥架安装", "unit": "m"}],
        "confidence": 82,
        "match_source": "search",
    }

    FinalValidator(province="测试省份", auto_correct=False).validate_result(result)

    assert result["confidence"] == 82
    assert result["confidence_score"] == 82
    assert result["review_risk"] == "medium"
    assert result["light_status"] == "yellow"
    assert result["final_validation"]["status"] == "ok"


def test_reasoning_decision_can_force_manual_review_without_capping_score():
    result = {
        "bill_item": {"name": "支架", "description": "", "unit": "m"},
        "quotas": [{"quota_id": "Q1", "name": "桥架支撑架安装", "unit": "m"}],
        "confidence": 88,
        "match_source": "search",
        "reasoning_decision": {
            "reason": "arbitrated_small_gap",
            "route": "installation_spec",
            "risk_level": "high",
            "require_final_review": True,
        },
    }

    FinalValidator(province="测试省份", auto_correct=False).validate_result(result)

    assert result["confidence"] == 88
    assert result["confidence_score"] == 88
    assert result["review_risk"] == "high"
    assert result["light_status"] == "red"
    assert result["final_validation"]["status"] == "manual_review"
    assert result["final_validation"]["issues"][0]["type"] == "ambiguity_review"


def test_final_validator_merges_reason_tags_and_final_reason():
    result = {
        "bill_item": {"name": "0005002", "description": "", "unit": "项"},
        "quotas": [{"quota_id": "Q1", "name": "给水阀门安装", "unit": "m"}],
        "confidence": 70,
        "match_source": "search",
        "reason_tags": ["dirty_input", "numeric_code"],
    }

    FinalValidator(province="测试省份", auto_correct=False).validate_result(result)

    assert "dirty_input" in result["reason_tags"]
    assert "manual_review" in result["reason_tags"]
    assert "light_red" in result["reason_tags"]
    assert result["final_reason"]


def test_price_mismatch_lowers_confidence_and_marks_manual_review():
    class _FakePriceValidator:
        def validate(self, bill_item, matched_quota):
            return {
                "status": "price_mismatch",
                "message": "单价偏离历史中位数60%",
                "confidence_penalty": -10,
                "median_price": 100.0,
                "actual_price": 160.0,
                "sample_count": 8,
                "deviation": 0.6,
            }

    result = {
        "bill_item": {"name": "桥架安装", "description": "", "unit": "m", "unit_price": 160.0},
        "quotas": [{"quota_id": "Q1", "name": "桥架安装", "unit": "m"}],
        "confidence": 80,
        "match_source": "search",
    }

    FinalValidator(
        province="测试省份",
        auto_correct=False,
        price_validator=_FakePriceValidator(),
    ).validate_result(result)

    assert result["confidence"] == 70
    assert result["confidence_score"] == 70
    assert result["review_risk"] == "high"
    assert result["light_status"] == "red"
    assert result["final_validation"]["status"] == "manual_review"
    assert result["final_validation"]["issues"][-1]["type"] == "price_mismatch"
    assert result["final_validation"]["price_validation"]["median_price"] == 100.0


def test_price_validator_skips_when_samples_are_insufficient():
    class _FakePriceDb:
        def get_composite_price_reference(self, **kwargs):
            return {
                "summary": {
                    "sample_count": 3,
                    "median_composite_unit_price": 100.0,
                }
            }

    validator = PriceValidator(_FakePriceDb())

    result = validator.validate(
        {"name": "桥架安装", "specialty": "安装", "unit_price": 90.0},
        {"quota_id": "Q1", "confidence": 75},
    )

    assert result["status"] == "insufficient_samples"
    assert result["sample_count"] == 3
