from src.final_validator import FinalValidator


def test_unit_conflict_caps_confidence_and_marks_manual_review():
    result = {
        "bill_item": {"name": "给水管道", "description": "", "unit": "m"},
        "quotas": [{"quota_id": "Q1", "name": "给水阀门安装", "unit": "台"}],
        "confidence": 91,
        "match_source": "search",
    }

    FinalValidator(province="测试省份", auto_correct=False).validate_result(result)

    assert result["confidence"] == 68
    assert result["final_validation"]["status"] == "manual_review"
    assert result["final_validation"]["issues"][0]["type"] == "unit_conflict"


def test_review_conflict_can_auto_correct(monkeypatch):
    result = {
        "bill_item": {"name": "镀锌钢管", "description": "", "unit": "m"},
        "quotas": [{"quota_id": "Q1", "name": "阀门安装", "unit": "个"}],
        "confidence": 88,
        "match_source": "search",
    }

    monkeypatch.setattr(
        FinalValidator,
        "_check_review_error",
        lambda self, item, result: {"type": "category_mismatch", "reason": "类别冲突"},
    )
    monkeypatch.setattr(
        "src.final_validator.correct_error",
        lambda item, error, dn, province=None: {
            "quota_id": "Q2",
            "quota_name": "镀锌钢管安装",
            "province": province,
        },
    )
    monkeypatch.setattr(
        "src.final_validator.search_by_id",
        lambda quota_id, province=None: (quota_id, "镀锌钢管安装", "m"),
    )

    FinalValidator(province="测试省份", auto_correct=True).validate_result(result)

    assert result["quotas"][0]["quota_id"] == "Q2"
    assert result["quotas"][0]["unit"] == "m"
    assert result["final_validation"]["status"] == "corrected"
    assert result["final_review_correction"]["quota_id"] == "Q2"


def test_review_conflict_without_correction_stays_manual_review(monkeypatch):
    result = {
        "bill_item": {"name": "镀锌钢管", "description": "", "unit": "m"},
        "quotas": [{"quota_id": "Q1", "name": "阀门安装", "unit": "m"}],
        "confidence": 80,
        "match_source": "search",
    }

    monkeypatch.setattr(
        FinalValidator,
        "_check_review_error",
        lambda self, item, result: {"type": "category_mismatch", "reason": "类别冲突"},
    )
    monkeypatch.setattr("src.final_validator.correct_error", lambda *args, **kwargs: None)

    FinalValidator(province="测试省份", auto_correct=True).validate_result(result)

    assert result["confidence"] == 62
    assert result["final_validation"]["status"] == "manual_review"
    assert result["final_validation"]["issues"][0]["type"] == "category_mismatch"


def test_anchor_conflict_marks_manual_review_and_caps_confidence(monkeypatch):
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

    monkeypatch.setattr(FinalValidator, "_check_review_error", lambda self, item, result: None)

    FinalValidator(province="测试省份", auto_correct=False).validate_result(result)

    assert result["confidence"] == 64
    assert result["final_validation"]["status"] == "manual_review"
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
    assert result["final_validation"]["status"] == "ok"


def test_reasoning_decision_can_force_manual_review_without_other_conflicts():
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

    assert result["confidence"] == 78
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
    assert result["final_reason"]
