# -*- coding: utf-8 -*-

from unittest.mock import patch

from src.match_pipeline import _review_check_match_result


def test_review_check_rebuilds_anchor_features_from_primary_subject():
    result = {
        "quotas": [{
            "quota_id": "Q1",
            "name": "桥架安装",
            "unit": "m",
        }],
        "match_source": "experience_exact",
    }
    item = {
        "name": "塑料配管敷设",
        "description": "",
        "canonical_features": {
            "entity": "配管",
            "system": "电气",
        },
        "canonical_query": {
            "primary_query_profile": {
                "primary_subject": "桥架",
            }
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
