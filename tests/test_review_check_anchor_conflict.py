from unittest.mock import patch

from src.final_validator import FinalValidator
from src.match_pipeline import _review_check_match_result


def test_review_check_rejects_anchor_conflict_without_precomputed_quota_features():
    result = {
        "quotas": [{
            "quota_id": "Q1",
            "name": "塑料配管敷设",
            "unit": "m",
        }],
        "match_source": "experience_exact",
    }
    item = {
        "name": "桥架安装",
        "description": "",
        "canonical_features": {
            "entity": "桥架",
            "system": "电气",
        },
    }

    with patch("src.match_pipeline.check_category_mismatch", return_value=None), \
         patch("src.match_pipeline.check_sleeve_mismatch", return_value=None), \
         patch("src.match_pipeline.check_material_mismatch", return_value=None), \
         patch("src.match_pipeline.check_connection_mismatch", return_value=None), \
         patch("src.match_pipeline.check_pipe_usage", return_value=None), \
         patch("src.match_pipeline.check_parameter_deviation", return_value=None), \
         patch("src.match_pipeline.check_electric_pair", return_value=None), \
         patch("src.match_pipeline.check_elevator_type", return_value=None), \
         patch("src.match_pipeline.check_elevator_floor", return_value=None), \
         patch("src.match_pipeline.check_unit_conflict", return_value=None):
        error = _review_check_match_result(result, item)

    assert error is not None
    assert error["type"] == "anchor_conflict"
    assert error["quota_id"] == "Q1"


def test_final_validator_detects_anchor_conflict_without_precomputed_quota_features():
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
        }],
        "confidence": 89,
        "match_source": "experience_exact",
    }

    validator = FinalValidator(province="测试省份", auto_correct=False)
    validator._collect_review_errors_for_quota = lambda item, quota_name, quota_id="": []  # type: ignore[method-assign]
    validator.validate_result(result)

    assert result["final_validation"]["status"] == "vetoed"
    assert result["final_validation"]["issues"][0]["type"] == "anchor_conflict"


def test_review_check_allows_cable_through_conduit_anchor_pair():
    result = {
        "quotas": [{
            "quota_id": "Q1",
            "name": "配管 暗配",
            "unit": "m",
            "candidate_canonical_features": {
                "entity": "配管",
                "family": "conduit_raceway",
                "system": "电气",
                "laying_method": "穿管",
            },
        }],
        "match_source": "experience_exact",
    }
    item = {
        "name": "电力电缆",
        "description": "管内敷设",
        "canonical_features": {
            "entity": "电缆",
            "family": "cable_family",
            "system": "电气",
        },
    }

    with patch("src.match_pipeline.check_category_mismatch", return_value=None), \
         patch("src.match_pipeline.check_sleeve_mismatch", return_value=None), \
         patch("src.match_pipeline.check_material_mismatch", return_value=None), \
         patch("src.match_pipeline.check_connection_mismatch", return_value=None), \
         patch("src.match_pipeline.check_pipe_usage", return_value=None), \
         patch("src.match_pipeline.check_parameter_deviation", return_value=None), \
         patch("src.match_pipeline.check_electric_pair", return_value=None), \
         patch("src.match_pipeline.check_elevator_type", return_value=None), \
         patch("src.match_pipeline.check_elevator_floor", return_value=None), \
         patch("src.match_pipeline.check_unit_conflict", return_value=None):
        error = _review_check_match_result(result, item)

    assert error is None


def test_final_validator_allows_cable_through_conduit_anchor_pair():
    result = {
        "bill_item": {
            "name": "电力电缆",
            "description": "管内敷设",
            "unit": "m",
            "canonical_features": {
                "entity": "电缆",
                "family": "cable_family",
                "system": "电气",
            },
        },
        "quotas": [{
            "quota_id": "Q1",
            "name": "配管 暗配",
            "unit": "m",
            "candidate_canonical_features": {
                "entity": "配管",
                "family": "conduit_raceway",
                "system": "电气",
                "laying_method": "穿管",
            },
        }],
        "confidence": 89,
        "match_source": "experience_exact",
    }

    validator = FinalValidator(province="测试省份", auto_correct=False)
    validator._collect_review_errors_for_quota = lambda item, quota_name, quota_id="": []  # type: ignore[method-assign]
    validator.validate_result(result)

    assert result["final_validation"]["status"] == "ok"
    assert result["final_validation"]["issues"] == []
