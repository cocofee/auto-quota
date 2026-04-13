from src.explicit_framework_family_pickers import _pick_explicit_plumbing_accessory_candidate
from src.match_pipeline import _prepare_item_for_matching
from src.param_validator import ParamValidator
from src.query_builder import build_quota_query
from src.text_parser import TextParser


parser = TextParser()


def test_parser_extracts_self_closing_flush_mode_from_flush_valve_text():
    result = parser.parse("壁挂式小便器 配套自闭式冲洗阀、冲水短管、存水弯")

    assert result["sanitary_subtype"] == "小便器"
    assert result["sanitary_mount_mode"] == "挂墙式"
    assert result["sanitary_flush_mode"] == "自闭阀"


def test_query_builder_keeps_self_closing_urinal_anchor():
    query = build_quota_query(
        parser,
        "小便器",
        "1.规格、类型：壁挂式小便器\n2.附件名称、数量：配套自闭式冲洗阀、装饰盖、冲水短管、橡胶密封圈、排水阀、存水弯等全部配件",
        specialty="C10",
    )

    assert "壁挂式小便器安装" in query
    assert "自闭阀" in query


def test_param_validator_rejects_shampoo_basin_for_wash_basin():
    validator = ParamValidator()
    results = validator.validate_candidates(
        query_text="洗脸盆 规格、类型：洗手盆 附件名称、数量：配套长颈水龙头、排水栓、存水弯等全部配件",
        candidates=[
            {
                "quota_id": "A",
                "name": "洗脸盆 洗发盆",
                "rerank_score": 0.95,
                "hybrid_score": 0.95,
            },
            {
                "quota_id": "B",
                "name": "洗脸盆 挂墙式 成套安装 冷水",
                "rerank_score": 0.10,
                "hybrid_score": 0.10,
            },
        ],
    )

    assert results[0]["quota_id"] == "B"
    assert next(item for item in results if item["quota_id"] == "A")["param_match"] is False


def test_param_validator_rejects_bidet_for_urinal():
    validator = ParamValidator()
    results = validator.validate_candidates(
        query_text="小便器 规格、类型：壁挂式小便器 附件名称、数量：配套自闭式冲洗阀、排水阀、存水弯等全部配件",
        candidates=[
            {
                "quota_id": "A",
                "name": "净身盆 壁挂式",
                "rerank_score": 0.95,
                "hybrid_score": 0.95,
            },
            {
                "quota_id": "B",
                "name": "壁挂式小便器安装 自闭阀",
                "rerank_score": 0.10,
                "hybrid_score": 0.10,
            },
        ],
    )

    assert results[0]["quota_id"] == "B"
    assert next(item for item in results if item["quota_id"] == "A")["param_match"] is False


def test_query_builder_adds_cleanout_installation_anchor():
    query = build_quota_query(
        parser,
        "塑料清扫口",
        "1.名称：塑料清扫口\n2.型号、规格：DN100",
        specialty="C10",
    )

    assert "清扫口安装" in query
    assert "塑料清扫口" in query


def test_plumbing_accessory_picker_prefers_cleanout_over_silencer():
    picked = _pick_explicit_plumbing_accessory_candidate(
        "塑料清扫口 DN100",
        [
            {"name": "塑料排水管消声器 公称直径（mm） 100", "param_score": 0.9, "rerank_score": 0.9},
            {"name": "清扫口安装 公称直径(mm以内) 100", "param_score": 0.4, "rerank_score": 0.3},
        ],
    )

    assert picked["name"] == "清扫口安装 公称直径(mm以内) 100"


def test_prepare_item_treats_composite_scaffold_as_measure_item():
    prepared = _prepare_item_for_matching(
        {
            "name": "综合脚手架",
            "description": "1.类型及结构形式:综合\n2.材料搬运、搭拆及堆放：综合\n3.包干使用",
            "unit": "项",
            "quantity": 1,
        },
        experience_db=None,
        rule_validator=None,
    )

    assert prepared.get("early_type") == "skip_measure"
    assert prepared["early_result"]["match_source"] == "skip_measure"
