from unittest.mock import patch

from src.match_pipeline import _review_check_match_result


def test_review_check_prefers_primary_subject_as_review_name():
    result = {
        "quotas": [{"quota_id": "Q-1", "name": "光伏逆变器安装 功率≤1000kW"}]
    }
    item = {
        "name": "组串式逆变器 规格型号:150KW 安装点离地高度:屋面支架安装",
        "description": "组串式逆变器 规格型号:150KW 安装点离地高度:屋面支架安装",
        "canonical_query": {
            "primary_query_profile": {
                "primary_subject": "组串式逆变器"
            }
        },
    }

    captured = {}

    def _capture_category(review_item, quota_name, desc_lines):
        captured["name"] = review_item.get("name")
        captured["quota_name"] = quota_name
        captured["desc_lines"] = list(desc_lines or [])
        return None

    with patch("src.match_pipeline.check_category_mismatch", side_effect=_capture_category), \
         patch("src.match_pipeline.check_sleeve_mismatch", return_value=None), \
         patch("src.match_pipeline.check_material_mismatch", return_value=None), \
         patch("src.match_pipeline.check_connection_mismatch", return_value=None), \
         patch("src.match_pipeline.check_pipe_usage", return_value=None), \
         patch("src.match_pipeline.check_parameter_deviation", return_value=None), \
         patch("src.match_pipeline.check_electric_pair", return_value=None), \
         patch("src.match_pipeline.check_elevator_type", return_value=None), \
         patch("src.match_pipeline.check_elevator_floor", return_value=None):
        error = _review_check_match_result(result, item)

    assert error is None
    assert captured["name"] == "组串式逆变器"
