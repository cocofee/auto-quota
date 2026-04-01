from src.match_pipeline import _pick_explicit_ventilation_family_candidate
from src.param_validator import ParamValidator
from src.query_builder import build_quota_query
from src.text_parser import TextParser


parser = TextParser()


def test_text_parser_extracts_sanitary_detail_modes():
    sink = parser.parse("单孔水槽 冷热水龙头")
    toilet = parser.parse("大便器 自闭阀")
    concealed = parser.parse("座便器 隐蔽水箱")

    assert sink["sanitary_subtype"] == "洗涤盆"
    assert sink["sanitary_nozzle_mode"] == "单嘴"
    assert sink["sanitary_water_mode"] == "冷热水"
    assert toilet["sanitary_subtype"] == "坐便器"
    assert toilet["sanitary_flush_mode"] == "自闭阀"
    assert concealed["sanitary_tank_mode"] == "隐藏水箱"


def test_query_builder_uses_sanitary_detail_modes():
    query = build_quota_query(
        parser,
        "单孔水槽",
        "单孔水槽 冷热水龙头 成品安装",
        specialty="C10",
    )

    assert "洗涤盆" in query
    assert "单嘴" in query
    assert "冷热水" in query


def test_param_validator_prefers_exact_sanitary_detail_modes():
    validator = ParamValidator()
    results = validator.validate_candidates(
        query_text="单孔水槽 冷热水龙头",
        candidates=[
            {
                "quota_id": "A",
                "name": "洗涤盆 双嘴 冷水",
                "rerank_score": 0.95,
                "hybrid_score": 0.95,
            },
            {
                "quota_id": "B",
                "name": "洗涤盆 单嘴 冷热水",
                "rerank_score": 0.10,
                "hybrid_score": 0.10,
            },
        ],
    )

    assert results[0]["quota_id"] == "B"
    assert next(item for item in results if item["quota_id"] == "A")["param_match"] is False


def test_ventilation_picker_prefers_toilet_ventilator_over_generic_fan():
    picked = _pick_explicit_ventilation_family_candidate(
        "离心式通风机 名称：PF1吊顶式通风器 型号：ST-9-5",
        [
            {
                "name": "离心式通风机 风机安装风量(m3/h) ≤1000",
                "param_score": 0.9,
                "rerank_score": 0.9,
            },
            {
                "name": "卫生间通风器安装",
                "param_score": 0.4,
                "rerank_score": 0.3,
            },
        ],
    )

    assert "卫生间通风器" in picked["name"]
