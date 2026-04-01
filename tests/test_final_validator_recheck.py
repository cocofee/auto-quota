from src.final_validator import FinalValidator


def test_veto_fallback_skips_candidates_that_still_fail_review():
    result = {
        "bill_item": {"name": "塑料阀门", "description": "", "unit": "个"},
        "quotas": [{"quota_id": "Q1", "name": "原始错误定额", "unit": "个"}],
        "candidate_snapshots": [
            {"quota_id": "Q1", "name": "原始错误定额", "unit": "个"},
            {"quota_id": "Q2", "name": "仍然错误的候选", "unit": "个"},
            {"quota_id": "Q3", "name": "塑料阀门安装", "unit": "个"},
        ],
        "confidence": 80,
        "match_source": "search",
    }

    validator = FinalValidator(province="测试省份", auto_correct=False)

    def fake_collect(item, quota_name, quota_id=""):
        if quota_id in {"Q1", "Q2"}:
            return [{"type": "category_mismatch", "reason": "类别冲突"}]
        return []

    validator._collect_review_errors_for_quota = fake_collect  # type: ignore[method-assign]

    validator.validate_result(result)

    assert result["quotas"][0]["quota_id"] == "Q1"
    assert result["confidence"] == 80
    assert result["review_risk"] == "high"
    assert result["light_status"] == "red"
    assert result["final_validation"]["status"] == "manual_review"
    assert result["final_validation"]["issues"][0]["type"] == "category_mismatch"
