from src.final_validator import FinalValidator


def test_auto_correction_is_rejected_when_corrected_quota_still_fails_review(monkeypatch):
    result = {
        "bill_item": {"name": "塑料阀门", "description": "", "unit": "个"},
        "quotas": [{"quota_id": "Q1", "name": "原始错误定额", "unit": "个"}],
        "confidence": 80,
        "match_source": "search",
    }

    def fake_check(self, item, current_result):
        return {"type": "category_mismatch", "reason": "类别冲突"}

    def fake_recheck(self, item, quota_name, quota_id=""):
        if quota_id == "Q2":
            return {"type": "category_mismatch", "reason": "纠正后仍冲突"}
        return None

    monkeypatch.setattr(FinalValidator, "_check_review_error", fake_check)
    monkeypatch.setattr(FinalValidator, "_check_review_error_for_quota", fake_recheck)
    monkeypatch.setattr(
        "src.final_validator.correct_error",
        lambda item, error, dn, province=None: {
            "quota_id": "Q2",
            "quota_name": "仍然错误的纠正定额",
            "province": province,
        },
    )
    monkeypatch.setattr(
        "src.final_validator.search_by_id",
        lambda quota_id, province=None: (quota_id, "仍然错误的纠正定额", "个"),
    )

    FinalValidator(province="测试省份", auto_correct=True).validate_result(result)

    assert result["quotas"][0]["quota_id"] == "Q1"
    assert result["confidence"] == 80
    assert result["review_risk"] == "high"
    assert result["light_status"] == "red"
    assert result["final_validation"]["status"] == "manual_review"
    assert result["final_validation"]["issues"][0]["type"] == "category_mismatch"
